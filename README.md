# GG Afrikan Eleganz — Flask storefront

Rebuild of ggafrikaneleganz.com as a Flask application.

For a codebase map and junior-developer continuation assignments, read
[architecture_walk_through.md](architecture_walk_through.md). Current completion and owner blockers
are tracked only in [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Run locally

Requirements: Python 3.13, Docker Desktop.

```bash
# 1. Start PostgreSQL (port 55432) and Redis (port 6379)
docker compose up -d db redis

# 2. Create a virtual environment and install
py -3.13 -m venv .venv
.venv/Scripts/pip install -r requirements.lock
.venv/Scripts/pip install -e ".[dev]"

# 3. Apply database migrations and start the dev server
set FLASK_APP=wsgi.py            # PowerShell: $env:FLASK_APP="wsgi.py"
.venv/Scripts/flask db upgrade
.venv/Scripts/flask run
```

Load the catalog (`flask --app wsgi catalog load`), then open http://127.0.0.1:5000/.

To run the production container locally: `docker compose --profile full up --build`, then run
`docker compose exec web flask --app wsgi db upgrade` and `... catalog load`, and open
http://127.0.0.1:8000/.

**Deploying to a Linux server with Apache:** step-by-step in [deploy/DEPLOY.md](deploy/DEPLOY.md)
(gunicorn service, Apache site with HTTPS and direct static/media serving, scheduled jobs,
nightly backups, updates, rollback and restore).

## Catalog

Products come from [catalog/manifest.yaml](catalog/manifest.yaml) plus photos in `catalog/assets/`.
Edit the YAML, then:

```bash
.venv/Scripts/flask --app wsgi catalog validate   # check for problems, changes nothing
.venv/Scripts/flask --app wsgi catalog load       # add/update products (safe to re-run)
```

Products removed from the manifest are archived, never deleted. Prices are whole cents
(`899` = $8.99). Every product has at least one variant.

### Automatic names

```bash
.venv/Scripts/flask --app wsgi catalog autoname            # preview
.venv/Scripts/flask --app wsgi catalog autoname --write    # save into manifest.yaml
.venv/Scripts/flask --app wsgi catalog autoname --sheet review.png   # visual check
```

Names follow `<family as on the site> - <colors> - <code>`, e.g.
`African Ankara Print - Navy & Gold - 101184`. Colors are measured on the fabric only: a
plain light studio backdrop is removed first, then each remaining pixel is matched to the
fixed color list and counted, so the preview shows each color's share of the fabric. Set
`colors: [Blue, Gold]` on a product to override detection, or `autoname: false` to skip it.

### Drafting from the old site (dev only)

`scripts/export_manifest_draft.py` reads the old WordPress database (read-only) and drafts
a manifest with converted photos for curation. Needs `pip install -e ".[tools]"`.

### Reviewing and bulk-editing the catalog (before launch)

- `python scripts/catalog_review.py [--wp-db new_wordpress_local]` writes
  `instance/catalog_review.html` (+ `.csv`): every product with its photo, price and stock, and
  what still needs a decision (per-yard prices sold as bundles, placeholder stock, missing
  weights, single photos, placeholder SKUs), most-flagged first.
- `python scripts/catalog_sheet.py export` writes `instance/catalog_sheet.csv` (one row per SKU)
  for editing price, crossed-out price, stock, weight and selling unit in a spreadsheet;
  `python scripts/catalog_sheet.py apply FILE.csv` previews the changes and `--write` saves them
  to the manifest (an invalid sheet changes nothing). Then `flask catalog load`.

Once staff work in the Studio, edit products there instead of the manifest.

## Marketing contacts

Customers who opted in can be exported for your email or SMS provider:

```bash
.venv/Scripts/flask --app wsgi accounts export-marketing --channel email > email-optins.csv
.venv/Scripts/flask --app wsgi accounts export-marketing --channel sms > sms-optins.csv
```

The files contain personal data: import them into the provider, then delete them. Only
people whose latest choice is "yes" are included; every opt-in/opt-out is kept in
`marketing_consents` with the exact wording shown, the date and the source.

Password-reset emails print to the terminal in development. On the live site reset is
switched off (and says so) until an email provider is connected (`MAIL_BACKEND`).

## Administration

Apply migrations, then provision the first administrator from a trusted terminal:

```bash
.venv/Scripts/flask --app wsgi admin create --email owner@example.com --role owner
```

The command prints an authenticator-app secret/URI and ten one-use recovery codes. Store
the recovery codes securely; they cannot be displayed again. Administration is available
at `/admin/`. Every administrator must complete two-factor authentication. Sessions expire
after 30 minutes idle or eight hours total, and catalog mutations are recorded in the audit
log. Roles are `owner`, `catalog`, and `support`.

Owners and support administrators can manage orders, private notes, status progression,
confirmation emails, invoices, and packing slips. Only owners can trigger a provider refund.
Owners and catalog administrators can manage coupons.

## Checkout and payments

Checkout fails closed until supported countries, flat shipping, and tax handling are set.
Configure the variables documented in `.env.example`, then register these HTTPS webhooks:

- Stripe: `/webhooks/stripe` (`checkout.session.completed`)
- PayPal: `/webhooks/paypal` (`PAYMENT.CAPTURE.COMPLETED`)

Provider requests and callbacks are idempotent. Order prices are snapshotted and stock is
reserved for 60 minutes. Schedule `flask --app wsgi orders release-expired-holds` at least
every 15 minutes. `TAX_BACKEND=none` is only appropriate when the business has confirmed
that no tax should be collected; otherwise checkout remains closed until a tax provider is
implemented and selected.

Newsletter signup uses Mailchimp double opt-in when its three `MAILCHIMP_*` variables are
configured. Exact-match legacy redirects live in `legacy_redirects.csv`; review that file
against analytics and backlink exports before launch.

## Shipping, tax and analytics providers

| Setting | Effect |
|---|---|
| `SHIPPING_BACKEND=shippo`, `SHIPPO_API_TOKEN`, `SHIP_FROM_*` | Live USPS rates from Shippo, cheapest USPS service, by parcel weight (product `weight_grams`, default 450 g each + 120 g packaging) and a 12×10×4 in box. Falls back to `FLAT_SHIPPING_CENTS` if Shippo is unavailable. `SHIPPING_HANDLING_CENTS` adds a fixed amount. |
| `LOCAL_SHIPPING_CENTS` | Philadelphia ZIPs (191xx, 19019, 19092, 19093, 19099) always get this rate. |
| `TAX_BACKEND=stripe` (+ `STRIPE_SECRET_KEY`) | Stripe Tax calculates tax at checkout for Stripe and PayPal orders, records a tax transaction after payment and reverses it on refund. If Stripe Tax is unreachable the order is refused rather than charged the wrong tax. |
| `MAIL_BACKEND=smtp`, `SMTP_HOST/PORT/USERNAME/PASSWORD`, `SMTP_SECURITY`, `MAIL_FROM` | Password-reset, order and Studio-summary emails through any provider's SMTP relay (Postmark, Amazon SES, SendGrid, Google Workspace…). Always encrypted (STARTTLS on 587 or SSL on 465); production refuses `console`/`memory`. A failed send is logged without the message or address. Verify the sending domain with the provider (SPF/DKIM/DMARC DNS records) or mail lands in spam. |
| `GA4_MEASUREMENT_ID=G-…` | Google Analytics 4 with a consent banner. Nothing is sent to Google until a visitor clicks Accept; Global Privacy Control counts as Decline. Reports `view_item`, `add_to_cart`, `begin_checkout`, `purchase`. |

## Media library

Photos, videos, SVGs and 3D models live as files under `MEDIA_ROOT` (default `instance/media`,
served at `/media/...`). PostgreSQL keeps one `media_assets` row per file: path, SHA-256,
type, size, dimensions, title/alt text and, for imported files, the WordPress item it came from.

```bash
# Copy real media from the old WordPress site (re-runnable; --dry-run to preview)
flask --app wsgi media import-wordpress   --uploads /path/to/wp-content/uploads   --theme-assets /path/to/wp-content/themes/woodmart-child/3d-assets   --wp-db mysql://root@localhost/new_wordpress_local

# Check every recorded file is present and unchanged
flask --app wsgi media verify
```

Private and generated WordPress folders (order logs, invoices, contact-form uploads, plugin
logs, cached CSS/fonts) are never read, and WordPress's resized copies are skipped.

**Moving to the server / backups:** the database and the media folder travel together.
Restore the `pg_dump`, copy the `MEDIA_ROOT` folder to the server's media path (e.g.
`rsync -a instance/media/ server:/srv/instance/media/`), then run `flask media verify`.
Set `MEDIA_ROOT` to that path. In production, have the web server serve `/media/` straight from
that folder (with the same `Content-Security-Policy: sandbox` and `nosniff` headers) and set
`SERVE_MEDIA=0`.

## Scheduled jobs

Run daily: `flask --app wsgi wishlist-prune` deletes wishlists nobody has changed for 90 days.
Run every 15 minutes: `flask --app wsgi orders release-expired-holds` releases abandoned
checkout reservations.
Run every minute: `flask --app wsgi studio sync-sales` activates and ends confirmed Staff Studio
sales at their scheduled times. The command is idempotent.
Run every minute: `flask --app wsgi studio sync-blog` publishes scheduled journal articles.
Run daily: `flask --app wsgi reviews purge-removed` permanently deletes expired private copies of
automatically removed text when `MODERATION_RETENTION_DAYS=15`.
Run daily: `flask --app wsgi studio daily-summary` emails operational counts to
`ORDER_NOTIFICATION_EMAIL` through the configured mail backend.

## Staff Studio (in progress)

The non-technical staff area is mounted at the private path in `STUDIO_PATH`; it is intentionally
absent from public navigation, robots, and the sitemap. Production requires a random URL-safe path
of at least 24 characters. Local development defaults to `/studio-local-development-only/`.

Accounts are managed only on the server. Password options prompt securely and are never accepted as
command-line arguments:

```bash
flask --app wsgi studio create
flask --app wsgi studio list
flask --app wsgi studio reset <username>
flask --app wsgi studio disable <username>
flask --app wsgi studio enable <username>
flask --app wsgi studio sync-users --file config/studio_users.json
flask --app wsgi studio sync-users --file config/studio_users.json --apply
```

`config/studio_users.json` is the editable roster for local and server environments. It contains
only usernames, roles, and active state—never passwords. The first command previews changes. The
second applies them and securely prompts for each new user's temporary password. Users removed
from the file are disabled and signed out rather than deleted, preserving their audit history.

`STUDIO_APPROVAL_BACKEND=console` logs the one-time owner approval URL during local development.
`flask studio approve-local <request-id>` is also available outside production for testing. Neither
is the production approval mechanism. The owner selected ntfy: production uses
`STUDIO_APPROVAL_BACKEND=ntfy`, a long private `STUDIO_NTFY_TOPIC`, and optionally a protected-topic
token. Launch still requires WebAuthn/passkey confirmation after the notification opens the approval
page. See `deploy/htaccess.example` for edge-protection rules that must be adapted and verified on
staging.

## Checks (same as CI)

```bash
.venv/Scripts/ruff check .
.venv/Scripts/ruff format --check .
.venv/Scripts/mypy
.venv/Scripts/pytest
```

Tests use SQLite and a fake Redis by default. Set `TEST_DATABASE_URL` / `TEST_REDIS_URL` to run them against real services (CI does).

## Configuration

All settings come from environment variables; see [.env.example](.env.example) for names. `APP_ENV=production` (used for staging and live) refuses to start without `SECRET_KEY` (32+ chars), `DATABASE_URL` and `REDIS_URL`.

Set `TRUSTED_PROXY_HOPS` to the number of reverse proxies in front of the app. With `0`, `X-Forwarded-For` is ignored.

## Endpoints

| Path | Purpose |
|---|---|
| `/health/live` | Process is up (no dependency checks) |
| `/health/ready` | Database and Redis reachable; 503 otherwise |
| `/` | Homepage |
| `/shop/`, `/shop/<category>/` | All products / a category (includes its subcategories) |
| `/collections/<slug>/` | Hand-picked collections plus `new-arrivals` and `sale` |
| `/product/<slug>/` | Product page |
| `/cart/`, `/checkout/` | Server-side cart and duplicate-proof hosted checkout |
| `/find-fabrics/` | Swipe-to-save fabric discovery (feed: `/api/v1/discovery/fabrics`) |
| `/wishlist/` | Anonymous wishlist (JSON: `/api/v1/wishlist`) |
| `/account/` | Customer accounts: register, sign in, details, message preferences, password, delete |
| `/admin/` | Private two-factor administration for catalog, orders, refunds, and coupons |
| `/<STUDIO_PATH>/` | Hidden owner-approved Staff Studio (path comes only from the environment) |
| `/sitemap.xml`, `/robots.txt` | Search-engine discovery rules |
| `/media/<path>` | Product images in development (object storage/CDN in production) |

## Dependencies

`pyproject.toml` lists direct dependencies; `requirements.lock` pins the exact runtime set. To update the lock, install `.` into a clean virtual environment and `pip freeze` it.
