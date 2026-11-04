# Battery Revenue Index SK

**English** · [Slovensky](README.sk.md)

Revenue benchmark for a battery energy storage system (BESS) on the Slovak electricity markets,
adapted from the [ISEA Battery Revenue Index](https://battery-charts.rwth-aachen.de/) (RWTH Aachen).

The system downloads market data, stores it in PostgreSQL with full provenance, calculates daily
revenues for four battery configurations and publishes them on a web dashboard. Everything runs in
Docker; a worker container updates the data every day at 16:00 (Europe/Bratislava).

| Market | Description | Source |
|---|---|---|
| DA | Day-ahead auction (hourly until 30.09.2025, 15-minute from 01.10.2025) | OKTE API |
| IDM15, IDM60 | Intraday market, 15- and 60-minute products | OKTE API |
| IMB | Imbalance settlement price (best of final / monthly / daily evaluation) | OKTE API |
| aFRR | Capacity (4-hour blocks, up/down) + activated energy | ENTSO-E API, SEPS/Damas XLSX |

Battery configurations: 1 MW with 1 h or 2 h storage, 1 or 2 cycles per day.

---

## Contents

1. [Architecture](#1-architecture)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Configuration (`.env`)](#4-configuration-env)
5. [First start: database, data migration, services](#5-first-start)
6. [SEPS/Damas Excel input](#6-sepsdamas-excel-input)
7. [Daily operation](#7-daily-operation)
8. [Tests](#8-tests)
9. [Checking that the system works](#9-checking-that-the-system-works)
10. [Database](#10-database)
11. [Troubleshooting](#11-troubleshooting)
12. [Project structure](#12-project-structure)
13. [License](#13-license)

---

## 1. Architecture

```text
 OKTE API ─┐                      ┌──────────────── Docker Compose ─────────────────┐
 ENTSO-E ──┼──────────────────────▶ worker (supercronic, daily 16:00)              │
           │                      │   python -m bri.pipeline                        │
 SEPS XLSX ┤  local inbox folder  │   1 lock  2 files  3 APIs  4 readiness          │
  (manual) └─ or OneDrive (rclone)│   5 calculation  6 publication  7 status report │
                                  │                  │                              │
                                  │                  ▼                              │
                                  │   PostgreSQL (schema bri) ◀── dashboard :5050   │
                                  └─────────────────────────────────────────────────┘
```

| Service | Role |
|---|---|
| `db` | PostgreSQL 16, schema `bri`: inputs, readiness, results, logs |
| `worker` | runs the pipeline once a day (the only scheduler) |
| `dashboard` | Flask/Gunicorn web app; reads only published results |
| `migrate` | one-off job (profile `jobs`): imports legacy files and backfills history |

**Data model in short** (`bri/sql/001_schema.sql`):

- every input value keeps its provenance: `source_artifact` (immutable raw file or API response, archived
  as `raw/<sha256>`) → `import_batch` (one parse attempt: running / success / failed);
- inputs are stored per market (`price_da`, `price_idm15`, `price_idm60`, `price_imb`, `afrr_capacity`,
  `afrr_energy`) as **immutable revisions** with native interval start/end; `*_current` views return the
  newest successful value;
- `market_day` records whether each market/day is complete (intervals cover the local day, DST-safe);
- every calculation is a `calc_run` with an exact input snapshot (`calc_input`); results are kept for all
  runs (`result_daily`, `result_interval`), and `result_publication` selects the one version shown on the
  dashboard (view `published_daily_total`).

A day is recalculated only when its input snapshot or the model version changes.
aFRR is calculated only when both capacity and energy data of the day are complete.

---

## 2. Requirements

| Software | Notes |
|---|---|
| Docker Desktop (Windows/macOS) or Docker Engine + Compose v2 (Linux) | all components run in containers |
| Git | to clone the repository |
| Internet access | OKTE and ENTSO-E APIs, image build (pip, rclone, supercronic) |
| ENTSO-E Transparency Platform API token | aFRR capacity and activation prices ([how to get one](https://transparency.entsoe.eu/content/static_content/Static%20content/web%20api/Guide.html#_authentication_and_authorisation)) |
| ~3 GB free disk space | image ~0.7 GB, database ~1 GB after the full backfill |

Optional: [DBeaver](https://dbeaver.io/) or any PostgreSQL client to browse the database;
[rclone](https://rclone.org/) only if SEPS files are delivered through OneDrive.

---

## 3. Installation

```bash
git clone -b feature/db-pipeline https://github.com/Ri4ik/battery_revenue_index_sk.git
cd battery_revenue_index_sk

cp .env.example .env                       # then edit it, see section 4
mkdir -p secrets/rclone incoming reports/local_inbox
docker compose build
```

> **Windows:** create the folders above **before** the first `docker compose` command. Docker Desktop may
> fail with `containers/create: EOF` when it has to create a bind-mounted folder itself, especially when the
> path contains non-Latin characters. In Git Bash prefix commands that pass container paths (`/app/...`)
> with `MSYS_NO_PATHCONV=1`, or use PowerShell.

---

## 4. Configuration (`.env`)

`.env` is not tracked by Git. Start from `.env.example`:

| Variable | Meaning | Example |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | database credentials (database is reachable only from `127.0.0.1:5432`) | `bri` / *your password* / `battery_revenue_index` |
| `ENTSOE_API_KEY` | ENTSO-E token; without it aFRR data are not downloaded | |
| `BRI_FILE_SOURCE` | where SEPS XLSX files come from: `local`, `onedrive` or `none` | `local` |
| `BRI_INBOX_HOST` | host folder for SEPS files (local source) | `./incoming` |
| `BRI_REPORTS_HOST` | host folder for import reports (local source) | `./reports/local_inbox` |
| `BRI_LOCAL_MIN_AGE_SECONDS` | a file must be unchanged this long before it is picked up | `10` |
| `BRI_DELETE_AFTER_SUCCESS` | `0` keeps imported files (moved to `incoming/processed/`); `1` deletes them after verified publication, **including their copies in the raw archive** | `0` |
| `ONEDRIVE_REMOTE` | rclone remote of the OneDrive folder (OneDrive source only) | `onedrive:BatteryIndex` |

---

## 5. First start

```bash
docker compose up -d db                    # database
docker compose run --rm migrate            # one-off: legacy data + backfill from 2025-03-01 (~40 min)
docker compose up -d                       # worker + dashboard
```

`migrate` does, idempotently:

1. creates the schema and the four battery configurations;
2. imports the legacy files shipped in the repository (aFRR capacity CSV, original SEPS XLSX, JSON results
   as calculation run `legacy-json-v1`);
3. downloads DA, IDM15, IDM60 and IMB again from the OKTE API with their native intervals, checks
   completeness and calculates all days with the current model; these results replace the legacy ones
   wherever inputs are complete.

Open the dashboard at **<http://127.0.0.1:5050/>**.

---

## 6. SEPS/Damas Excel input

aFRR activated energy is not available through a public API in the required form. It is uploaded as the
SEPS/Damas export **„Regulačná elektrina (denná)“** (XLSX, worksheet `aFRR`). Two formats are accepted:

- **priced export** (7 or 9 columns): date, hour, quarter, up volume, up price, down volume, down price;
- **volume-only export** (5 columns): date, hour, quarter, up volume, down volume — the matching
  activation prices are downloaded from ENTSO-E (document A84) automatically.

Every day in the file must contain all quarter-hours of the local day (92, 96 or 100).

### 6.1 Local inbox (`BRI_FILE_SOURCE=local`, default)

Copy the file into `incoming/` (on Windows preferably with the helper, which copies atomically and verifies
the checksum):

```powershell
.\tools\upload_local_input.ps1 -Path "C:\path\to\seps_export.xlsx"
```

The next pipeline run imports it (or start it immediately, see section 7). A report (JSON + TXT) is written
to `reports/local_inbox/`. Imported files are moved to `incoming/processed/`, unusable ones to
`incoming/errors/`. If prices are not yet available from ENTSO-E the file waits in `incoming/.processing/`
and the next run continues.

### 6.2 OneDrive (`BRI_FILE_SOURCE=onedrive`)

A OneDrive Personal folder of a dedicated project account, accessed by the worker through rclone:

```text
BatteryIndex/
├── 1_Nahrať/       upload SEPS XLSX here
├── 2_Spracované/   imported files (moved after the database commit)
├── 3_Chyby/        rejected files + <file>.chyba.txt with the reason
└── STAV.txt        status written by every run
```

One-time authorisation on a computer with a browser (log in as the folder owner):

```bash
rclone config --config secrets/rclone/rclone.conf
#   n → name "onedrive" → storage "onedrive" → client id/secret empty → region "global"
#   → advanced "n" → auto config "y" (browser login) → type "onedrive" → choose the drive → "y"
rclone lsf onedrive:BatteryIndex --config secrets/rclone/rclone.conf
```

`secrets/rclone/rclone.conf` contains the OAuth token. It is excluded from Git and from the image and is
mounted **writable** into the worker only (rclone refreshes the token). Never commit or share it.

A file is imported only when it has not changed during download; it is moved to `2_Spracované` only after
the data are committed. Network or authorisation errors leave the file in `1_Nahrať` for the next run.

---

## 7. Daily operation

The worker runs `python -m bri.pipeline --trigger cron` every day at **16:00 Europe/Bratislava**
(`docker/crontab`). A PostgreSQL advisory lock prevents two runs at the same time.

| Task | Command |
|---|---|
| Run the pipeline now | `docker compose exec worker python -m bri.pipeline` |
| Run with a specific file source | `docker compose exec worker python -m bri.pipeline --source local` |
| Recalculate a period | `docker compose exec worker python -m bri.pipeline --from 2026-01-01 --to 2026-01-31` |
| Recalculate without downloading | `docker compose exec worker python -m bri.pipeline --skip-api --source none` |
| Force recalculation | `docker compose exec worker python -m bri.pipeline --from 2026-01-01 --force-calc` |
| Worker log | `docker compose logs -f worker` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Back up the database | `docker compose exec db pg_dump -U bri battery_revenue_index > backup.sql` |

Each run downloads DA/IDM for incomplete and recent days, IMB until the final evaluation is published,
aFRR capacity from ENTSO-E, imports new SEPS files, recalculates changed days and publishes them.

---

## 8. Tests

The tests run in the worker image against a separate database (`bri_test`); the production database is
not touched. Recorded OKTE/ENTSO-E responses in `tests/fixtures/` make them independent of the network.

```bash
docker compose up -d db
docker compose exec db psql -U bri -d battery_revenue_index -c "CREATE DATABASE bri_test"
docker compose run --rm -e TEST_DATABASE_URL=postgresql://bri:<password>@db:5432/bri_test \
    worker python -m pytest tests
```

They cover native DAM intervals on the transition and DST days (30.03.2025, 30.09.2025, 01.10.2025,
26.10.2025), immutable revisions and failed imports, publication of exactly one result version,
recalculation on changed inputs, aFRR on 23/25-hour days, the trading model, the pipeline lock, OneDrive
handling (with a fake rclone), the local inbox and the ENTSO-E activation prices.

---

## 9. Checking that the system works

| Check | How | Expected |
|---|---|---|
| Services | `docker compose ps` | `db` healthy, `worker` and `dashboard` up |
| Schedule | `docker compose logs worker` | `read crontab: /app/docker/crontab` |
| Time zone | `docker compose exec worker date` | CET / CEST |
| Dashboard | <http://127.0.0.1:5050/> | source „Zverejnené výsledky (databáza)“, data until yesterday, „Aktualizované: …“ |
| Manual run | `docker compose exec worker python -m bri.pipeline` | ends with `pipeline finished: success` (or `partial` with listed reasons) |
| Nothing changed | run it again | `calculation tasks: 0` |
| Lock | start two runs at once | the second prints `another pipeline is running - exit` |
| File import | put a SEPS XLSX into the inbox, run the pipeline | report in `reports/local_inbox/` (local) or file in `2_Spracované` (OneDrive) |
| Bad file | put a non-Excel file named `test.xlsx` into the inbox | rejected with a reason, no data changed |
| Run log | `SELECT * FROM bri.pipeline_run ORDER BY 1 DESC LIMIT 5;` | status and messages of each run |

---

## 10. Database

Connect with any PostgreSQL client to `localhost:5432`, database `battery_revenue_index`, user/password from
`.env`, schema `bri`. Or use the console:

```bash
docker compose exec db psql -U bri -d battery_revenue_index
```

```sql
SET search_path = bri;

-- what the dashboard shows (1 h / 1 cycle)
SELECT market, round(sum(revenue_eur)::numeric) AS eur
FROM published_daily_total WHERE config_code = '1h_1c' GROUP BY market;

-- completeness of inputs
SELECT m.code, d.component, d.status, count(*)
FROM market_day d JOIN market m USING (market_id) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

-- calculation runs
SELECT run_id, model_version, trigger, status, started_at FROM calc_run ORDER BY run_id;

-- 15-minute DA prices on the first day of the 15-minute DAM
SELECT interval_start AT TIME ZONE 'Europe/Bratislava' AS local_time, price_eur_mwh
FROM price_da_current
WHERE interval_start >= '2025-09-30 22:00+00' AND interval_start < '2025-10-01 22:00+00';
```

The views `*_current` and `published_daily_total`, and the tables `schema_migration` and `pipeline_run`,
intentionally have no foreign keys.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `containers/create: EOF` (Windows) | create `secrets/rclone`, `incoming`, `reports/local_inbox` before starting; restart Docker Desktop |
| Git Bash turns `/app/...` into `C:/Program Files/Git/app/...` | `MSYS_NO_PATHCONV=1 docker compose ...` or PowerShell |
| `CERTIFICATE_VERIFY_FAILED` from ENTSO-E inside Docker | a TLS-inspecting proxy: put its CA bundle into `secrets/rclone/ca.pem` and add a `docker-compose.override.yml` with `SSL_CERT_FILE: /config/rclone/ca.pem` for `worker` and `migrate` |
| `ENTSOE_API_KEY nie je nastavený` | set the token in `.env`, `docker compose up -d worker` |
| OneDrive authorisation error in the log / `STAV.txt` | the token was revoked or expired: run `rclone config` again as the folder owner |
| `another pipeline is running` | another run (e.g. `migrate`) holds the lock; wait until it finishes |
| aFRR missing for recent days | no SEPS/Damas file for those days yet; upload it |

---

## 12. Project structure

| Path | Content |
|---|---|
| `bri/sql/001_schema.sql` | database schema |
| `bri/pipeline.py` | daily pipeline (`python -m bri.pipeline`) |
| `bri/importers/` | OKTE, ENTSO-E (capacity, activation prices), SEPS/Damas |
| `bri/local_inbox.py`, `bri/onedrive.py` | file sources |
| `bri/readiness.py`, `bri/models.py`, `bri/calc.py` | completeness, revenue models, runs and publication |
| `bri/migrate.py`, `bri/legacy.py` | one-off import of the legacy file data |
| `markets/`, `analysismodes/` | market models of the original index |
| `tools/slovakia_revenue_dashboard.py` | dashboard |
| `tools/upload_local_input.ps1` | safe copy of a SEPS file into the local inbox |
| `docker-compose.yml`, `Dockerfile`, `docker/crontab` | containers and schedule |
| `tests/` | automated tests and recorded API responses |
| `marketdata/`, `results/` | legacy file data used once by `migrate` |
| `docs/` | methodology of the original ISEA index |

## 13. License

GNU General Public License v3.0, see [LICENSE.txt](LICENSE.txt). Authors and data sources are listed on the
dashboard page `/about`. Market data belong to their publishers (OKTE, a.s., SEPS, a.s., ENTSO-E).
