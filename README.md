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

Open http://127.0.0.1:5000/ and http://127.0.0.1:5000/health/ready.

To run the production container locally: `docker compose --profile full up --build`, then open http://127.0.0.1:8000/.

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

## Dependencies

`pyproject.toml` lists direct dependencies; `requirements.lock` pins the exact runtime set. To update the lock, install `.` into a clean virtual environment and `pip freeze` it.
