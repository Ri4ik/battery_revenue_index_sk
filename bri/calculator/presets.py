"""Predefined commercial grid-scale battery systems (utility-scale BESS blocks).

Data: Coloprice BESS price index and catalog (https://coloprice.com/bess/), updated 2026-09-28,
product pages verified 2026-08. Prices are equipment prices per kWh in USD: for Tesla the public
US configurator price (AC block incl. inverter, before shipping and installation), for the others
Coloprice estimates ex-works China from tender benchmarks. Middle of the published range is used.

How a preset fills the calculator:
  * power and energy = number of units x rated block power / energy;
  * efficiency = sqrt(round-trip efficiency) (the model uses a one-way efficiency);
  * capex_energy_eur_kwh = equipment price x EUR_PER_USD;
  * DC blocks have no inverter: capex_power_eur_kw = PCS_EUR_KW (estimate);
  * capex_installation_pct = INSTALLATION_PCT (transport, installation, grid connection, EPC - estimate,
    Coloprice gives 150-320 USD/kWh installed in EU/US against 80-130 USD/kWh ex-works).
Update the list when new prices are published; values marked "estimate" are not verified data.
"""

import math

PRICE_SOURCE = "Coloprice BESS price index (coloprice.com/bess), stav 2026-09"
EUR_PER_USD = 0.86       # orientation exchange rate - adjust to the current rate
PCS_EUR_KW = 60.0        # estimate: inverter/PCS for DC blocks
INSTALLATION_PCT = 50.0  # estimate: transport, installation, grid connection, project (% of equipment)
DEFAULT_ONE_WAY_EFFICIENCY = 0.95

PRESETS = [
    {
        "id": "tesla_megapack2xl_2h",
        "name": "Tesla Megapack 2 XL (2 h)",
        "maker": "Tesla",
        "unit_power_mw": 1.927, "unit_energy_mwh": 3.854,
        "rte": 0.92, "ac_block": True,
        "price_usd_kwh": (266, 315),
        "price_note": "verejný konfigurátor Tesla (USA), blok AC vrátane striedača, bez dopravy a inštalácie",
        "cycle_fade_pct_per_100efc": None, "warranty_years": 20,
        "url": "https://coloprice.com/bess/catalog/tesla/tesla-megapack-2-xl/",
    },
    {
        "id": "tesla_megapack2xl_4h",
        "name": "Tesla Megapack 2 XL (4 h)",
        "maker": "Tesla",
        "unit_power_mw": 0.979, "unit_energy_mwh": 3.916,
        "rte": 0.937, "ac_block": True,
        "price_usd_kwh": (266, 315),
        "price_note": "verejný konfigurátor Tesla (USA), blok AC vrátane striedača, bez dopravy a inštalácie",
        "cycle_fade_pct_per_100efc": None, "warranty_years": 20,
        "url": "https://coloprice.com/bess/catalog/tesla/tesla-megapack-2-xl/",
    },
    {
        "id": "sungrow_powertitan2_2h",
        "name": "Sungrow PowerTitan 2.0 (2 h)",
        "maker": "Sungrow",
        "unit_power_mw": 2.52, "unit_energy_mwh": 5.015,
        "rte": 0.895, "ac_block": True,
        "price_usd_kwh": (90, 130),
        "price_note": "odhad Coloprice, ex-works Čína, blok AC vrátane PCS",
        "cycle_fade_pct_per_100efc": None, "warranty_years": None,
        "url": "https://coloprice.com/bess/catalog/sungrow/sungrow-powertitan-2-0/",
    },
    {
        "id": "byd_mccube_t_4h",
        "name": "BYD MC Cube-T (4 h)",
        "maker": "BYD",
        "unit_power_mw": 1.608, "unit_energy_mwh": 6.431,
        "rte": None, "ac_block": False,
        "price_usd_kwh": (74, 94),
        "price_note": "odhad Coloprice, ex-works Čína, blok DC bez striedača",
        "cycle_fade_pct_per_100efc": None, "warranty_years": None,
        "url": "https://coloprice.com/bess/catalog/byd/byd-mc-cube-t/",
    },
    {
        "id": "huawei_luna2000_4472",
        "name": "Huawei LUNA2000-4472-2S (2 h)",
        "maker": "Huawei",
        "unit_power_mw": 2.236, "unit_energy_mwh": 4.472,
        "rte": None, "ac_block": False,
        "price_usd_kwh": (80, 110),
        "price_note": "odhad Coloprice, ex-works, kontajner DC bez PCS",
        "cycle_fade_pct_per_100efc": None, "warranty_years": None,
        "url": "https://coloprice.com/bess/catalog/huawei/huawei-luna2000-4472-2s/",
    },
    {
        "id": "catl_tener",
        "name": "CATL TENER (2 h)",
        "maker": "CATL",
        # 8,000 cycles to 70 % SOH at cell level (CATL brochure 2026) -> 30 % / 80 = 0.375 % per 100 EFC
        "unit_power_mw": 3.126, "unit_energy_mwh": 6.25,
        "rte": None, "ac_block": False,
        "price_usd_kwh": (80, 105),
        "price_note": "odhad Coloprice, ex-works Čína, blok DC bez striedača",
        "cycle_fade_pct_per_100efc": 0.375, "warranty_years": None,
        "url": "https://coloprice.com/bess/catalog/catl/catl-tener/",
    },
]
BY_ID = {p["id"]: p for p in PRESETS}


def price_usd_kwh(preset):
    low, high = preset["price_usd_kwh"]
    return (low + high) / 2


def to_params(preset, units=1):
    """Calculator fields filled from a preset and a number of units."""
    units = max(1, int(units))
    efficiency = math.sqrt(preset["rte"]) if preset["rte"] else DEFAULT_ONE_WAY_EFFICIENCY
    values = {
        "preset": preset["id"],
        "preset_units": units,
        "power_mw": round(units * preset["unit_power_mw"], 3),
        "duration_h": round(preset["unit_energy_mwh"] / preset["unit_power_mw"], 3),
        "efficiency": round(efficiency, 4),
        "capex_energy_eur_kwh": round(price_usd_kwh(preset) * EUR_PER_USD, 1),
        "capex_power_eur_kw": 0.0 if preset["ac_block"] else PCS_EUR_KW,
        "capex_installation_pct": INSTALLATION_PCT,
    }
    if preset["cycle_fade_pct_per_100efc"] is not None:
        values["cycle_fade_pct_per_100efc"] = preset["cycle_fade_pct_per_100efc"]
    return values


def describe(preset):
    """Facts shown on the page next to the preset."""
    low, high = preset["price_usd_kwh"]
    return {
        "id": preset["id"], "name": preset["name"],
        "unit": "{} MW / {} MWh".format(preset["unit_power_mw"], preset["unit_energy_mwh"]),
        "rte": "{:.1f} %".format(preset["rte"] * 100) if preset["rte"] else "nezverejnená (použité {:.0f} %)".format(
            DEFAULT_ONE_WAY_EFFICIENCY ** 2 * 100),
        "price": "{}–{} USD/kWh".format(low, high),
        "price_note": preset["price_note"],
        "block": "AC (so striedačom)" if preset["ac_block"] else "DC (bez striedača)",
        "warranty": "{} rokov".format(preset["warranty_years"]) if preset["warranty_years"] else "nezverejnená",
        "cycles": "8 000 cyklov do 70 % SoH (článok, údaj výrobcu)" if preset["id"] == "catl_tener" else "nezverejnená",
        "url": preset["url"],
        "fill": to_params(preset, 1),
        "unit_power_mw": preset["unit_power_mw"],
    }
