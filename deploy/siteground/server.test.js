"use strict";
// Unit tests for the SiteGround wrapper's request-handling helpers: `npm test`.
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseEnv, clientIp, upstreamHeaders, downstreamHeaders, normaliseHost } = require("./server.js");

function pairs(raw) {
  const out = [];
  for (let i = 0; i < raw.length; i += 2) out.push([raw[i].toLowerCase(), raw[i + 1]]);
  return out;
}

test("settings file: comments, quotes, export and trailing notes", () => {
  const env = parseEnv(
    [
      "# comment",
      "APP_ENV=production",
      'MAIL_FROM="GG Afrikan Eleganz <ggafrikan@gmail.com>"',
      "SMTP_PORT=587          # STARTTLS",
      "export KV_BACKEND=database",
      "EMPTY=",
      "EMPTY_WITH_NOTE=                 # optional amount added to each Shippo rate",
      "HASH_IN_VALUE=abc#def",
      "bad key=x",
      "=novalue",
    ].join("\n")
  );
  assert.deepEqual(env, {
    APP_ENV: "production",
    MAIL_FROM: "GG Afrikan Eleganz <ggafrikan@gmail.com>",
    SMTP_PORT: "587",
    KV_BACKEND: "database",
    EMPTY: "",
    EMPTY_WITH_NOTE: "",
    HASH_IN_VALUE: "abc#def",
  });
});

test("client IP is the last entry the host's web server added", () => {
  assert.equal(clientIp({ "x-forwarded-for": "1.2.3.4, 203.0.113.9" }, "x-forwarded-for"), "203.0.113.9");
  assert.equal(clientIp({ "x-real-ip": "2001:db8::1" }, "X-Real-IP"), "2001:db8::1");
  assert.equal(clientIp({ "x-forwarded-for": "203.0.113.9, not-an-ip" }, "x-forwarded-for"), "");
  assert.equal(clientIp({}, "x-forwarded-for"), "");
});

test("client-sent forwarding headers are replaced, never trusted", () => {
  const raw = [
    "Host", "staging2.example.com",
    "X-Forwarded-For", "6.6.6.6",
    "X-Forwarded-Host", "evil.example",
    "X-Forwarded-Proto", "http",
    "Forwarded", "for=6.6.6.6;host=evil.example",
    "X-Real-IP", "6.6.6.6",
    "X-Original-URL", "/admin/",
    "Cookie", "a=1",
  ];
  const out = pairs(upstreamHeaders(raw, { ip: "203.0.113.9", host: "staging2.example.com", proto: "https" }));
  assert.deepEqual(out, [
    ["host", "staging2.example.com"],
    ["cookie", "a=1"],
    ["x-forwarded-for", "203.0.113.9"],
    ["x-forwarded-proto", "https"],
    ["x-forwarded-host", "staging2.example.com"],
  ]);
});

test("hop-by-hop headers, including ones named in Connection, are dropped", () => {
  const raw = [
    "Connection", "keep-alive, X-Secret-Hop",
    "Keep-Alive", "timeout=5",
    "Transfer-Encoding", "chunked",
    "Upgrade", "websocket",
    "X-Secret-Hop", "1",
    "Stripe-Signature", "t=1,v1=abc",
    "Content-Type", "application/json",
  ];
  const names = pairs(upstreamHeaders(raw, { ip: "", host: "h", proto: "https" })).map(([n]) => n);
  assert.deepEqual(names, ["stripe-signature", "content-type", "x-forwarded-proto", "x-forwarded-host"]);
});

test("repeated request headers and every Set-Cookie survive the trip", () => {
  const up = pairs(upstreamHeaders(["Accept", "a", "Accept", "b"], { ip: "", host: "h", proto: "https" }));
  assert.deepEqual(up.slice(0, 2), [["accept", "a"], ["accept", "b"]]);
  const down = pairs(
    downstreamHeaders([
      "Set-Cookie", "session=1; Secure; HttpOnly",
      "Set-Cookie", "cart=2; Secure",
      "Transfer-Encoding", "chunked",
      "Content-Type", "text/html",
    ])
  );
  assert.deepEqual(down, [
    ["set-cookie", "session=1; Secure; HttpOnly"],
    ["set-cookie", "cart=2; Secure"],
    ["content-type", "text/html"],
  ]);
});

test("host names are compared without port or case", () => {
  assert.equal(normaliseHost(" Staging2.GGAfrikanEleganz.com:443 "), "staging2.ggafrikaneleganz.com");
  assert.equal(normaliseHost(undefined), "");
});
