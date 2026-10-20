FROM postgres:16-bookworm AS postgres_client

FROM python:3.12-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5050

WORKDIR /app

# Match dump/restore clients to the server major version. Newer clients can emit
# settings (e.g. transaction_timeout) which PostgreSQL 16 cannot restore.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 liblz4-1 libzstd1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=postgres_client /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=postgres_client /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/pg_restore
COPY --from=postgres_client /usr/share/doc/postgresql-16/copyright /usr/local/share/licenses/postgresql-16/copyright

COPY docker/requirements.txt docker/requirements.txt
RUN pip install -r docker/requirements.txt

COPY tools/slovakia_revenue_dashboard.py tools/results_db.py tools/import_results_to_db.py tools/result_files.py tools/migrate_results_db.py tools/
COPY docker/wsgi.py docker/about.html docker/backup.sh docker/restore.sh docker/
COPY LICENSE.txt NOTICE.md DATA_SOURCES.md ./

RUN useradd --uid 10001 --create-home appuser \
    && mkdir /backups && chown appuser:appuser /backups
USER appuser

EXPOSE 5050

# Hosting platforms (Render, Railway, Fly, ...) inject $PORT; locally it defaults to 5050.
CMD ["sh", "-c", "exec gunicorn --chdir /app/docker --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 --access-logfile - --error-logfile - wsgi:app"]

FROM runtime AS test
USER root
RUN pip install pytest==8.4.2
COPY tests/ tests/
USER appuser
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]

# The normal build remains the runtime image, with no test dependencies.
FROM runtime AS production
