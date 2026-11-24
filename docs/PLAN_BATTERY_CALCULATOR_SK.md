# Analýza a plán: Battery Calculator pre Battery Revenue Index SK

**Cieľ:** vytvoriť nástroj, ktorý potenciálnemu investorovi umožní nastaviť parametre batérie, simulovať jej
potenciálne výnosy a vyhodnotiť, či je investícia ekonomicky výhodná.

Hlavné smery rozvoja:

1. **Battery Modelling** – technické a ekonomické parametre batérie, degradácia, životnosť a pokles kapacity.
2. **Revenue Simulation** – kalkulátor výnosov na rôznych trhoch, simulácia príjmov a nákladov z historických cien.
3. **Investment Analysis** – investičné a prevádzkové náklady, financovanie (vlastné zdroje alebo úver),
   ekonomická efektívnosť a doba návratnosti.
4. **Integration** – interaktívny Battery Calculator integrovaný do existujúceho projektu.

---

## 1. Východiská – čo už projekt má

| Oblasť | Aktuálny stav | Čo chýba voči cieľu |
|---|---|---|
| Parametre batérie | tabuľka `battery_config`: kapacita (MWh), výkon (MW), limit cyklov, účinnosť, SOC min/max/štart, DoD, CAPEX (€/kWh), životnosť (roky) | degradácia, pokles kapacity v čase, vlastná spotreba, OPEX, CAPEX za výkon (€/kW) |
| Opotrebenie | lineárne „aging costs“ = CAPEX / (životnosť × cykly × 365), používa sa iba ako minimálny zisk obchodu | skutočný model degradácie (cyklická + kalendárna), stav batérie (SoH) po rokoch |
| Výnosy | denné výnosy pre 5 trhov (DA, IDM15, IDM60, IMB, aFRR) od 1. 3. 2025, natívne intervaly | ľubovoľná konfigurácia batérie (dnes len 4 pevné: 1–2 h × 1–2 cykly) |
| Kombinácia trhov | každý trh sa počíta samostatne (single-market) | stratégia kombinácie – súčet trhov nie je reálne dosiahnuteľný výnos |
| Náklady a investícia | – | CAPEX, OPEX, poplatky, financovanie, NPV, IRR, doba návratnosti |
| Rozhranie | dashboard zobrazuje zverejnené výsledky 4 konfigurácií | interaktívny kalkulátor s vlastnými parametrami |

**Výhoda pre ďalší vývoj:** výpočtové modely (`bri/models.py`) sú „čisté funkcie“ – dostanú ceny a parametre
batérie a vrátia výnosy. Dajú sa preto použiť aj pre ľubovoľnú batériu zadanú používateľom; ceny sú už v databáze.

---

## 2. Analýza jednotlivých smerov

### 2.1 Battery Modelling

- **Parametre:** doplniť vlastnú spotrebu (auxiliary), CAPEX rozdelený na €/kWh a €/kW, OPEX (€/kW/rok),
  limit SoH na konci životnosti (napr. 70–80 %).
- **Degradácia:** z výsledkov sa dá spočítať počet ekvivalentných plných cyklov (EFC) za rok – `result_interval`
  už obsahuje priebeh SOC. Pokles kapacity = kalendárna zložka (% za rok) + cyklická zložka (% na EFC).
- **Dôsledok:** každý ďalší rok má batéria menšiu kapacitu, teda aj menší výnos. Model musí počítať rok po roku,
  nie jeden rok × životnosť.
- **Riziko:** konkrétne koeficienty degradácie závisia od technológie (LFP, NMC) a výrobcu – mať ich ako
  nastaviteľné parametre s rozumnými predvolenými hodnotami a uvedeným zdrojom.

### 2.2 Revenue Simulation

- **Výkon výpočtu:** pri doterajšom prepočte trval jeden výpočet (trh × deň × konfigurácia) približne 40 ms.
  Jeden rok pre 5 trhov ≈ 1 800 výpočtov ≈ 1–1,5 min na konfiguráciu – na okamžitú odozvu v prehliadači pomalé.
  Riešenia:
  - (a) asynchrónny výpočet s ukazovateľom priebehu a uložením výsledku;
  - (b) predpočítaná mriežka konfigurácií (napr. 0,5–4 h × 1–3 cykly) a interpolácia medzi nimi;
  - **odporúčanie:** kombinácia – rýchly odhad z mriežky, presný výpočet na požiadanie.
- **Kombinácia trhov:** batéria nemôže v tom istom čase obchodovať na DA aj IMB. Možnosti:
  1. používateľ vyberie jeden trh;
  2. najlepší trh v každom dni (horná hranica – treba ju tak označiť);
  3. rozdelenie výkonu medzi trhy, napr. časť na aFRR a zvyšok na arbitráž (zložitejšie, vyžaduje úpravu modelov).
- **Obdobie:** historické dáta sú od 1. 3. 2025. Pre 10–15-ročnú investíciu treba scenáre budúcich výnosov,
  napr. historický rok × koeficient vývoja cien (konzervatívny / základný / optimistický).
- **Prevádzkové náklady:** poplatky za distribúciu/prenos pri nabíjaní a vybíjaní, poplatok agregátorovi,
  zodpovednosť za odchýlku. Konkrétne slovenské sadzby (ÚRSO, SEPS, distribučné spoločnosti) treba dohľadať
  a overiť – v projekte zatiaľ nie sú.

### 2.3 Investment Analysis

- **Vstupy:** CAPEX (batéria, striedač, pripojenie, projekt), OPEX, náhrada článkov (augmentácia),
  diskontná sadzba, daň a odpisy (slovenské pravidlá treba overiť), financovanie: vlastné zdroje alebo úver
  (podiel, úrok, splatnosť, anuita).
- **Výstupy:** peňažné toky po rokoch, NPV, IRR (projektová aj pre vlastníka), jednoduchá a diskontovaná doba
  návratnosti, LCOS (náklady na uloženú MWh), pri úvere DSCR (pokrytie splátok).
- **Citlivostná analýza:** vplyv ceny batérie, úrokov, poklesu výnosov a degradácie (tornado graf) – pre
  investora dôležitejšia ako jedno číslo.

### 2.4 Integration

- Nová stránka v existujúcom dashboarde (Flask), napr. `/kalkulacka`: formulár parametrov batérie, trhov
  a financovania; výsledok ako grafy a tabuľky.
- **Oddeliť od oficiálneho indexu:** používateľské scenáre nesmú meniť zverejnené výsledky. Nové tabuľky,
  napr. `calc_scenario` (parametre používateľa), `scenario_run`, `scenario_year` (výsledok po rokoch);
  `battery_config` zostáva len pre 4 oficiálne konfigurácie indexu.
- **Bezpečnosť:** obmedziť rozsahy vstupov a počet súčasných výpočtov, aby verejný kalkulátor nepreťažil server.

---

## 3. Plán

| Fáza | Úlohy | Výstup | Závisí od |
|---|---|---|---|
| 0. Metodika | dohodnúť model degradácie, stratégiu kombinácie trhov, scenáre budúcich cien a zoznam nákladov; dohľadať SK poplatky a daňové pravidlá | krátky metodický dokument na schválenie vyučujúcim | – |
| 1. Battery Modelling | rozšíriť parametre batérie; výpočet EFC zo SOC; model degradácie (kalendárna + cyklická); SoH a kapacita po rokoch; testy | modul `battery_model`, testy | 0 |
| 2. Revenue Simulation | výpočet výnosov pre ľubovoľnú konfiguráciu nad cenami v DB; predpočítaná mriežka + interpolácia; stratégie trhov (jeden trh / najlepší trh v dni); prevádzkové náklady; projekcia na roky s degradáciou | modul `revenue_simulation`, tabuľky scenárov, testy | 0, 1 |
| 3. Investment Analysis | peňažné toky po rokoch; financovanie vlastnými zdrojmi / úverom; NPV, IRR, návratnosť, LCOS, DSCR; citlivostná analýza | modul `investment`, testy (overené na ručne vypočítanom príklade v Exceli) | 2 |
| 4. Integration | stránka `/kalkulacka`: formulár, asynchrónny výpočet, grafy (výnosy, SoH, cash-flow, citlivosť), export PDF/CSV; prepojenie s dashboardom | funkčný kalkulátor v Docker nasadení | 1–3 |
| 5. Overenie a dokumentácia | porovnanie s verejnými štúdiami a benchmarkmi BESS; používateľský návod; aktualizácia README | správa o validácii, návod | 4 |

**Odporúčané poradie:** fázy 1 a 3 sa dajú robiť paralelne (degradácia a finančný model sú nezávislé),
fáza 2 ich prepojí.

---

## 4. Otvorené otázky na rozhodnutie

1. **Kombinácia trhov:** jeden zvolený trh, najlepší trh v dni, alebo delenie výkonu?
2. **Model degradácie:** jednoduchý (lineárny pokles na EFC + rok), alebo podľa technológie (LFP/NMC)?
3. **Budúce výnosy:** opakovať posledný historický rok, alebo scenáre vývoja cien?
4. **Rozsah nákladov:** poplatky za sieť a daň už v prvej verzii, alebo neskôr?
5. **Cieľový používateľ:** verejný kalkulátor (obmedzenia, výkon), alebo interný nástroj tímu?

---

## 5. Kritériá úspechu

- Používateľ nastaví batériu, obdobie, trh a financovanie a do niekoľkých sekúnd dostane odhad
  (presný výpočet na požiadanie).
- Výstupy: výnosy, náklady, SoH po rokoch, NPV, IRR a doba návratnosti so zreteľne uvedenými predpokladmi.
- Výsledky sú overené na ručne vypočítanom príklade a pre 4 oficiálne konfigurácie sedia s existujúcim indexom.
- Oficiálne zverejnené výsledky indexu sa kalkulátorom nemenia.

---

*Zdroje: aktuálny kód projektu (`bri/sql/001_schema.sql`, `calculation_config_slovakia.py`, `bri/models.py`,
`bri/calc.py`) a namerané časy výpočtu pri prepočte histórie. Metodické odporúčania (degradácia, NPV/IRR, LCOS,
DSCR) vychádzajú zo všeobecných znalostí; slovenské sadzby poplatkov, daňové pravidlá a koeficienty degradácie
nie sú overené a treba ich doplniť.*
