"use strict";
// Public entry point on SiteGround's managed Node.js hosting.
//
// SiteGround starts this process for each request after an idle period and stops it when idle;
// it will not let visitors reach a Python process any other way. This file starts the unchanged
// Flask app under Gunicorn on a private Unix socket and streams every request to it. It holds no
// business logic, parses no request bodies and keeps no sessions: Flask makes every security and
// business decision. No npm packages are used.
//
// Private state lives outside the deployed code, in ~/.ggflask (mode 700):
//   env           the app's settings (KEY=VALUE, same format as .env; never in git)
//   venv/         Python packages, rebuilt when requirements.lock or Python changes
//   run/app.sock  the Gunicorn socket (this account only)
//   logs/         wrapper.log and app.log, rotated at 5 MB
//   media/        uploaded and imported photos (MEDIA_ROOT)
//   jobs/         scheduled-job heartbeats (JOBS_HEARTBEAT_DIR)
//   current       link to the deployed code, used by cron jobs (deploy/siteground/run-job.sh)

const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn, spawnSync } = require("node:child_process");

const APP_DIR = path.resolve(__dirname, "..", "..");

const LIMITS = {
  startupWaitMs: 25_000, // a request waits this long for Python before getting a 503
  upstreamTimeoutMs: 35_000, // the slowest Flask response allowed (as Apache had)
  maxWaiting: 200, // requests held while Python starts
  headersTimeoutMs: 20_000,
  requestTimeoutMs: 120_000, // whole request including a large photo upload
  keepAliveMs: 5_000,
  maxHeaders: 100,
  logBytes: 5 * 1024 * 1024,
  logKeep: 3,
  crashWindowMs: 120_000,
  maxCrashes: 5, // crashes inside the window before giving up (stays 503, logged)
};

// Never forwarded: they describe a single connection, not the request.
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);
// Set only by this file from the trusted connection; any client-sent copy is dropped so it
// cannot spoof the visitor's IP, scheme or host to Flask.
const FORWARDING = new Set([
  "forwarded",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-port",
  "x-forwarded-prefix",
  "x-forwarded-proto",
  "x-forwarded-server",
  "x-forwarded-ssl",
  "x-original-url",
  "x-rewrite-url",
  "x-real-ip",
]);

// ---------- pure helpers (unit-tested in server.test.js) ----------

/** Parse a .env-style file: KEY=VALUE, # comments, optional quotes, trailing " # notes". */
function parseEnv(text) {
  const out = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim().replace(/^export\s+/, "");
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    let value = line.slice(eq + 1).trim();
    const quote = value[0];
    if ((quote === '"' || quote === "'") && value.indexOf(quote, 1) > 0) {
      value = value.slice(1, value.indexOf(quote, 1));
    } else if (value.startsWith("#")) {
      value = ""; // `KEY=   # note` has no value, as python-dotenv and the shell read it
    } else {
      const hash = value.search(/\s#/);
      if (hash >= 0) value = value.slice(0, hash).trim();
    }
    out[key] = value;
  }
  return out;
}

/** The visitor's IP from the header the host's web server sets (its last entry). */
function clientIp(headers, headerName) {
  const value = headers[headerName.toLowerCase()];
  const joined = Array.isArray(value) ? value.join(",") : value || "";
  const parts = joined
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  const last = parts[parts.length - 1] || "";
  return net.isIP(last) ? last : "";
}

/** Request headers for Flask, as a raw [name, value, ...] list so repeated headers survive. */
function upstreamHeaders(rawHeaders, { ip, host, proto }) {
  const connectionTokens = new Set();
  for (let i = 0; i < rawHeaders.length; i += 2) {
    if (rawHeaders[i].toLowerCase() === "connection") {
      for (const t of rawHeaders[i + 1].split(",")) connectionTokens.add(t.trim().toLowerCase());
    }
  }
  const out = [];
  for (let i = 0; i < rawHeaders.length; i += 2) {
    const name = rawHeaders[i].toLowerCase();
    if (HOP_BY_HOP.has(name) || FORWARDING.has(name) || connectionTokens.has(name)) continue;
    out.push(rawHeaders[i], rawHeaders[i + 1]);
  }
  if (ip) out.push("X-Forwarded-For", ip);
  out.push("X-Forwarded-Proto", proto, "X-Forwarded-Host", host);
  return out;
}

/** Response headers back to the visitor, keeping every Set-Cookie as its own header. */
function downstreamHeaders(rawHeaders) {
  const out = [];
  for (let i = 0; i < rawHeaders.length; i += 2) {
    if (!HOP_BY_HOP.has(rawHeaders[i].toLowerCase())) out.push(rawHeaders[i], rawHeaders[i + 1]);
  }
  return out;
}

function normaliseHost(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/:\d+$/, "");
}

function hashFiles(files, extra = "") {
  const h = crypto.createHash("sha256").update(extra);
  for (const f of files) {
    h.update(path.basename(f)).update("\0");
    h.update(fs.readFileSync(f)).update("\0");
  }
  return h.digest("hex");
}

// ---------- logging ----------

class RotatingLog {
  constructor(file) {
    this.file = file;
    this.size = fs.existsSync(file) ? fs.statSync(file).size : 0;
  }

  write(text) {
    const data = text.endsWith("\n") ? text : `${text}\n`;
    if (this.size + data.length > LIMITS.logBytes) this.rotate();
    fs.appendFileSync(this.file, data, { mode: 0o600 });
    this.size += Buffer.byteLength(data);
  }

  rotate() {
    for (let i = LIMITS.logKeep - 1; i >= 1; i -= 1) {
      const from = `${this.file}.${i}`;
      if (fs.existsSync(from)) fs.renameSync(from, `${this.file}.${i + 1}`);
    }
    if (fs.existsSync(this.file)) fs.renameSync(this.file, `${this.file}.1`);
    this.size = 0;
  }
}

// ---------- the wrapper ----------

function createWrapper(options = {}) {
  const home = options.home || process.env.GG_HOME || path.join(os.homedir(), ".ggflask");
  const P = {
    home,
    env: path.join(home, "env"),
    venv: path.join(home, "venv"),
    run: path.join(home, "run"),
    socket: path.join(home, "run", "app.sock"),
    logs: path.join(home, "logs"),
    media: path.join(home, "media"),
    jobs: path.join(home, "jobs"),
    state: path.join(home, "state.json"),
    current: path.join(home, "current"),
  };
  for (const dir of [P.home, P.run, P.logs, P.media, P.jobs]) {
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  }
  fs.chmodSync(P.home, 0o700);
  fs.chmodSync(P.run, 0o700);

  const wlog = new RotatingLog(path.join(P.logs, "wrapper.log"));
  const alog = new RotatingLog(path.join(P.logs, "app.log"));
  const log = (msg) => wlog.write(`${new Date().toISOString()} ${msg}`);

  const fileEnv = fs.existsSync(P.env) ? parseEnv(fs.readFileSync(P.env, "utf8")) : {};
  if (!fs.existsSync(P.env)) log(`no settings file at ${P.env}; the app will refuse to start`);
  const setting = (name, fallback) => fileEnv[name] ?? process.env[name] ?? fallback;

  const allowedHosts = new Set(
    setting("GG_ALLOWED_HOSTS", "")
      .split(",")
      .map(normaliseHost)
      .filter(Boolean)
  );
  const ipHeader = setting("GG_CLIENT_IP_HEADER", "x-forwarded-for");
  const forceHttps = setting("GG_FORCE_HTTPS", "1") !== "0";
  const maxBody = Number(setting("GG_MAX_BODY_MB", "20")) * 1024 * 1024;
  let debugForwarding = Number(setting("GG_DEBUG_FORWARDING", "0")) || 0;

  // Python sees only its own settings plus a minimal base, not Node's environment.
  const pythonEnv = {
    PATH: process.env.PATH || "/usr/local/bin:/usr/bin:/bin",
    HOME: os.homedir(),
    LANG: "C.UTF-8",
    PYTHONUNBUFFERED: "1",
    MEDIA_ROOT: P.media,
    JOBS_HEARTBEAT_DIR: P.jobs,
    GG_PLATFORM: "siteground",
    ...fileEnv,
    GG_SOCKET: P.socket,
  };

  let phase = "starting"; // starting | ready | failed | stopping
  let child = null;
  let crashes = [];
  let waiters = [];

  function setPhase(next) {
    phase = next;
    if (next !== "starting") {
      for (const w of waiters) w(next === "ready");
      waiters = [];
    }
  }

  function waitReady() {
    if (phase === "ready") return Promise.resolve(true);
    if (phase !== "starting" || waiters.length >= LIMITS.maxWaiting) return Promise.resolve(false);
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        waiters = waiters.filter((w) => w !== done);
        resolve(false);
      }, LIMITS.startupWaitMs);
      const done = (ok) => {
        clearTimeout(timer);
        resolve(ok);
      };
      waiters.push(done);
    });
  }

  function run(cmd, args, label) {
    return new Promise((resolve) => {
      log(`${label}: starting`);
      const p = spawn(cmd, args, { cwd: APP_DIR, env: pythonEnv, stdio: ["ignore", "pipe", "pipe"] });
      p.stdout.on("data", (d) => alog.write(`[${label}] ${d.toString().trimEnd()}`));
      p.stderr.on("data", (d) => alog.write(`[${label}] ${d.toString().trimEnd()}`));
      p.on("error", (e) => {
        log(`${label}: could not start (${e.code})`);
        resolve(false);
      });
      p.on("exit", (code) => {
        log(`${label}: exit ${code}`);
        resolve(code === 0);
      });
    });
  }

  function findPython() {
    for (const cmd of ["python3.14", "python3.13", "python3"]) {
      const r = spawnSync(cmd, ["--version"], { encoding: "utf8" });
      if (r.status === 0) return { cmd, version: (r.stdout || r.stderr).trim() };
    }
    return null;
  }

  function readState() {
    try {
      return JSON.parse(fs.readFileSync(P.state, "utf8"));
    } catch {
      return {};
    }
  }

  function writeState(state) {
    fs.writeFileSync(P.state, JSON.stringify(state, null, 2), { mode: 0o600 });
  }

  async function prepare() {
    // cron jobs find the deployed code through this link
    try {
      fs.rmSync(P.current, { force: true });
      fs.symlinkSync(APP_DIR, P.current);
    } catch (e) {
      log(`could not link ${P.current} (${e.code})`);
    }
    const python = findPython();
    if (!python) {
      log("no python3 found");
      return false;
    }
    const state = readState();
    const lock = path.join(APP_DIR, "requirements.lock");
    const reqHash = hashFiles([lock], python.version);
    const bin = path.join(P.venv, "bin");
    if (state.requirements !== reqHash || !fs.existsSync(path.join(bin, "gunicorn"))) {
      fs.rmSync(P.venv, { recursive: true, force: true });
      if (!(await run(python.cmd, ["-m", "venv", P.venv], "venv"))) return false;
      // --only-binary: the host has no compilers; every package must come prebuilt.
      const pip = path.join(bin, "pip");
      const ok = await run(
        pip,
        ["install", "--disable-pip-version-check", "--only-binary=:all:", "-r", lock],
        "pip"
      );
      if (!ok) return false;
      state.requirements = reqHash;
      writeState(state);
    }
    const versions = path.join(APP_DIR, "migrations", "versions");
    const migrationFiles = fs
      .readdirSync(versions)
      .filter((f) => f.endsWith(".py"))
      .sort()
      .map((f) => path.join(versions, f));
    const migHash = hashFiles(migrationFiles);
    if (state.migrations !== migHash) {
      if (!(await run(path.join(bin, "flask"), ["--app", "wsgi", "db", "upgrade"], "migrate"))) {
        return false; // never serve with a database the code does not match
      }
      state.migrations = migHash;
      writeState(state);
    }
    return true;
  }

  function socketAccepts() {
    return new Promise((resolve) => {
      const s = net.connect(P.socket);
      s.once("connect", () => {
        s.destroy();
        resolve(true);
      });
      s.once("error", () => resolve(false));
    });
  }

  async function startPython() {
    fs.rmSync(P.socket, { force: true });
    const gunicorn = path.join(P.venv, "bin", "gunicorn");
    const conf = path.join(APP_DIR, "deploy", "siteground", "gunicorn.conf.py");
    child = spawn(gunicorn, ["--config", conf, "wsgi:app"], {
      cwd: APP_DIR,
      env: { ...pythonEnv, GG_WEB_ONLY: "1" }, // skip CLI-only imports: faster wake-up
      stdio: ["ignore", "pipe", "pipe"],
    });
    const started = child;
    child.stdout.on("data", (d) => alog.write(d.toString().trimEnd()));
    child.stderr.on("data", (d) => alog.write(d.toString().trimEnd()));
    child.on("error", (e) => log(`gunicorn could not start (${e.code})`));
    child.on("exit", (code, signal) => onExit(started, code, signal));
    const deadline = Date.now() + LIMITS.startupWaitMs + 30_000;
    while (Date.now() < deadline && child === started && started.exitCode === null) {
      if (await socketAccepts()) {
        log(`ready (gunicorn pid ${started.pid})`);
        setPhase("ready");
        return;
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    if (child === started && phase === "starting") {
      log("gunicorn did not become ready in time");
      started.kill("SIGTERM");
    }
  }

  function onExit(proc, code, signal) {
    if (proc !== child || phase === "stopping") return;
    log(`gunicorn exited (code ${code}, signal ${signal})`);
    const now = Date.now();
    crashes = crashes.filter((t) => now - t < LIMITS.crashWindowMs).concat(now);
    if (crashes.length > LIMITS.maxCrashes) {
      log(`gave up after ${crashes.length} crashes in ${LIMITS.crashWindowMs / 1000}s; see app.log`);
      setPhase("failed");
      return;
    }
    phase = "starting";
    const delay = Math.min(1000 * 2 ** (crashes.length - 1), 30_000);
    setTimeout(() => startPython().catch((e) => log(`restart failed: ${e.message}`)), delay);
  }

  function plain(res, status, text, extra = {}) {
    if (res.headersSent) return res.destroy();
    res.writeHead(status, {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
      ...extra,
    });
    res.end(`${text}\n`);
  }

  function forward(req, res, host, ip) {
    const headers = upstreamHeaders(req.rawHeaders, { ip, host, proto: forceHttps ? "https" : "http" });
    const upstream = http.request({
      socketPath: P.socket,
      method: req.method,
      path: req.url,
      headers,
      setHost: false,
    });
    let answered = false; // set once this file has replied itself (413), so later events stay quiet
    upstream.setTimeout(LIMITS.upstreamTimeoutMs, () => upstream.destroy(new Error("timeout")));
    upstream.on("response", (up) => {
      if (answered) return void up.resume();
      res.writeHead(up.statusCode, up.statusMessage, downstreamHeaders(up.rawHeaders));
      up.pipe(res);
    });
    upstream.on("error", (e) => {
      if (answered) return;
      if (e.message === "timeout") return plain(res, 504, "The page took too long. Please try again.");
      log(`upstream error ${e.code || e.message}`);
      plain(res, 502, "Something went wrong. Please try again in a moment.");
    });
    res.on("close", () => {
      if (!res.writableFinished) upstream.destroy(); // visitor left: stop the work
    });
    let seen = 0;
    req.on("data", (chunk) => {
      seen += chunk.length;
      if (seen > maxBody && !answered) {
        answered = true;
        upstream.destroy();
        plain(res, 413, "That upload is too large.", { Connection: "close" });
        req.destroy();
      }
    });
    req.pipe(upstream); // streamed untouched: webhook signatures see the exact bytes
  }

  async function handle(req, res) {
    const host = normaliseHost(req.headers.host);
    if (allowedHosts.size && !allowedHosts.has(host)) return plain(res, 421, "Unknown host.");
    const declared = Number(req.headers["content-length"] || 0);
    if (declared > maxBody) return plain(res, 413, "That upload is too large.", { Connection: "close" });
    const scheme = String(req.headers["x-forwarded-proto"] || "")
      .split(",")[0]
      .trim()
      .toLowerCase();
    if (forceHttps && scheme === "http") {
      return plain(res, 308, "Redirecting to HTTPS.", { Location: `https://${host}${req.url}` });
    }
    const ip = clientIp(req.headers, ipHeader);
    if (debugForwarding > 0) {
      debugForwarding -= 1; // staging aid: which forwarding headers does the host send?
      const names = ["x-forwarded-for", "x-real-ip", "x-forwarded-proto", "forwarded", "x-forwarded-host"];
      log(`forwarding ${names.map((n) => `${n}=${JSON.stringify(req.headers[n] ?? null)}`).join(" ")} -> ip=${ip}`);
    }
    if (!(await waitReady())) {
      // "failed" means setup or Python could not start: see ~/.ggflask/logs/app.log
      const text =
        phase === "failed"
          ? "The shop is temporarily unavailable. Please try again later."
          : "The shop is starting up. Please refresh in a few seconds.";
      return plain(res, 503, text, { "Retry-After": phase === "failed" ? "60" : "5" });
    }
    forward(req, res, host, ip);
  }

  const server = http.createServer({ maxHeadersCount: LIMITS.maxHeaders }, (req, res) => {
    handle(req, res).catch((e) => {
      log(`handler error ${e.message}`);
      plain(res, 500, "Something went wrong.");
    });
  });
  server.headersTimeout = LIMITS.headersTimeoutMs;
  server.requestTimeout = LIMITS.requestTimeoutMs;
  server.keepAliveTimeout = LIMITS.keepAliveMs;

  async function boot() {
    log(`boot node ${process.version}, app ${APP_DIR}`);
    if (!(await prepare())) {
      log("setup failed; serving 503 until the next deploy/restart (see app.log)");
      setPhase("failed");
      return;
    }
    await startPython();
  }

  function stop() {
    if (phase === "stopping") return;
    phase = "stopping";
    log("stopping");
    server.close();
    if (child && child.exitCode === null) child.kill("SIGTERM");
    setTimeout(() => process.exit(0), 10_000).unref();
    if (child && child.exitCode === null) child.once("exit", () => process.exit(0));
    else process.exit(0);
  }

  return { server, boot, stop, paths: P, phase: () => phase };
}

module.exports = {
  parseEnv,
  clientIp,
  upstreamHeaders,
  downstreamHeaders,
  normaliseHost,
  createWrapper,
  LIMITS,
};

if (require.main === module) {
  const wrapper = createWrapper();
  const port = Number(process.env.PORT || 3000);
  wrapper.server.listen(port, () => {
    wrapper.boot().catch((e) => process.stderr.write(`boot failed: ${e.message}\n`));
  });
  process.on("SIGTERM", wrapper.stop);
  process.on("SIGINT", wrapper.stop);
}
