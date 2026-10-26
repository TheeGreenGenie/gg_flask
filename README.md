# GG Afrikan Eleganz — Flask storefront

Rebuild of ggafrikaneleganz.com as a Flask application.

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
| `/cart/`, `/checkout/` | Cart (server-side) and checkout (payments arrive in Phase 7) |
| `/media/<path>` | Product images in development (object storage/CDN in production) |

## Dependencies

`pyproject.toml` lists direct dependencies; `requirements.lock` pins the exact runtime set. To update the lock, install `.` into a clean virtual environment and `pip freeze` it.
