# Release validation — 2026-10-20

Validation used isolated Docker projects on Docker Desktop's Linux engine. No university
server or public domain was deployed. Existing user edits and historical results were kept.

## Automated checks

`docker compose -f docker-compose.test.yml -p bri-tests up --build --abort-on-container-exit --exit-code-from tests`

Result: **31 passed, 3 skipped**, exit code 0, Python 3.12 / PostgreSQL 16.15.
The three skips are PostgreSQL-only cases under the SQLite fixture; their PostgreSQL
variants executed successfully. This covers:

- Fresh schema and legacy migration; exact data preservation and publication defaults.
- Dataset/market foreign keys, duplicate keys and restricted deletion.
- Repeated upserts, changed values, newly appended days, and explicit full replacement.
- Invalid input and corrupted JSON; transaction rollback after an injected insert failure.
- Mandatory successful backup before legacy CLI migration and PostgreSQL DDL rollback.
- Real pg_dump/pg_restore round trip, using matching PostgreSQL 16 clients.
- Four battery variants, market coverage, missing-day gaps, grouped bars, per-market
  cumulative raw EUR, URL publication boundaries and validation of date/filter inputs.
- Health/readiness, attribution, and byte-for-byte delivery of the original LICENSE.txt.
- Full historical inventory: 8,055 JSON files and battery metadata consistency.

## End-to-end checks

- Production image built successfully, with pinned direct/transitive web requirements.
- Compose started PostgreSQL → migration → importer → Gunicorn → Caddy successfully.
- Repeated import and image recreation retained 8,055 rows without duplicates.
- Every key and floating point value in PostgreSQL matched the source JSON exactly for
  all four battery variants: 2,562 / 1,831 / 1,831 / 1,831 rows.
- Restarted DB and web containers; readiness returned 200 and data remained available.
- Ran the supplied backup and restore scripts for the full 8,055-row database into an
  isolated replacement DB. Ordered keys and binary float representations matched by hash.
- HTTP index, all four variants, about, license and readiness returned 200; a private
  dataset URL returned 404. Malformed moving-average input did not crash the page.
- Verified HTTPS through Caddy with a local CA explicitly trusted by the test client;
  certificate validation was enabled. Public ACME issuance still needs real DNS/domain.
- Missing POSTGRES_PASSWORD was rejected by Compose. DB and web ports are not published.
- Image contains LICENSE.txt, NOTICE.md and PostgreSQL client copyright information;
  it contains neither historical JSON nor .venv. `pip check` passed.
- LICENSE.txt in the repository was not modified. `.venv` is no longer tracked (33,039
  staged removals); local files, including its interpreter, remain on disk.
- `git diff --check` passed. Git reports line-ending conversion notices on Windows;
  shell files have LF checkout rules in .gitattributes.

## Remaining external conditions

Select the Linux host/domain and configure DNS before public HTTPS deployment. Record
the applicable OKTE/SEPS data reuse terms or permissions described in DATA_SOURCES.md.
Browser UI automation was unavailable in this session; page responses and Plotly figure
data were tested, but no browser screenshot review is claimed. Cron remains disabled.

A loopback-only local preview is running at http://localhost:18080 using the ignored
`.env.release-check` file and Compose project `bri-release-check`. Its runtime data and
backups are in that project's named volumes. Stop it without removing its data with:

```sh
docker compose --env-file .env.release-check -p bri-release-check stop
```
