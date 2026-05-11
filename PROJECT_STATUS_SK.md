# Stav projektu (Slovensko)

## Aktualny stav projektu

Projekt je adaptovany na slovenske data a aktualne stabilne pocita index v rezime **single-market** pre trhy:

- `DA` (Day-Ahead),
- `ID1` (intraday continuous/index),
- `IDA1` (zatial fallback z IDM 15-min),
- `IMB` (z OKTE SystemImbalance API),
- ciastocne pripravene `FCR` a `aFRR capacity` (pre plny rozsah treba pravidelne SEPS data za cele obdobie).

Vysledky sa ukladaju po dnoch do JSON (`results/...`) a zobrazuju sa vo webovom dashborde (Flask) s filtrami podla obdobia, trhov a agregacie (absolute, rolling average, annualized).

## Nakolko to funguje korektne

### Co funguje dobre

- Kompletny tok: import dat -> vypocet -> vizualizacia.
- Kontrola celistvosti vstupnych radov (DA/ID1/IDA1/IMB) pred vypoctom.
- Pokracovanie vo vypocte (`--resume`) bez opakovaneho prepocitavania hotovych dni.
- Opravene problemy pri prechodoch letneho/zimneho casu (DST), ktore predtym zastavovali vypocet pre konkretne datumy.

### Aktualne limity korektnosti

- `ID1` a `IDA1` sa v aktualnej implementacii pripravuju z rovnakeho 15-min OKTE IDM zdroja, preto su casto rovnake.
- V casti obdobia sa `IMB` mohol zhodovat s `ID1` kvoli fallback logike; to je ciastocne opravene vynutenym refreshom IMB z API.
- Pre `FCR` a `aFRR capacity` zatial nie je kompletna slovenska rocna datova sada vo formate ekvivalentnom nemeckemu pipeline.

## Co treba este dokoncit

1. **Oddelit `IDA1` a `ID1` na urovni zdrojov**  
   Je potrebny samostatny spolahlivy zdroj/vyvoz priamo pre `IDA1` (nie fallback z jedneho IDM 15-min suboru).

2. **Dokoncit rocne pokrytie `FCR` a `aFRR capacity`**  
   Treba denne SEPS data v stabilnom formate, nasledne hromadny import a validaciu.

3. **Formalizovat quality checks**  
   Automaticke kontroly:
   - kde `ID1 == IDA1`,
   - kde `IMB == ID1`,
   - kde chybaju dni/trhy.

4. **Zlepsit odolnost importu pre produkcne pouzitie**  
   Lepsie logovanie, retry mechanizmy API volani a jasne reporty o chybajucich/problemovych dnoch.

## Preco sa pre Slovensko nepodarilo 1:1 implementovat vsetky trhy ako v nemeckej verzii

Strucne: **limit nie je v matematike modelu, ale v dostupnosti a strukture vstupnych dat**.

Pre nemecku verziu ([ISEA Revenue Index](https://battery-charts.de/revenue-index/#daily-revenues)) existuje mature a unifikovany datapipeline napriec trhmi (vratane dat pre komplexne kombinovane strategie a detailnejsie intraday modelovanie).  
Pre slovensku adaptaciu plati:

- data su publikovane v inom rozsahu a s inou hlbkou,
- pri niektorych trhoch chyba ekvivalentna otvorena detailnost za cele obdobie,
- formaty exportov nie su vzdy stabilne a vyzaduju fallback/proxy pristupy,
- preto sa cast nemeckych modulov (najma cross-market logika) neda korektne reprodukovat bez dalsich komercnych/operatorovych zdrojov.

Zaver: Slovenska verzia je aktualne **prakticky pouzitelny benchmark** na zaklade dostupnych verejnych dat, nie vsak plna funkcna kopia nemeckeho indexu pre vsetky trhy a rezimy.
