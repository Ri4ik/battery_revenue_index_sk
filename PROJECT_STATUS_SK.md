# Stav projektu (Slovensko)

## Aktualny stav projektu

Projekt je adaptovany na slovenske data a aktualne pocita prakticky pouzitelny
single-market benchmark pre tieto hlavne trhy:

- `DA` - day-ahead cena z OKTE,
- `IDM15` - intraday continuous/index z OKTE IDM 15-min, povodne `ID1`,
- `IDM60` - intraday continuous/index z OKTE IDM 60-min, importovany z OKTE API,
- `IMB` - imbalance cena z OKTE SystemImbalance API,
- `aFRR` - kombinacia aFRR Capacity + aFRR Energy.

Hlavny aktualne pouzitelny rocny vysledok je:

- `results/Slovakia_2025-2026 (01.03)`
- obdobie: `2025-03-01` az `2026-03-01`
- nove trhy pre dashboard: `DA`, `IDM15`, `IDM60`, `IMB`, `aFRR`
- legacy vysledky `ID1` a `IDA1` este zostavaju v priecinku kvoli spätnej kompatibilite,
  ale dashboard ich uz standardne nezobrazuje.

Sucty denných vynosov v aktualnom rocniku:

- `DA`: 37 610.87 EUR
- `IDM15`: 55 582.88 EUR
- `IDM60`: 43 574.60 EUR
- `IMB`: 114 381.29 EUR
- `aFRR`: 322 281.78 EUR
  - z toho `aFRR Capacity`: 106 806.59 EUR
  - z toho `aFRR Energy`: 215 475.19 EUR

## Aktualne datove zdroje

### OKTE

- OKTE Day-Ahead export (`Overwiev_DAM_...csv`) ->
  `marketdata/DA/DA_YYYY-MM-DD.csv`
- OKTE IDM 15-min export / pripravene OKTE data ->
  `marketdata/IDM15/IDM15_YYYY-MM-DD.csv`
- OKTE IDM Results API, `productType=60` ->
  `marketdata/IDM60/IDM60_YYYY-MM-DD.csv`
- OKTE SystemImbalance API ->
  `marketdata/IMB/IMB_YYYY-MM-DD.csv`
- Raw OKTE IDM60 API export:
  `marketdata/OKTE/idm_results_product60_2025-03-01_2026-03-01.csv`

Poznamka k premenovaniu:

- stare `ID1` je teraz projektovo `IDM15`,
- stare `IDA1` uz nepouzivame ako fallback z 15-min dat,
- namiesto toho je pridany novy `IDM60` z OKTE intraday continuous/index 60-min.

### aFRR

- aFRR Capacity je importovana z ENTSO-E `procured_balancing_capacity`
  do `marketdata/aFRR_capacity/afrr_capacity_YYYY-MM-DD.csv`.
- aFRR Energy je importovana z SEPS/Damas exportov
  `Regulacna elektrina (denna)` do:
  - `marketdata/SepsDamasEnergy/raw/`
  - `marketdata/SepsDamasEnergy/normalized/`
  - `marketdata/SepsDamasEnergy/daily/`
- Aktualny SEPS/Damas energy dataset pokryva `2025-01-01` az `2026-05-26`
  a obsahuje aj subory `07` a `08` pre rok 2026.

## Co je implementovane

- Import OKTE IDM 60-min cez API v `tools/import_okte_exports.py`
  (`--fetch-idm-results --idm-product-types 60`).
- Podpora novych trhov `IDM15` a `IDM60` v:
  - `analysismodes/single_market_analysis.py`
  - `markets/wholesale_market.py`
  - `tools/validate_marketdata.py`
  - `tools/slovakia_revenue_dashboard.py`
  - `calculation_config_slovakia.py`
  - `calculation_config_slovakia_okte_only.py`
- Rychly intraday vypocet pre `IDM15/IDM60`, aby rocny prepocet netrval hodiny.
- Dashboard zobrazuje defaultne `DA`, `IDM15`, `IDM60`, `IMB`, `aFRR`.
- aFRR Energy sa pocita zo SEPS/Damas aktivacii a cien s limitom podla
  dostupnej bateriovej vykonovej kapacity v kazdom 15-min intervale.
- SEPS/Damas aFRR Energy import nahradza extremne neplatne cenove odlahle
  hodnoty s `abs(price) > 10000 EUR/MWh` interpolaciou v ramci dna.

## Nakolko to funguje korektne

### Co funguje dobre

- Kompletny tok: import dat -> validacia -> vypocet -> JSON vysledky -> dashboard.
- Rocny vysledok pre `2025-03-01` az `2026-03-01`.
- Validacia vstupnych dat pre `DA`, `IDM15`, `IDM60`, `IMB`, `aFRR`.
- `IDM60` je skutocne oddeleny zdroj z OKTE API, nie fallback z `IDM15`.
- aFRR uz nie je iba testovaci den: v hlavnom rocniku je kompletna
  kombinacia Capacity + Energy.

### Aktualne limity korektnosti

- `IDM15` je projektove premenovanie stareho `ID1`; historicke vypocty boli
  prenesene tak, aby sa samotna hodnota nemenila len premenovanim.
- `IDM60` je 60-min index rozbaleny na 15-min casovu os pre kompatibilitu
  s bateriovym modelom. V kazdej hodine su preto styri rovnake ceny.
- `IMB` treba este samostatne auditovat na dni, kde mohli v starsich suboroch
  ostat fallback/proxy hodnoty.
- `FCR` a `IDC` nie su plnohodnotne rocne integrovane v hlavnom dashboarde.
- Cross-market strategia ako v nemeckej verzii este nie je korektne
  reprodukovana pre Slovensko.

## Co treba este dokoncit

1. **Docistit legacy `ID1/IDA1`**
   Rozhodnut, ci stare subory a JSON vysledky ponechat len ako archiv, alebo ich
   po migracii odstranit z pracovnych priecinkov.

2. **Dorefreshovat a skontrolovat `IMB`**
   Overit dni, kde mohol byt pouzity fallback/proxy, a spravit report kvality.

3. **Doplnit FCR**
   Najst alebo pripravit stabilny zdroj rocnych FCR dat pre Slovensko.

4. **Rozhodnut o IDC**
   Overit, ci existuje dostatocne detailny verejny zdroj transakcii alebo indexu
   pre korektny slovensky IDC vypocet.

5. **Formalizovat quality checks**
   Automaticke kontroly:
   - chybajuce dni/trhy,
   - nespravny pocet bodov (`DA` 24, `IDM15/IDM60/IMB` 96),
   - `NaN` ceny,
   - fallback/proxy dni,
   - DST dni s ocakavanym poctom bodov pri zdrojoch, kde to dava zmysel.

## Zaver

Slovenska verzia uz nie je iba OKTE-only prototyp. Aktualne ma rocny dashboard
pre `DA`, `IDM15`, `IDM60`, `IMB` a `aFRR`, pricom `aFRR` obsahuje Capacity aj
Energy cast. Najdolezitejsie dalsie prace su audit `IMB`, rozhodnutie o legacy
`ID1/IDA1`, doplnenie FCR/IDC a dalsie quality reporty.
