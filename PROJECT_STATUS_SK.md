# Stav projektu (Slovensko)

## Aktualny stav projektu

Projekt je adaptovany na slovenske data a aktualne stabilne pocita prakticky pouzitelny index v rezime **single-market OKTE-only** pre trhy:

- `DA` (Day-Ahead),
- `ID1` (intraday continuous/index z OKTE IDM 15-min),
- `IDA1` (zatial fallback z rovnakeho OKTE IDM 15-min zdroja),
- `IMB` (primarne z OKTE SystemImbalance API, s ciastocnym fallback/proxy stavom v starsich suboroch).

Hlavny aktualne pouzitelny rocny vysledok je:

- `results/Slovakia_2025-2026 (01.03)`
- obdobie: `2025-03-01` az `2026-03-01`
- trhy vo vysledkoch: `DA`, `ID1`, `IDA1`, `IMB`
- pocet JSON vysledkov: 1464 suborov

Vstupne OKTE data su lokalne pripravene vo vacsom rozsahu:

- `marketdata/DA`: 396 dni (`2025-03-01` az `2026-03-31`)
- `marketdata/ID1`: 396 dni (`2025-03-01` az `2026-03-31`)
- `marketdata/IDA1`: 396 dni (`2025-03-01` az `2026-03-31`)
- `marketdata/IMB`: 396 dni (`2025-03-01` az `2026-03-31`)

Webovy dashboard (`tools/slovakia_revenue_dashboard.py`) zobrazuje JSON vysledky z `results/...` a podporuje filtre podla obdobia, trhov, typu grafu a agregacie (`absolute`, `30-day average`, `365-day average`, `annualized`). Dashboard vie spustit aj novy prepocet pre OKTE-only trhy (`DA`, `IDA1`, `ID1`, `IMB`).

## Nakolko to funguje korektne

### Co funguje dobre

- Kompletny tok pre OKTE-only vetvu: import dat -> validacia -> vypocet -> JSON vysledky -> dashboard.
- Kontrola celistvosti vstupnych radov (`DA`, `ID1`, `IDA1`, `IMB`) pred vypoctom.
- Rocny single-market vysledok pre `2025-03-01` az `2026-03-01`.
- Paralelny vypocet v `calculation_config_slovakia_okte_only.py` cez `--workers`.
- Pokracovanie vo vypocte (`--resume`) bez opakovaneho prepocitavania hotovych dni.
- Opravene problemy pri prechodoch letneho/zimneho casu (DST), ktore predtym zastavovali vypocet pre konkretne datumy.

### Aktualne limity korektnosti

- `ID1` a `IDA1` su v aktualnom datasete totozne na vsetkych 396 dostupnych dnoch. Dovod: `IDA1` sa zatial generuje ako fallback z rovnakeho OKTE IDM 15-min suboru ako `ID1`.
- `IMB` je vacsinovo oddelene od `ID1`, ale v aktualnom datasete je este 31 dni, kde `IMB == ID1`. To ukazuje na zostatok fallback/proxy logiky alebo starsie opravene subory, ktore treba refreshnut a skontrolovat.
- `FCR`, `aFRR capacity`, `aFRR energy` a `IDC` su v kode a ciastocne vo formatoch pripravene, ale lokalne kompletne data existuju len pre testovaci den `2025-03-01`.
- Plna nemecka cross-market metodika zatial nie je pre Slovensko korektne reprodukovatelna, lebo chyba spolahlive rocne pokrytie rezervnych a detailnych intraday/activation dat.

## Aktualne datove zdroje

### Pouzivane v hlavnom OKTE-only vypocte

- OKTE Day-Ahead export (`Overwiev_DAM_...csv`) -> `marketdata/DA/DA_YYYY-MM-DD.csv`
- OKTE IDM 15-min export -> `marketdata/ID1/ID1_YYYY-MM-DD.csv`
- OKTE IDM 15-min export ako fallback -> `marketdata/IDA1/IDA1 YYYY-MM-DD.csv`
- OKTE SystemImbalance API -> `marketdata/IMB/IMB_YYYY-MM-DD.csv`

### Ciastocne pripravene / testovacie

- SEPS/FCR testovaci den -> `marketdata/FCR/FCR_2025-03-01.csv`
- aFRR capacity testovaci den -> `marketdata/aFRR_capacity/afrr_capacity_2025-03-01.csv`
- aFRR energy merit order a activation testovaci den -> `marketdata/aFRR_energy/*_2025-03-01.csv`
- IDC transactions testovaci den -> `marketdata/IDC/transactions_2025-03-01.csv`

## Co treba este dokoncit

1. **Oddelit `IDA1` a `ID1` na urovni zdrojov**  
   Je potrebny samostatny spolahlivy zdroj alebo export priamo pre `IDA1`, nie fallback z jedneho OKTE IDM 15-min suboru.

2. **Dorefreshovat a skontrolovat `IMB`**  
   Treba odstranit zostavajuce dni, kde `IMB == ID1`, ak nejde o realnu zhodu trhu. Prakticky to znamena znovu natiahnut `IMB` z OKTE SystemImbalance API a ulozit report dni, ktore boli fallback/proxy.

3. **Dokoncit rocne pokrytie `FCR` a `aFRR capacity`**  
   Treba denne SEPS data v stabilnom formate za cele obdobie a potom hromadny import, validaciu a porovnanie s metodikou ISEA.

4. **Rozhodnut o `aFRR energy` a `IDC` pre Slovensko**  
   Pre nemecky index tieto casti stoja na detailnych merit order, activation a transaction datach. Pre Slovensko treba potvrdit, ci su verejne dostupne v dostatocnej kvalite a granularite.

5. **Formalizovat quality checks**  
   Automaticke kontroly:
   - kde `ID1 == IDA1`,
   - kde `IMB == ID1`,
   - kde chybaju dni/trhy,
   - kde ma subor nespravny pocet bodov (`DA` 24, `ID1/IDA1/IMB` 96),
   - kde su podozrive interpolacie alebo fallbacky.

6. **Zlepsit odolnost importu pre produkcne pouzitie**  
   Lepsie logovanie, retry mechanizmy API volani, jasne reporty o chybajucich/problemovych dnoch a oddelene oznacenie suborov vytvorenych fallbackom.

## Preco sa pre Slovensko nepodarilo 1:1 implementovat vsetky trhy ako v nemeckej verzii

Strucne: **limit nie je v matematike modelu, ale v dostupnosti a strukture vstupnych dat**.

Pre nemecku verziu ([ISEA Revenue Index](https://battery-charts.de/revenue-index/#daily-revenues)) existuje mature a unifikovany datapipeline napriec trhmi, vratane dat pre komplexne kombinovane strategie, detailnejsie intraday modelovanie, FCR/aFRR a cross-market logiku.

Pre slovensku adaptaciu plati:

- data su publikovane v inom rozsahu a s inou hlbkou,
- pri niektorych trhoch chyba ekvivalentna otvorena detailnost za cele obdobie,
- formaty exportov nie su vzdy stabilne a vyzaduju fallback/proxy pristupy,
- cast nemeckych modulov, najma `IDC`, `aFRR Energy` a `Cross-Market`, sa neda korektne reprodukovat bez dalsich spolahlivych operatorovych alebo komercnych zdrojov.

## Zaver

Slovenska verzia je aktualne **prakticky pouzitelny OKTE-only benchmark** pre `DA`, `ID1`, `IDA1` a `IMB` na zaklade dostupnych verejnych dat. Nie je to zatial plna funkcna kopia nemeckeho ISEA indexu pre vsetky trhy a rezimy.

Najblizsie technicke priority su:

1. oddelit realny `IDA1` od `ID1`,
2. docistit `IMB` fallback dni,
3. doplnit rocne SEPS/FCR/aFRR data,
4. pridat automaticke quality reporty.
