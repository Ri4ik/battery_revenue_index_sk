# Battery Revenue Index SK

[English](README.md) · **Slovensky**

Index výnosov batériového úložiska (BESS) na slovenských trhoch s elektrinou, prevzatý z
[ISEA Battery Revenue Index](https://battery-charts.rwth-aachen.de/) (RWTH Aachen).

Systém sťahuje trhové dáta, ukladá ich do PostgreSQL vrátane pôvodu, počíta denné výnosy pre štyri
konfigurácie batérie a zverejňuje ich na webovom dashboarde. Všetko beží v Dockeri; kontajner `worker`
aktualizuje dáta každý deň o 16:00 (Europe/Bratislava).

| Trh | Popis | Zdroj |
|---|---|---|
| DA | Denný trh (do 30. 9. 2025 hodinový, od 1. 10. 2025 štvrťhodinový) | OKTE API |
| IDM15, IDM60 | Vnútrodenný trh, 15- a 60-minútové produkty | OKTE API |
| IMB | Zúčtovacia cena odchýlky (najlepšie z hodnotení final / monthly / daily) | OKTE API |
| aFRR | Kapacita (4-hodinové bloky, nahor/nadol) + aktivovaná energia | ENTSO-E API, SEPS/Damas XLSX |

Konfigurácie batérie: 1 MW s kapacitou 1 h alebo 2 h, 1 alebo 2 cykly za deň.

---

## Obsah

1. [Architektúra](#1-architektúra)
2. [Požiadavky](#2-požiadavky)
3. [Inštalácia](#3-inštalácia)
4. [Konfigurácia (`.env`)](#4-konfigurácia-env)
5. [Prvé spustenie](#5-prvé-spustenie)
6. [Vstupy SEPS/Damas (Excel)](#6-vstupy-sepsdamas-excel)
7. [Bežná prevádzka](#7-bežná-prevádzka)
8. [Testy](#8-testy)
9. [Kontrola funkčnosti](#9-kontrola-funkčnosti)
10. [Databáza](#10-databáza)
11. [Riešenie problémov](#11-riešenie-problémov)
12. [Štruktúra projektu](#12-štruktúra-projektu)
13. [Licencia](#13-licencia)

---

## 1. Architektúra

```text
 OKTE API ─┐                      ┌──────────────── Docker Compose ─────────────────┐
 ENTSO-E ──┼──────────────────────▶ worker (supercronic, denne 16:00)              │
           │                      │   python -m bri.pipeline                        │
 SEPS XLSX ┤  lokálny priečinok   │   1 zámok  2 súbory  3 API  4 úplnosť dní       │
  (ručne)  └─ alebo OneDrive      │   5 výpočet  6 zverejnenie  7 stavový report    │
                                  │                  │                              │
                                  │                  ▼                              │
                                  │   PostgreSQL (schéma bri) ◀── dashboard :5050   │
                                  └─────────────────────────────────────────────────┘
```

| Služba | Úloha |
|---|---|
| `db` | PostgreSQL 16, schéma `bri`: vstupy, úplnosť dní, výsledky, denníky |
| `worker` | spúšťa pipeline raz denne (jediný plánovač) |
| `dashboard` | webová aplikácia Flask/Gunicorn; číta iba zverejnené výsledky |
| `migrate` | jednorazová úloha (profil `jobs`): import pôvodných súborov a dopočet histórie |

**Dátový model v skratke** (`bri/sql/001_schema.sql`):

- každá vstupná hodnota má pôvod: `source_artifact` (nemenný pôvodný súbor alebo odpoveď API, archív
  `raw/<sha256>`) → `import_batch` (jeden pokus o import: running / success / failed);
- vstupy sú uložené po trhoch (`price_da`, `price_idm15`, `price_idm60`, `price_imb`, `afrr_capacity`,
  `afrr_energy`) ako **nemenné revízie** s pôvodným začiatkom a koncom intervalu; pohľady `*_current`
  vracajú najnovšiu úspešnú hodnotu;
- `market_day` uchováva, či je deň trhu úplný (intervaly pokrývajú miestny deň, aj pri zmene času);
- každý výpočet je `calc_run` s presným snímkom vstupov (`calc_input`); výsledky všetkých behov sa
  uchovávajú (`result_daily`, `result_interval`) a `result_publication` určuje jedinú verziu zobrazenú na
  dashboarde (pohľad `published_daily_total`).

Deň sa prepočíta iba vtedy, keď sa zmení snímok jeho vstupov alebo verzia modelu.
aFRR sa počíta iba vtedy, keď sú za deň úplné kapacita aj energia.

---

## 2. Požiadavky

| Softvér | Poznámka |
|---|---|
| Docker Desktop (Windows/macOS) alebo Docker Engine + Compose v2 (Linux) | všetky časti bežia v kontajneroch |
| Git | stiahnutie repozitára |
| Prístup na internet | API OKTE a ENTSO-E, zostavenie obrazu (pip, rclone, supercronic) |
| Token API ENTSO-E Transparency Platform | kapacita aFRR a ceny aktivácie ([ako ho získať](https://transparency.entsoe.eu/content/static_content/Static%20content/web%20api/Guide.html#_authentication_and_authorisation)) |
| ~3 GB voľného miesta | obraz ~0,7 GB, databáza ~1 GB po úplnom dopočte |

Voliteľne: [DBeaver](https://dbeaver.io/) alebo iný klient PostgreSQL na prezeranie databázy;
[rclone](https://rclone.org/) iba ak sa súbory SEPS dodávajú cez OneDrive.

---

## 3. Inštalácia

```bash
git clone -b feature/db-pipeline https://github.com/Ri4ik/battery_revenue_index_sk.git
cd battery_revenue_index_sk

cp .env.example .env                       # potom upraviť, pozri časť 4
mkdir -p secrets/rclone incoming reports/local_inbox
docker compose build
```

> **Windows:** priečinky vyššie vytvorte **pred** prvým príkazom `docker compose`. Docker Desktop môže skončiť
> chybou `containers/create: EOF`, keď musí pripájaný priečinok vytvoriť sám, najmä ak cesta obsahuje iné ako
> latinské znaky. V Git Bash pridajte pred príkazy s cestami v kontajneri (`/app/...`) `MSYS_NO_PATHCONV=1`,
> alebo použite PowerShell.

---

## 4. Konfigurácia (`.env`)

Súbor `.env` sa nesleduje v gite. Vychádzajte z `.env.example`:

| Premenná | Význam | Príklad |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | prístup do databázy (dostupná iba z `127.0.0.1:5432`) | `bri` / *vaše heslo* / `battery_revenue_index` |
| `ENTSOE_API_KEY` | token ENTSO-E; bez neho sa dáta aFRR nesťahujú | |
| `BRI_FILE_SOURCE` | odkiaľ prichádzajú súbory SEPS: `local`, `onedrive` alebo `none` | `local` |
| `BRI_INBOX_HOST` | priečinok na hostiteľovi pre súbory SEPS (lokálny zdroj) | `./incoming` |
| `BRI_REPORTS_HOST` | priečinok pre reporty importu (lokálny zdroj) | `./reports/local_inbox` |
| `BRI_LOCAL_MIN_AGE_SECONDS` | súbor musí byť toľko sekúnd nezmenený, kým sa spracuje | `10` |
| `BRI_DELETE_AFTER_SUCCESS` | `0` súbory ponechá (presunie do `incoming/processed/`); `1` ich po overenom zverejnení zmaže **vrátane kópií v archíve raw** | `0` |
| `ONEDRIVE_REMOTE` | rclone remote priečinka OneDrive (iba pre zdroj OneDrive) | `onedrive:BatteryIndex` |

---

## 5. Prvé spustenie

```bash
docker compose up -d db                    # databáza
docker compose run --rm migrate            # jednorazovo: pôvodné dáta + dopočet od 1. 3. 2025 (~40 min)
docker compose up -d                       # worker + dashboard
```

`migrate` (opakovateľne, bez duplicít):

1. vytvorí schému a štyri konfigurácie batérie;
2. naimportuje pôvodné súbory z repozitára (CSV kapacity aFRR, pôvodné XLSX SEPS, výsledky JSON ako
   beh `legacy-json-v1`);
3. znovu stiahne DA, IDM15, IDM60 a IMB z API OKTE s pôvodnými intervalmi, skontroluje úplnosť a prepočíta
   všetky dni aktuálnym modelom; tieto výsledky nahradia pôvodné všade, kde sú vstupy úplné.

Dashboard: **<http://127.0.0.1:5050/>**.

---

## 6. Vstupy SEPS/Damas (Excel)

Aktivovaná energia aFRR nie je v potrebnej podobe dostupná cez verejné API. Nahráva sa ako export
SEPS/Damas **„Regulačná elektrina (denná)“** (XLSX, hárok `aFRR`). Prijímajú sa dva formáty:

- **export s cenami** (7 alebo 9 stĺpcov): dátum, hodina, štvrťhodina, objem nahor, cena nahor, objem nadol,
  cena nadol;
- **export iba s objemami** (5 stĺpcov): dátum, hodina, štvrťhodina, objem nahor, objem nadol — príslušné
  ceny aktivácie sa automaticky stiahnu z ENTSO-E (dokument A84).

Každý deň v súbore musí obsahovať všetky štvrťhodiny miestneho dňa (92, 96 alebo 100).

### 6.1 Lokálny priečinok (`BRI_FILE_SOURCE=local`, predvolené)

Súbor skopírujte do `incoming/` (vo Windows najlepšie pomocným skriptom, ktorý kopíruje atomicky a overí
kontrolný súčet):

```powershell
.\tools\upload_local_input.ps1 -Path "C:\cesta\k\seps_export.xlsx"
```

Najbližší beh pipeline ho naimportuje (alebo ho spustite hneď, pozri časť 7). Report (JSON + TXT) sa zapíše
do `reports/local_inbox/`. Naimportované súbory sa presunú do `incoming/processed/`, nepoužiteľné do
`incoming/errors/`. Ak ceny z ENTSO-E ešte nie sú k dispozícii, súbor čaká v `incoming/.processing/`
a ďalší beh pokračuje.

### 6.2 OneDrive (`BRI_FILE_SOURCE=onedrive`)

Priečinok OneDrive Personal samostatného projektového konta, ku ktorému worker pristupuje cez rclone:

```text
BatteryIndex/
├── 1_Nahrať/       sem sa nahrávajú XLSX SEPS
├── 2_Spracované/   naimportované súbory (presunuté po zápise do databázy)
├── 3_Chyby/        odmietnuté súbory + <súbor>.chyba.txt s dôvodom
└── STAV.txt        stav zapisovaný každým behom
```

Jednorazová autorizácia na počítači s prehliadačom (prihlásiť sa ako vlastník priečinka):

```bash
rclone config --config secrets/rclone/rclone.conf
#   n → názov "onedrive" → storage "onedrive" → client id/secret prázdne → region "global"
#   → advanced "n" → auto config "y" (prihlásenie v prehliadači) → typ "onedrive" → vybrať disk → "y"
rclone lsf onedrive:BatteryIndex --config secrets/rclone/rclone.conf
```

`secrets/rclone/rclone.conf` obsahuje token OAuth. Je vylúčený z gitu aj z obrazu a do kontajnera `worker`
sa pripája **na zápis** (rclone token obnovuje). Nikdy ho necommitujte ani nezdieľajte.

Súbor sa naimportuje iba vtedy, ak sa počas sťahovania nezmenil; do `2_Spracované` sa presunie až po
zápise dát do databázy. Pri chybe siete alebo autorizácie zostane v `1_Nahrať` na ďalší beh.

---

## 7. Bežná prevádzka

Worker spúšťa `python -m bri.pipeline --trigger cron` každý deň o **16:00 Europe/Bratislava**
(`docker/crontab`). Zámok v PostgreSQL zabráni dvom súčasným behom.

| Úloha | Príkaz |
|---|---|
| Spustiť pipeline hneď | `docker compose exec worker python -m bri.pipeline` |
| Spustiť s konkrétnym zdrojom súborov | `docker compose exec worker python -m bri.pipeline --source local` |
| Prepočítať obdobie | `docker compose exec worker python -m bri.pipeline --from 2026-01-01 --to 2026-01-31` |
| Prepočet bez sťahovania | `docker compose exec worker python -m bri.pipeline --skip-api --source none` |
| Vynútený prepočet | `docker compose exec worker python -m bri.pipeline --from 2026-01-01 --force-calc` |
| Log workera | `docker compose logs -f worker` |
| Zastaviť / spustiť | `docker compose down` / `docker compose up -d` |
| Záloha databázy | `docker compose exec db pg_dump -U bri battery_revenue_index > backup.sql` |

Každý beh stiahne DA/IDM pre neúplné a posledné dni, IMB kým nie je zverejnené hodnotenie final, kapacitu
aFRR z ENTSO-E, naimportuje nové súbory SEPS, prepočíta zmenené dni a zverejní ich.

---

## 8. Testy

Testy bežia v obraze workera proti samostatnej databáze (`bri_test`); produkčná databáza sa nemení.
Uložené odpovede OKTE/ENTSO-E v `tests/fixtures/` ich robia nezávislými od siete.

```bash
docker compose up -d db
docker compose exec db psql -U bri -d battery_revenue_index -c "CREATE DATABASE bri_test"
docker compose run --rm -e TEST_DATABASE_URL=postgresql://bri:<heslo>@db:5432/bri_test \
    worker python -m pytest tests
```

Pokrývajú pôvodné intervaly DAM v dňoch prechodu a zmeny času (30. 3. 2025, 30. 9. 2025, 1. 10. 2025,
26. 10. 2025), nemenné revízie a neúspešné importy, zverejnenie jedinej verzie výsledku, prepočet pri zmene
vstupov, aFRR v 23/25-hodinových dňoch, obchodný model, zámok pipeline, spracovanie OneDrive (s falošným
rclone), lokálny priečinok a ceny aktivácie z ENTSO-E.

---

## 9. Kontrola funkčnosti

| Kontrola | Ako | Očakávané |
|---|---|---|
| Služby | `docker compose ps` | `db` healthy, `worker` a `dashboard` up |
| Plán | `docker compose logs worker` | `read crontab: /app/docker/crontab` |
| Časové pásmo | `docker compose exec worker date` | CET / CEST |
| Dashboard | <http://127.0.0.1:5050/> | zdroj „Zverejnené výsledky (databáza)“, dáta do včera, „Aktualizované: …“ |
| Ručný beh | `docker compose exec worker python -m bri.pipeline` | končí `pipeline finished: success` (alebo `partial` s uvedenými dôvodmi) |
| Bez zmien | spustiť znova | `calculation tasks: 0` |
| Zámok | spustiť dva behy naraz | druhý vypíše `another pipeline is running - exit` |
| Import súboru | vložiť XLSX SEPS do priečinka a spustiť pipeline | report v `reports/local_inbox/` (lokálne) alebo súbor v `2_Spracované` (OneDrive) |
| Chybný súbor | vložiť súbor, ktorý nie je Excel, pod názvom `test.xlsx` | odmietnutý s dôvodom, dáta sa nezmenia |
| Denník behov | `SELECT * FROM bri.pipeline_run ORDER BY 1 DESC LIMIT 5;` | stav a hlásenia každého behu |

---

## 10. Databáza

Pripojte sa ľubovoľným klientom PostgreSQL na `localhost:5432`, databáza `battery_revenue_index`,
používateľ/heslo z `.env`, schéma `bri`. Alebo cez konzolu:

```bash
docker compose exec db psql -U bri -d battery_revenue_index
```

```sql
SET search_path = bri;

-- čo zobrazuje dashboard (1 h / 1 cyklus)
SELECT market, round(sum(revenue_eur)::numeric) AS eur
FROM published_daily_total WHERE config_code = '1h_1c' GROUP BY market;

-- úplnosť vstupov
SELECT m.code, d.component, d.status, count(*)
FROM market_day d JOIN market m USING (market_id) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

-- behy výpočtu
SELECT run_id, model_version, trigger, status, started_at FROM calc_run ORDER BY run_id;

-- 15-minútové ceny DA v prvý deň 15-minútového DAM
SELECT interval_start AT TIME ZONE 'Europe/Bratislava' AS miestny_cas, price_eur_mwh
FROM price_da_current
WHERE interval_start >= '2025-09-30 22:00+00' AND interval_start < '2025-10-01 22:00+00';
```

Pohľady `*_current` a `published_daily_total` a tabuľky `schema_migration` a `pipeline_run` zámerne nemajú
cudzie kľúče.

---

## 11. Riešenie problémov

| Príznak | Riešenie |
|---|---|
| `containers/create: EOF` (Windows) | pred spustením vytvoriť `secrets/rclone`, `incoming`, `reports/local_inbox`; reštartovať Docker Desktop |
| Git Bash mení `/app/...` na `C:/Program Files/Git/app/...` | `MSYS_NO_PATHCONV=1 docker compose ...` alebo PowerShell |
| `CERTIFICATE_VERIFY_FAILED` z ENTSO-E v Dockeri | proxy kontrolujúca TLS: certifikát CA do `secrets/rclone/ca.pem` a `docker-compose.override.yml` s `SSL_CERT_FILE: /config/rclone/ca.pem` pre `worker` a `migrate` |
| `ENTSOE_API_KEY nie je nastavený` | nastaviť token v `.env`, `docker compose up -d worker` |
| Chyba autorizácie OneDrive v logu / `STAV.txt` | token bol odvolaný alebo vypršal: znova `rclone config` ako vlastník priečinka |
| `another pipeline is running` | iný beh (napr. `migrate`) drží zámok; počkať na jeho koniec |
| Chýba aFRR za posledné dni | za tieto dni ešte nie je súbor SEPS/Damas; nahrať ho |

---

## 12. Štruktúra projektu

| Cesta | Obsah |
|---|---|
| `bri/sql/001_schema.sql` | schéma databázy |
| `bri/pipeline.py` | denný proces (`python -m bri.pipeline`) |
| `bri/importers/` | OKTE, ENTSO-E (kapacita, ceny aktivácie), SEPS/Damas |
| `bri/local_inbox.py`, `bri/onedrive.py` | zdroje súborov |
| `bri/readiness.py`, `bri/models.py`, `bri/calc.py` | úplnosť dní, modely výnosov, behy a zverejnenie |
| `bri/migrate.py`, `bri/legacy.py` | jednorazový import pôvodných súborových dát |
| `markets/`, `analysismodes/` | trhové modely pôvodného indexu |
| `tools/slovakia_revenue_dashboard.py` | dashboard |
| `tools/upload_local_input.ps1` | bezpečné kopírovanie súboru SEPS do lokálneho priečinka |
| `docker-compose.yml`, `Dockerfile`, `docker/crontab` | kontajnery a plán |
| `tests/` | automatické testy a uložené odpovede API |
| `marketdata/`, `results/` | pôvodné súborové dáta, ktoré jednorazovo použije `migrate` |
| `docs/` | metodika pôvodného indexu ISEA |

## 13. Licencia

GNU General Public License v3.0, pozri [LICENSE.txt](LICENSE.txt). Autori a zdroje dát sú uvedení na stránke
dashboardu `/about`. Trhové dáta patria ich vydavateľom (OKTE, a.s., SEPS, a.s., ENTSO-E).
