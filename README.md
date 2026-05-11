# Battery Revenue Index (Slovensko)

Projekt je zamerany na vypocet vynosov baterioveho uloziska (BESS) pre slovenske trhy a na vizualizaciu vysledkov vo webovom dashboarde.

## Co je aktualne implementovane

- Vypocet v rezime **single-market** pre trhy: `DA`, `IDA1`, `ID1`, `IMB`, `FCR`, `aFRR`.
- Import a normalizacia vstupnych dat OKTE/SEPS do interneho formatu.
- Ukladanie vysledkov po dnoch do `results/.../*.json`.
- Webovy dashboard vo Flasku (`tools/slovakia_revenue_dashboard.py`) s filtrami:
  - obdobie dat,
  - vyber trhov (checkbox),
  - typ grafu (stlpce/ciary),
  - agregacia (`Absolutne hodnoty`, `30-dnovy priemer`, `365-dnovy priemer`, `Anualizovane`),
  - volitelna kumulativna krivka.

## Spustenie projektu (krok za krokom)

### 1) Stiahnutie projektu z GitHubu

```bash
git clone https://github.com/Ri4ik/battery_revenue_index_sk.git
cd battery_revenue_index_sk
```

### 2) Vytvorenie virtualneho prostredia a instalacia balikov

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 3) Priprava vstupnych dat

Moznost A (jednym prikazom, ak pouzivate pripraveny import):

```powershell
.\run_slovakia_import.ps1
```

Moznost B (rucna konverzia CSV/XLSX suborov):

```powershell
.\.venv\Scripts\python.exe tools\slovakia_data_adapter.py `
  --workspace . `
  --day 2025-03-01 `
  --da-file raw/okte_da_2025-03-01.csv `
  --id1-file raw/okte_id1_2025-03-01.csv `
  --ida1-file raw/okte_ida1_2025-03-01.csv `
  --imb-file raw/okte_imb_2025-03-01.csv `
  --fcr-file raw/seps_fcr_2025-03-01.csv `
  --afrr-cap-file raw/seps_afrr_capacity_2025-03-01.csv
```

## Prirucka: nacitanie dat z OKTE

Pre import OKTE dat sa pouziva skript `tools/import_okte_exports.py`.

### Najcastejsi priklad (DAM + IDM + IMB API)

```powershell
.\.venv\Scripts\python.exe tools\import_okte_exports.py `
  --workspace . `
  --dam-overview "raw/Overwiev_DAM_2025-03-01_2026-03-01.csv" `
  --idm-15min "raw/IDM_15min_2025-03-01_2026-03-01.csv" `
  --date-from 2025-03-01 `
  --date-to 2026-03-01 `
  --fetch-system-imbalance `
  --fetch-demand-supply
```

### Co znamena kazdy parameter

- `--workspace`  
  Koreň projektu, kam sa zapisuju subory `marketdata/...`.

- `--dam-overview`  
  Vstupny CSV export z OKTE pre day-ahead (DAM). Vysledok ide do `marketdata/DA/DA_YYYY-MM-DD.csv`.

- `--idm-15min`  
  Vstupny 15-min export z OKTE intraday.  
  Skript z neho zapisuje:
  - `ID1` do `marketdata/ID1/ID1_YYYY-MM-DD.csv`,
  - `IDA1` fallback do `marketdata/IDA1/IDA1 YYYY-MM-DD.csv`.

- `--date-from`, `--date-to`  
  Datumovy rozsah pre API volania (IMB a demand/supply).

- `--fetch-system-imbalance`  
  Stiahne IMB ceny z OKTE API (`SystemImbalance`) a zapise ich do `marketdata/IMB`.

- `--system-imbalance-evaluation-type`  
  Typ vyhodnotenia pre IMB API (default: `final`).

- `--system-imbalance-price-field`  
  Cenove pole z API (default: `isp`). Menit iba ak vies, preco.

- `--fetch-demand-supply`  
  Volitelny export doplnkovych API dat (DemandSupplyBalance) do `marketdata/OKTE/...`.

- `--use-idm-as-imb-proxy`  
  Nudzovy fallback: prepise IMB z IDM suboru. Pouzivaj iba ked IMB API nie je dostupne.

### Odporucany postup pre projekt

1. Naimportuj DAM + IDM (`--dam-overview`, `--idm-15min`).
2. Dotiahni IMB z API (`--fetch-system-imbalance`) pre cely rozsah.
3. Over, ze v `marketdata/IMB` mas subory pre kazdy den.
4. Az potom spusti vypocet `calculation_config_slovakia_okte_only.py`.

### 4) Spustenie vypoctu

```powershell
.\.venv\Scripts\python.exe calculation_config_slovakia.py
```

Vysledky sa ulozia do priecinka `results/Slovakia_...`.

Pre rychlejsi vypocet OKTE-only variantu pouzite paralelne jadra:

```powershell
.\.venv\Scripts\python.exe calculation_config_slovakia_okte_only.py --start-day 2025-03-01 --end-day 2026-03-01 --workers 8
```

Ak parameter `--workers` neuvediete, skript automaticky pouzije rozumny pocet jadier.

### 5) Spustenie dashboardu

```powershell
.\run_slovakia_dashboard.ps1
```

Potom otvorte v prehliadaci: `http://127.0.0.1:5050/`

## Hlavne skripty

- `calculation_config_slovakia.py` - hlavny vypocet slovenskeho benchmarku.
- `tools/slovakia_data_adapter.py` - konverzia surovych dat do `marketdata/`.
- `run_slovakia_import.ps1` - import dat (ak pouzivate automaticky pipeline).
- `tools/slovakia_revenue_dashboard.py` - Flask aplikacia pre vizualizaciu.
- `run_slovakia_dashboard.ps1` - pohodlne spustenie dashboardu.

## Zdroje dat

- OKTE Day-ahead: <http://www.okte.sk/en/short-term-market/published-information-of-dam/day-ahead-detailed-overview/>
- OKTE Intraday: <http://www.okte.sk/en/short-term-market/published-information-of-idm/intraday-detailed-overview/>
- OKTE Imbalance: <http://www.okte.sk/en/imbalance-settlement/published-information/demand-supply-balance/>
- SEPS systemove sluzby: <https://www.sepsas.sk/en/services/system-services/>

## Najcastejsie problemy

- Ak `run_slovakia_dashboard.ps1` zlyha pri VS Code debug scenari s chybou `ETIMEDOUT ... pipe\\PSE...`, spustite dashboard priamo:

```powershell
.\.venv\Scripts\python.exe tools\slovakia_revenue_dashboard.py --no-browser
```

- Ak graf neukazuje cely rok, vo vybranom priecinku `results/...` nie su data za cely rok. Vyberte iny priecinok alebo najprv dopocitajte chybajuce obdobie.

## Licencia

GNU GPL, pozri `LICENSE`.