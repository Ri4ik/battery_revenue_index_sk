# Battery Revenue Index (Slovensko)

Projekt je zamerany na vypocet vynosov baterioveho uloziska (BESS) pre slovenske trhy a ich vizualizaciu vo webovom dashborde.

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

## Rychly start (Windows)

### 1) Instalacia zavislosti

```powershell
cd "c:\Users\Даниил Бережной\Downloads\battery_revenue_index-main"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2) Priprava dat

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

### 3) Spustenie vypoctu

```powershell
.\.venv\Scripts\python.exe calculation_config_slovakia.py
```

Vysledky sa ulozia do priecinka `results/Slovakia_...`.

### 4) Spustenie webu (dashboard)

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