FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml wsgi.py ./
COPY app ./app
COPY migrations ./migrations
COPY catalog ./catalog

# Runs from /srv (not pip-installed) so app.root_path, catalog/ and instance/ resolve here.
# instance/media holds seeded product images until object storage is set up.
RUN useradd --system --uid 10001 --no-create-home appuser \
    && mkdir -p /srv/instance/media && chown -R appuser /srv/instance
USER appuser

ENV APP_ENV=production
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"

# No access log here: raw request lines can contain one-time links (see deploy/gg-flask.service).
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "wsgi:app"]
