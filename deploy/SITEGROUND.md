# Running the shop on SiteGround (staging2 first, then live)

SiteGround cannot run Python as a web server, but its **Node.js projects** (GrowBig and up) can.
`deploy/siteground/server.js` is a small Node file with no packages: it starts the unchanged Flask
app on a private socket and passes every request to it. Flask still makes every decision.
Everything private lives in `~/.ggflask` on the Node project's account, never in the code:

| Path | What |
|---|---|
| `~/.ggflask/env` | the app's settings (you write it; same format as `.env`) |
| `~/.ggflask/venv/` | Python packages (built automatically on first start) |
| `~/.ggflask/media/` | product and blog photos |
| `~/.ggflask/logs/` | `wrapper.log`/`app.log` (5 MB, three rotated copies); `python.log` (rolled to `.1` only when a fresh Gunicorn starts); `jobs.log` (append-only—monitor its size) |
| `~/.ggflask/jobs/` | scheduled-job heartbeats (`jobs check` reads them) |
| `~/.ggflask/current` | link to the deployed code, used by cron and the helper below |

Measured: the first start after a deploy takes ~1–2 minutes (installs packages, updates the
database). SiteGround stops the app after ~20–45 s without visitors; waking it takes about a
second, after which pages take ~0.1–0.25 s.

## 1. Create the Node.js project (Client Area)

1. **Websites → Node.js → New project**, name `gg-staging`.
2. Source: **GitHub**, repository `TheeGreenGenie/gg_flask`, branch `main` (authorise SiteGround's
   GitHub app for this one repository only).
3. Settings: Node **22**; framework preset **none/Other**; package manager **npm**; build command
   **empty** (if required: `npm ci`); start command **`npm start`**; output directory **empty**.
4. Keep the temporary `*.sg-host.com` domain for now. Delete the old `gg-probe` test project.

## 2. Create the database (the new project's Site Tools)

**Site → PostgreSQL** (or the MySQL page's PostgreSQL tab): create a database and a user with all
privileges. Note the database name, user, password and host. If the password contains `@ : / ? #`
or `%`, generate a new one without them.

## 3. Write the settings file (SSH into the Node project)

Use the SSH details from the **Node project's** Site Tools (not the WordPress site's). Generate two
random values first:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # STUDIO_PATH (staging-only)
```

Then create the file. Paste the lines, then press Enter and Ctrl-D:

```bash
mkdir -p ~/.ggflask && chmod 700 ~/.ggflask
cat > ~/.ggflask/env
```

Staging contents (copy the provider values from your PC's `.env`; never paste them in chat):

```ini
APP_ENV=production
SECRET_KEY=PASTE_RANDOM_48
DATABASE_URL=postgresql+psycopg://DB_USER:DB_PASSWORD@DB_HOST:5432/DB_NAME
KV_BACKEND=database
TRUSTED_PROXY_HOPS=1
GG_ALLOWED_HOSTS=staging2.ggafrikaneleganz.com,TEMP-NAME.sg-host.com
GG_DEBUG_FORWARDING=20
STUDIO_PATH=PASTE_RANDOM_24
STUDIO_APPROVAL_BACKEND=ntfy
STUDIO_NTFY_TOPIC=your existing topic
SUPPORTED_COUNTRIES=US,CA,MX
FLAT_SHIPPING_CENTS=1000
LOCAL_SHIPPING_CENTS=799
SHIPPING_WEIGHT_RATES=3:999,8:1699,15:2499,25:3499
TAX_BACKEND=none
MAIL_BACKEND=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=ggafrikaneleganz@gmail.com
SMTP_PASSWORD=the app password
MAIL_FROM="GG Afrikan Eleganz <ggafrikaneleganz@gmail.com>"
ORDER_NOTIFICATION_EMAIL=ggafrikaneleganz@gmail.com
MONITORING_SUMMARY_EMAIL=solomonshasanmi@gmail.com
HERE_API_KEY=...
MAILCHIMP_API_KEY=...
MAILCHIMP_SERVER_PREFIX=...
MAILCHIMP_LIST_ID=...
```

Finish with `chmod 600 ~/.ggflask/env`. Stripe/PayPal sandbox values come last, as planned.
`TAX_BACKEND=none` is for staging only; live needs Stripe Tax.

## 4. Deploy and watch it start

Click **Deploy** in the Client Area. While it installs, the site says "starting up". Follow along:

```bash
tail -f ~/.ggflask/logs/wrapper.log      # Ctrl-C to stop; "ready (gunicorn pid ...)" = up
tail -n 50 ~/.ggflask/logs/app.log       # package install, database update, errors
```

## 5. Point staging2 at it

1. In the **WordPress site's** Site Tools → Domain → Subdomains, **delete** `staging2` (the test
   folder made earlier).
2. In the Client Area, attach `staging2.ggafrikaneleganz.com` to the `gg-staging` project.
3. In the Node project's Site Tools → Security → SSL Manager, issue Let's Encrypt for it, and turn
   on **HTTPS Enforce** if offered.

## 6. Accounts

```bash
F="sh $HOME/.ggflask/current/deploy/siteground/flask.sh"
$F admin create --username owner --email ggafrikaneleganz@gmail.com --role owner   # scan the QR/secret
$F studio sync-users             # preview
$F studio sync-users --apply     # creates the Studio accounts and links tgg to owner
$F mail test YOUR@EMAIL          # real email delivery check
```

## 7. Scheduled jobs (Node project's Site Tools → Devs → Cron Jobs)

Command for each (replace the last words per row):
`sh /home/customer/.ggflask/current/deploy/siteground/run-job.sh studio sync-sales`

| Schedule | Job |
|---|---|
| every minute (or SiteGround's shortest) | `studio sync-sales`, `studio sync-blog` |
| every 15 minutes | `orders release-expired-holds`, `orders sync-refunds` |
| daily 03:10 / 03:15 / 03:20 | `wishlist-prune`, `accounts purge-unverified`, `reviews purge-removed` |
| daily 07:30 | `studio daily-summary` |
| Saturdays 03:30 (`30 3 * * 6`) | `backup run`: database backup to `~/.ggflask/backups`, keeps the newest 3 (three weeks) |

**Confirm the high-frequency rows with SiteGround before launch.** SiteGround's current
[fair-use guidance](https://www.siteground.com/kb/fair-use-siteground-hosting/) recommends at least
30 minutes between scheduled script executions. This project's one-minute sale/blog timing and
15-minute order timing predate that guidance. If support requires a 30-minute cadence, do not just
slow the jobs: their heartbeat thresholds and the accepted delay for scheduled sales, posts, stock
release and pending refunds must be changed together. The daily summary, daily cleanup and weekly
backup rows already meet the interval guidance.

The 07:30 summary goes to `MONITORING_SUMMARY_EMAIL` and reports failed privileged sign-ins,
lockouts, job/backup health and operating counts. It falls back to `ORDER_NOTIFICATION_EMAIL` only
for deployments that have not added the dedicated setting yet. This is an application email, so
SMTP delivery must be healthy; keep UptimeRobot and the external security probe off-host so a host
outage cannot silence every alert.

If `/home/customer` is not your home path, use the output of `echo $HOME`. Check them with
`$F jobs check` the next day.

SiteGround creates and edits these under **Site Tools > Devs > Cron Jobs**. Its cron form can send
the command's output to an email address, which is useful during setup but noisy for frequent jobs;
the application wrapper also keeps private output and heartbeats under `~/.ggflask/`.

**Backups:** run `$F backup run` once by hand first. It needs `pg_dump` on the server (SiteGround
includes it with PostgreSQL); the message says plainly if it is missing. `$F backup list` shows
what is kept.

**Off-server copy by email:** add `BACKUP_PASSPHRASE=` (a long phrase only you know, 16+
characters; write it down somewhere safe, it cannot be recovered) to `~/.ggflask/env`. Each
backup is then encrypted with it and emailed to the original shop mailbox (ggafrikan@gmail.com, on purpose; set
`BACKUP_EMAIL=` for another). Without the passphrase nothing is emailed. To open an emailed
`.enc` file on your computer: `flask --app wsgi backup decrypt FILE.enc`.
For the launch restore drill, create an empty spare PostgreSQL database in Site Tools, then run
`$F backup test-restore SPARE_DB_NAME`. It refuses the live database name, restores only into the
spare database and compares key row counts; do not use a raw restore command against production.

## 8. Pre-launch checks (paste results to Claude; no secrets)

First run the automated security and hosting check; it prints PASS / FAIL / WARN per item and
never prints a secret value:

```bash
sh ~/.ggflask/current/deploy/siteground/launch-check.sh
```

Then the manual checks:

1. **Visitor IP:** open the site from your phone, then
   `grep forwarding ~/.ggflask/logs/wrapper.log | tail -3`. The `ip=` value must be your phone's
   public IP. If it is empty or SiteGround's, tell Claude before going further (rate limits and the
   optional admin IP list depend on it). Then set `GG_DEBUG_FORWARDING=0`.
2. **No shared caching of pages:** in PowerShell,
   `curl.exe -sI https://ggafrikaneleganz.com/ | findstr /i "cache-control x-proxy-cache"`
   must show `private, no-store` and no `HIT`.
3. **Nothing private is public:** these must all be 404:
   `/.ggflask/env`, `/.env`, `/.git/config`, `/.nodeapp/`, `/requirements.lock`, `/package.json`,
   `/deploy/siteground/server.js`.
4. **Studio approval by ntfy:** sign in to the Studio as a staff account on the computer; tap the
   phone notification; sign in to `/admin` as owner on the phone; compare the match code; approve;
   the computer continues.
5. **tgg shortcut:** while signed in to `/admin` as owner in the same browser, a Studio sign-in as
   tgg goes straight in.
6. **Newsletter:** a signed-out signup gets the Mailchimp confirmation email.
7. **Address autocomplete** at checkout suggests addresses.
8. **Wake-up time:** leave the site untouched for 2 minutes, then
   `curl.exe -s -o NUL -w "%{time_starttransfer}s" https://ggafrikaneleganz.com/`, and
   again right after.

## 9. Final cutover checklist

1. Deploy one reviewed commit containing all intended launch changes; run the launch check above and
   resolve every `FAIL` before changing payment mode.
2. Add `MONITORING_SUMMARY_EMAIL=solomonshasanmi@gmail.com`, confirm the cron rows in section 7 and
   get SiteGround's answer on the four high-frequency jobs. Run `$F jobs check` after they have had
   time to produce heartbeats.
3. Run `$F mail test solomonshasanmi@gmail.com`, then `$F studio daily-summary`; confirm the actual
   digest arrives. This is the only safe proof that SMTP, cron command configuration and the mailbox
   work together.
4. Complete `$F backup test-restore SPARE_DB_NAME`; keep the encrypted off-host backup and its
   passphrase separately.
5. Configure UptimeRobot (homepage and `/health/ready`) and schedule the credential-free security
   probe off-host with its own alert destination. Do not make an on-host email the only outage alert.
6. Enter live Stripe and PayPal values only in SiteGround's protected environment, including new
   live webhook signing values and `PAYPAL_API_BASE=https://api-m.paypal.com`; restart once. Confirm
   the Apple Pay association if Apple Pay is enabled. Do not paste or print any secret during checks.
7. Run one low-value real order and refund through each provider. Confirm provider dashboard,
   signed webhook receipt, database order state, inventory effect and customer/admin emails. Reset
   test-item stock if needed and hide the test item afterward.
8. Revoke superseded WordPress/sandbox/monitor credentials, remove `PREVIEW_PASSWORD`, restart, rerun
   the launch check and security probe without `--expect-preview`, and submit the sitemap to Search
   Console.
9. Watch payment, refund, order email, inventory, job, backup and security alerts for 48 hours.

## Optional: bring the local catalog and photos

On the PC: `docker exec gg_flask-db-1 pg_dump -U gg -Fc gg_site > gg_site.dump`, upload it and the
`instance\media\` folder (SFTP) to the Node project, then over SSH:
`pg_restore --no-owner -d "DB connection URL without +psycopg" gg_site.dump` and move the photos
into `~/.ggflask/media/`. Restart the project afterwards.

## Updating and rolling back

Push to `main` and click **Deploy** (or enable automatic deploys). Package or database changes are
applied automatically on the next start; a failed database update keeps the site on "starting
up" rather than serving with a mismatched database. To roll back, deploy the previous commit.
