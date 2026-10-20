# Docker release runbook

This release is a read-only student dashboard. Public release remains conditional on
confirming data permissions in DATA_SOURCES.md. No server, DNS record or public site
is created by building these files. Use a real domain only after those checks.

## Local preview and first install

Install Docker Engine with Compose v2 on Linux (Docker Desktop for local Windows testing).
Copy `.env.example` to `.env`, set a long random `POSTGRES_PASSWORD`, and retain
`SITE_ADDRESS=http://localhost` for local preview. Do not commit `.env`.
Passwords containing `$` must be single-quoted in `.env`; Compose constructs the database
URL safely, so URL-encoding passwords is unnecessary. Keep database/user names simple.

The `results/` directory must exist and contain these folders:

- `Slovakia_2025-2026 (01.03)` (legacy 1-hour/1-cycle folder)
- `Slovakia_2025-2026 (01.03)_1_2`
- `Slovakia_2025-2026 (01.03)_2_1`
- `Slovakia_2025-2026 (01.03)_2_2`

A suffixed `_1_1` folder, if present, takes precedence over the legacy folder. JSON is
mounted read-only into the importer, not copied into the image or mounted into the web app.
Only the annual dataset is imported by default. Other database datasets are retained but
unpublished. An explicit `--dataset NAME` import creates other datasets unpublished.

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps -a
docker compose logs --tail=100 migrate importer dashboard caddy
curl -f http://localhost/readyz
```

Order: healthy PostgreSQL → schema migration → import → healthy Gunicorn → Caddy.
`/healthz` checks the web process; `/readyz` checks the schema and published data.
When the DB is unavailable, readiness returns 503; Compose restart policies recover
exited processes, but health checks alone do not restart a still-running process.
Monitor readiness and container status with the hosting provider's monitoring system.

Only Caddy publishes host ports (80 and 443). PostgreSQL and port 5050 stay on the
Compose network. `HTTP_PORT` and `HTTPS_PORT` can change local host bindings.
For public HTTPS set `SITE_ADDRESS=index.example.edu`, point its DNS records to the
server, and make inbound ports 80/443 available. Caddy obtains/renews certificates;
`caddy_data` stores certificate state. Keep `http://localhost` only for local preview.

Web requirements are isolated in `docker/requirements.txt` (Python 3.12).
Do not use the copied legacy `.venv`: recreate environments from dependency files.
The old root requirements still serve the calculation/notebook workflows.

## Import, update and migration

### Local inspection with DBeaver

An optional `docker-compose.local.yml` overlay publishes PostgreSQL only on the local
computer at `127.0.0.1:15432`. The production Compose file still publishes no DB port.
For the existing local preview, apply it from the project directory:

```sh
docker compose --env-file .env.release-check -p bri-release-check -f docker-compose.yml -f docker-compose.local.yml up -d --no-deps db
```

In DBeaver create a PostgreSQL connection with host `127.0.0.1`, port `15432`,
database `bri_release_check`, username `bri`, and the `POSTGRES_PASSWORD` value from
the local `.env.release-check` file. Do not substitute the sample `.env.example` values.
Select Test Connection, then Finish; expand Schemas → public → Tables and open Data.

Keep both `-f` options on later `up` commands when local DB access is needed. Running
`up` using only the production file can recreate the DB container without the local port;
the database remains in the same `pgdata` volume. To remove local access intentionally,
run the same command with only `-f docker-compose.yml`.

### Import commands

Replace/extend JSON files on the host, then run the importer explicitly:

```sh
docker compose run --rm --no-deps importer
docker compose run --rm --no-deps importer python -m tools.import_results_to_db --dataset 'Slovakia_2025-2026 (01.03)' --force
```

Normal imports upsert by dataset/battery/day/market, retaining days absent from the input.
`--force` replaces each selected battery combo, removing obsolete rows only after that
combo's complete validation. Different combos are separate transactions: an error in a
later folder does not undo earlier successful folders. Never modify JSON during an import;
publish complete files atomically. No import downloads prices or runs optimization.

Before upgrading an existing deployment, schedule maintenance and stop dashboard and
all other DB writers. Keep the same Compose project name and `pgdata` volume as before.
The old image expects a different schema; it must not run while the schema is migrated.

```sh
docker compose stop dashboard caddy importer
docker compose build
docker compose run --rm --no-deps migrate
docker compose run --rm --no-deps importer
docker compose up -d dashboard caddy
```

Migration v1 handles an empty DB or the old text-key `daily_revenue`. For a legacy DB it
first runs `pg_dump` into the private `backups` volume and fails without a successful dump.
Migration then locks the source table, moves all datasets and markets, compares row counts
and every key/value in both directions, and records schema version 1 in one transaction.
The old table remains `daily_revenue_legacy_v0` for comparison. New writes go only to the
normalized table; the legacy copy is not a continuing backup. Do not delete `pgdata`.

The schema is `datasets(id, name UNIQUE, is_published)`, `markets(id, code UNIQUE)` and
`daily_revenue(dataset_id FK, system_type, cycle_limit, day, market_id FK, daily_revenue_eur)`.
The five identity fields form the result PK; both FKs restrict deletion. Negative revenue
is valid. `system_type` is duration in hours for the existing 1 MW benchmark. No market
aliases are merged. Only the annual dataset receives `is_published=true` automatically;
publication changes are an explicit administrator SQL update, never a browser action.

## Backup, restore and rollback

Run an on-demand backup using the same pg_dump/pg_restore client installed in the app image:

```sh
docker compose run --rm --no-deps migrate sh docker/backup.sh
docker compose run --rm --no-deps migrate ls -lh /backups
```

Copy backups off the host regularly. To export a named dump to the local `backups/` directory:

```sh
mkdir -p backups
docker compose create --no-deps migrate
docker compose cp migrate:/backups/FILE.dump ./backups/FILE.dump
```

For restoration, first stop the dashboard and every writer. The following command
**replaces objects in the target database**; confirm the project/DB and dump first:

```sh
docker compose stop dashboard caddy importer
docker compose run --rm --no-deps -e RESTORE_CONFIRM=replace-database migrate sh docker/restore.sh /backups/FILE.dump
docker compose up -d dashboard caddy
```

`pg_restore --clean` replaces objects present in the dump; it does not remove unrelated
objects. For rollback to the pre-migration release, restore the pre-v1 dump into a fresh,
empty replacement database and use the exact previous image/configuration against that
database; do not overlay it on the normalized schema or run the new migration again.
Keep the current volume until rollback is verified. For a restore rehearsal,
use a separate Compose project with a fresh DB volume, copy the dump into its backup volume,
restore, and compare row counts and dashboard results before reopening traffic. Never run
`docker compose down -v` against a deployment whose data must be kept.

## Cron (example only, disabled)

Cron is the Linux service that runs commands on a schedule. The five fields are minute,
hour, day of month, month and weekday; `0 6 * * *` means daily at 06:00 in cron's timezone.
See https://man7.org/linux/man-pages/man5/crontab.5.html.

`docker/cron.example` is fully commented out. It assumes a UTC-configured scheduler,
`/opt/bri`, `/usr/bin/docker` and `/usr/bin/flock`; check these paths on the chosen host.
It uses absolute paths, captures stdout/stderr and prevents overlapping scheduled imports.
The deployment user needs write access to the lock/log directory and Docker access.
Rotate `import.log` with host logrotate; no email or external notification is sent.

Only after successful manual imports, install the example with the appropriate host
administrator. This release installs no cron jobs. A future download → calculate → import
pipeline needs a separate calculation image, credentials, data-lag policy and backfill rules.

## Tests and source distribution

```sh
docker build --target test -t bri-tests .
docker run --rm bri-tests
```

Run the full suite including PostgreSQL 16, backup/restore and historical JSON audit:

```sh
docker compose -f docker-compose.test.yml -p bri-tests up --build --abort-on-container-exit --exit-code-from tests
docker compose -f docker-compose.test.yml -p bri-tests down
```

This uses a separate temporary test database without host ports or deployment volumes.

To run the PostgreSQL tests, start an isolated PostgreSQL 16 database whose name ends in
`_test` and pass its SQLAlchemy URL as `TEST_DATABASE_URL` to the test container. Tests
reset tables in that test DB. Never point them at a deployment database.

Before distribution, tag the exact source revision and image version together. Provide
the matching source, LICENSE.txt, NOTICE.md, Docker scripts and dependency information
beside the image download. Preserve dependency notices installed in site-packages.
The existing GitLab Pages job builds documentation, not this Flask service.
