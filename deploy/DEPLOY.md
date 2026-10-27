# Deploying to your own server (staging2 first, then live)

Written for a Linux server (Ubuntu 24.04 / Debian 13) with Apache in front. Every command is
copy-paste; replace values in `ALL_CAPS`. Do staging2 completely, test it, then repeat for the
live domain. Nothing here touches the WordPress site until you switch the domain over.

Files used: `deploy/gg-flask.service` (app service), `deploy/gg-flask.cron` + `deploy/run-job.sh`
(scheduled jobs), `deploy/apache-site.example.conf` (Apache site),
`deploy/htaccess.example` (private-path and file protection rules), `deploy/backup.sh` (nightly
backup).

## 1. Install the server software (once)

```bash
sudo apt update
sudo apt install -y apache2 redis-server rsync git certbot python3-certbot-apache
# PostgreSQL 17 — the same major version as the PC. Debian 13 ships it; on Ubuntu 24.04 (which
# ships 16, and 16 cannot restore a dump made by 17) add PostgreSQL's official repository first:
sudo apt install -y postgresql-common && sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
sudo apt install -y postgresql-17
# Python 3.13 — Debian 13 ships it; on Ubuntu 24.04 use the deadsnakes PPA:
sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt install -y python3.13 python3.13-venv
sudo a2enmod proxy proxy_http headers rewrite ssl expires
```

Redis must listen on localhost only (the default `bind 127.0.0.1 -::1` in `/etc/redis/redis.conf`).

## 2. App user, code and Python packages

```bash
sudo useradd --system --home /srv/gg_flask --shell /usr/sbin/nologin ggflask
sudo mkdir -p /srv/gg_flask && sudo chown ggflask:ggflask /srv/gg_flask
sudo -u ggflask git clone https://github.com/TheeGreenGenie/gg_flask.git /srv/gg_flask
cd /srv/gg_flask
sudo -u ggflask python3.13 -m venv .venv
sudo -u ggflask .venv/bin/pip install -r requirements.lock
sudo -u ggflask mkdir -p instance/media
sudo chmod +x deploy/run-job.sh deploy/backup.sh
```

(Private repository? Use a read-only deploy key: GitHub → repo → Settings → Deploy keys.)

## 3. Database

```bash
sudo -u postgres createuser ggflask
sudo -u postgres createdb -O ggflask gg_site
```

The app connects as `ggflask` over the local socket, so no database password is needed:
`DATABASE_URL=postgresql+psycopg://ggflask@/gg_site?host=/var/run/postgresql`.

**Bring your data over from this PC** (catalog, photos records, blog, settings) instead of
rebuilding it. On the PC, in PowerShell from the project folder:

```powershell
docker exec gg_flask-db-1 pg_dump -U gg -Fc gg_site > gg_site.dump
```

Copy `gg_site.dump` to the server (WinSCP or `scp`), then:

```bash
sudo -u ggflask pg_restore --no-owner --role=ggflask -d gg_site /path/to/gg_site.dump
```

## 4. Secrets file

```bash
sudo mkdir -p /etc/gg-flask
sudo install -m 640 -o root -g ggflask /dev/null /etc/gg-flask/env
sudo nano /etc/gg-flask/env
```

Start from `.env.example`. Minimum for staging (generate the two random values with
`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`):

```ini
APP_ENV=production
SECRET_KEY=RANDOM_48_CHARACTERS
DATABASE_URL=postgresql+psycopg://ggflask@/gg_site?host=/var/run/postgresql
REDIS_URL=redis://127.0.0.1:6379/0
TRUSTED_PROXY_HOPS=1
SERVE_MEDIA=0
MEDIA_ROOT=/srv/gg_flask/instance/media
STUDIO_PATH=ANOTHER_RANDOM_VALUE
SUPPORTED_COUNTRIES=US,CA,MX
FLAT_SHIPPING_CENTS=1000
LOCAL_SHIPPING_CENTS=500
```

Then add provider values as you get them (Stripe/PayPal sandbox, Shippo, Stripe Tax, SMTP email,
ntfy, GA4). Put quotes around values with spaces, e.g.
`MAIL_FROM="GG Afrikan Eleganz <orders@ggafrikaneleganz.com>"`. Never put this file in git.

Apply migrations (safe to repeat; also brings a restored dump up to date):

```bash
cd /srv/gg_flask && sudo -u ggflask sh -c 'set -a; . /etc/gg-flask/env; set +a; .venv/bin/flask --app wsgi db upgrade'
```

## 5. Photos and media

Copy the PC's `instance\media\` folder to `/srv/gg_flask/instance/media/` (WinSCP, or
`scp -r instance/media/* USER@SERVER:/tmp/media/` then `sudo rsync -a /tmp/media/ /srv/gg_flask/instance/media/`), then:

```bash
sudo chown -R ggflask:ggflask /srv/gg_flask/instance
sudo -u ggflask /srv/gg_flask/deploy/run-job.sh media verify   # logs "OK: every recorded file..."
journalctl -t gg-flask-job -n 5
```

## 6. Start the app

```bash
sudo cp /srv/gg_flask/deploy/gg-flask.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now gg-flask
curl -s http://127.0.0.1:8000/health/ready     # {"status": "ok", ...}
```

## 7. Apache, HTTPS and the private-path rules

```bash
sudo cp /srv/gg_flask/deploy/apache-site.example.conf /etc/apache2/sites-available/gg-flask.conf
sudo nano /etc/apache2/sites-available/gg-flask.conf   # set REPLACE_DOMAIN, REPLACE_OWNER_IP
sudo certbot certonly --apache -d REPLACE_DOMAIN
sudo a2ensite gg-flask && sudo apachectl configtest && sudo systemctl reload apache2
```

Paste the rules from `deploy/htaccess.example` inside the `<VirtualHost *:443>` block with your
real Studio path, then `apachectl configtest` and reload again. Disable the old WordPress site's
Apache config for this domain only when you switch over.

## 8. Accounts

```bash
cd /srv/gg_flask
sudo -u ggflask sh -c 'set -a; . /etc/gg-flask/env; set +a; .venv/bin/flask --app wsgi admin create --email YOU@EXAMPLE.COM --role owner'
sudo -u ggflask sh -c 'set -a; . /etc/gg-flask/env; set +a; .venv/bin/flask --app wsgi studio create --help'
```

Scan the admin QR/secret into your authenticator app and store the recovery codes offline.

## 9. Scheduled jobs and backups

```bash
sudo cp /srv/gg_flask/deploy/gg-flask.cron /etc/cron.d/gg-flask && sudo chmod 644 /etc/cron.d/gg-flask
sudo /srv/gg_flask/deploy/backup.sh && ls -l /var/backups/gg-flask/db   # first backup now
```

Copy `/var/backups/gg-flask` off the server regularly (another machine or cloud storage).

## 10. Smoke test (staging2)

- `https://DOMAIN/` loads; `curl -sI https://DOMAIN/ | grep -i strict-transport` shows HSTS.
- A product page shows photos (served by Apache: `curl -sI https://DOMAIN/media/...` shows the
  `sandbox` Content-Security-Policy).
- `https://DOMAIN/admin/` works from your IP and is refused from a phone on mobile data.
- An unknown address under the Studio path shows the normal 404; Studio sign-in sends the ntfy
  notification to your phone.
- With sandbox keys: place a Stripe and a PayPal test order; the confirmation email arrives.
- `journalctl -t gg-flask-job --since "10 min ago"` shows the minute jobs running.

## Updating to a new version

```bash
sudo /srv/gg_flask/deploy/backup.sh                       # 1. backup first, always
cd /srv/gg_flask && sudo -u ggflask git pull               # 2. new code
sudo -u ggflask .venv/bin/pip install -r requirements.lock # 3. packages
sudo -u ggflask sh -c 'set -a; . /etc/gg-flask/env; set +a; .venv/bin/flask --app wsgi db upgrade'
sudo systemctl reload gg-flask                             # 4. graceful restart, no downtime
```

Then repeat the smoke test.

## Rolling back

```bash
cd /srv/gg_flask && sudo -u ggflask git checkout PREVIOUS_COMMIT
sudo -u ggflask .venv/bin/pip install -r requirements.lock
```

If the bad version ran a database migration, restore the backup taken just before the update
(below) rather than trying to reverse the migration, then `sudo systemctl reload gg-flask`.

## Restore from backup (also the "backup + restore test")

```bash
sudo systemctl stop gg-flask
sudo -u postgres dropdb gg_site && sudo -u postgres createdb -O ggflask gg_site
sudo -u ggflask pg_restore --no-owner --role=ggflask -d gg_site /var/backups/gg-flask/db/gg_site-STAMP.dump
sudo rsync -a --delete /var/backups/gg-flask/media/STAMP/ /srv/gg_flask/instance/media/
sudo systemctl start gg-flask
sudo -u ggflask /srv/gg_flask/deploy/run-job.sh media verify
```

For the launch-gate restore test, restore into a scratch database (`createdb gg_restore_test`)
on staging instead of dropping the real one, point a copy of the env file at it, and check that
products, orders and photos are all there.
