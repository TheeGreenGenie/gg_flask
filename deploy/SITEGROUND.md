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
| `~/.ggflask/logs/` | `wrapper.log`, `app.log`, `jobs.log` (rotated at 5 MB) |
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
LOCAL_SHIPPING_CENTS=500
TAX_BACKEND=none
MAIL_BACKEND=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=ggafrikan@gmail.com
SMTP_PASSWORD=the app password
MAIL_FROM="GG Afrikan Eleganz <ggafrikan@gmail.com>"
ORDER_NOTIFICATION_EMAIL=ggafrikan@gmail.com
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
$F admin create --username owner --email ggafrikan@gmail.com --role owner   # scan the QR/secret
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

If `/home/customer` is not your home path, use the output of `echo $HOME`. Check them with
`$F jobs check` the next day.

**Backups:** run `$F backup run` once by hand first. It needs `pg_dump` on the server (SiteGround
includes it with PostgreSQL); the message says plainly if it is missing. `$F backup list` shows
what is kept.

**Off-server copy by email:** add `BACKUP_PASSPHRASE=` (a long phrase only you know, 16+
characters; write it down somewhere safe, it cannot be recovered) to `~/.ggflask/env`. Each
backup is then encrypted with it and emailed to the shop address (ggafrikan@gmail.com; set
`BACKUP_EMAIL=` for another). Without the passphrase nothing is emailed. To open an emailed
`.enc` file on your computer: `flask --app wsgi backup decrypt FILE.enc`.
Restoring replaces the live data, so ask Claude for the exact `pg_restore` command first.

## 8. Staging checks (paste results to Claude; no secrets)

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
   `curl.exe -sI https://staging2.ggafrikaneleganz.com/ | findstr /i "cache-control x-proxy-cache"`
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
   `curl.exe -s -o NUL -w "%{time_starttransfer}s" https://staging2.ggafrikaneleganz.com/`, and
   again right after.

## Optional: bring the local catalog and photos

On the PC: `docker exec gg_flask-db-1 pg_dump -U gg -Fc gg_site > gg_site.dump`, upload it and the
`instance\media\` folder (SFTP) to the Node project, then over SSH:
`pg_restore --no-owner -d "DB connection URL without +psycopg" gg_site.dump` and move the photos
into `~/.ggflask/media/`. Restart the project afterwards.

## Updating and rolling back

Push to `main` and click **Deploy** (or enable automatic deploys). Package or database changes are
applied automatically on the next start; a failed database update keeps the site on "starting
up" rather than serving with a mismatched database. To roll back, deploy the previous commit.
