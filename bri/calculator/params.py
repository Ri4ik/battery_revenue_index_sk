"""Calculator input: defaults, allowed ranges and validation.

Every parameter has a range so a public calculator cannot be asked for absurd or very
expensive calculations. Default values are orientation values for a first estimate,
not verified market data - the web page lists them as assumptions.
"""

import datetime as dt
import hashlib
import json

from bri import MODEL_VERSION

MARKETS = ("DA", "IDM15", "IDM60", "IMB", "AFRR")
STRATEGIES = MARKETS + ("BEST",)  # one market, or the best market of each day (upper bound)
MAX_PERIOD_DAYS = 731

# name: (default, min, max, kind, Slovak label, unit, group)
SPEC = [
    # battery (technical)
    ("power_mw", 1.0, 0.1, 500.0, float, "Výkon", "MW", "battery"),
    ("duration_h", 2.0, 0.5, 8.0, float, "Kapacita / výkon (doba vybíjania)", "h", "battery"),
    ("cycles_per_day", 1.0, 0.5, 4.0, float, "Limit cyklov za deň", "cyklov", "battery"),
    ("efficiency", 0.95, 0.70, 1.0, float, "Účinnosť nabíjania a vybíjania (jednosmerná)", "–", "battery"),
    ("soc_min", 0.0, 0.0, 0.5, float, "Minimálny SoC", "–", "battery"),
    ("soc_max", 1.0, 0.5, 1.0, float, "Maximálny SoC", "–", "battery"),
    # degradation
    ("calendar_fade_pct", 1.0, 0.0, 5.0, float, "Kalendárna degradácia", "% kapacity / rok", "degradation"),
    ("cycle_fade_pct_per_100efc", 0.35, 0.0, 2.0, float, "Cyklická degradácia", "% kapacity / 100 EFC",
     "degradation"),
    ("eol_soh_pct", 70.0, 50.0, 95.0, float, "Koniec životnosti článkov pri SoH", "%", "degradation"),
    ("replace_at_eol", 1, 0, 1, int, "Výmena článkov po dosiahnutí konca životnosti", "áno/nie", "degradation"),
    ("replacement_cost_pct", 60.0, 0.0, 150.0, float, "Cena výmeny článkov", "% z CAPEX energie",
     "degradation"),
    # revenue
    ("revenue_factor_pct", 100.0, 10.0, 150.0, float, "Dosiahnuteľná časť simulovaných výnosov", "%",
     "revenue"),
    ("revenue_growth_pct", 0.0, -15.0, 15.0, float, "Zmena výnosov", "% / rok", "revenue"),
    ("aggregator_fee_pct", 10.0, 0.0, 50.0, float, "Poplatok agregátorovi / obchodníkovi", "% z výnosov",
     "revenue"),
    ("grid_fee_eur_mwh", 0.0, 0.0, 200.0, float, "Sieťové poplatky za nabitú energiu", "€/MWh", "revenue"),
    # investment and operation
    ("capex_energy_eur_kwh", 250.0, 50.0, 1500.0, float, "CAPEX – batéria (energia)", "€/kWh", "investment"),
    ("capex_power_eur_kw", 0.0, 0.0, 1500.0, float, "CAPEX – striedače a pripojenie (výkon)", "€/kW",
     "investment"),
    ("capex_installation_pct", 0.0, 0.0, 200.0, float,
     "Doprava, inštalácia, pripojenie, projekt", "% z ceny zariadenia", "investment"),
    ("capex_fixed_eur", 0.0, 0.0, 50000000.0, float, "CAPEX – fixné náklady (projekt, pozemok)", "€",
     "investment"),
    ("opex_pct_capex", 1.5, 0.0, 10.0, float, "OPEX (údržba, poistenie, monitoring)", "% z CAPEX / rok",
     "investment"),
    ("opex_escalation_pct", 2.0, 0.0, 10.0, float, "Rast OPEX (inflácia)", "% / rok", "investment"),
    ("project_life_years", 15, 1, 30, int, "Doba hodnotenia projektu", "rokov", "investment"),
    ("residual_value_pct", 0.0, 0.0, 50.0, float, "Zostatková hodnota na konci", "% z CAPEX", "investment"),
    # financing and tax
    ("discount_rate_pct", 7.0, 0.0, 25.0, float, "Diskontná sadzba", "%", "finance"),
    ("debt_share_pct", 0.0, 0.0, 90.0, float, "Podiel úveru na CAPEX", "%", "finance"),
    ("interest_rate_pct", 6.0, 0.0, 20.0, float, "Úroková sadzba úveru", "% / rok", "finance"),
    ("loan_tenor_years", 10, 1, 25, int, "Splatnosť úveru (anuita)", "rokov", "finance"),
    ("tax_rate_pct", 21.0, 0.0, 50.0, float, "Daň z príjmov právnických osôb", "%", "finance"),
    ("depreciation_years", 12, 1, 30, int, "Doba odpisovania (rovnomerne)", "rokov", "finance"),
]
DEFAULTS = {name: default for name, default, *_ in SPEC}
GROUPS = [
    ("battery", "Batéria"),
    ("degradation", "Degradácia a životnosť"),
    ("revenue", "Výnosy a prevádzkové poplatky"),
    ("investment", "Investícia a prevádzka"),
    ("finance", "Financovanie a dane"),
]
STRATEGY_LABELS = {
    "DA": "Denný trh (DA)",
    "IDM15": "Vnútrodenný trh 15 min (IDM15)",
    "IDM60": "Vnútrodenný trh 60 min (IDM60)",
    "IMB": "Odchýlka (IMB)",
    "AFRR": "aFRR (kapacita + energia)",
    "BEST": "Najlepší trh v každom dni (horná hranica)",
}

# Fixed like in the official index (calculation_config_slovakia.build_configs): start SoC and DoD,
# and the wear threshold of the trading model = 250 EUR/kWh over 10 years x cycles x 365.
# Dispatch therefore matches the index; the investor's CAPEX enters only the cash-flow model.
SOC_START = 0.5
DOD = 1.0
DISPATCH_COST_EUR_KWH = 250.0
DISPATCH_LIFE_YEARS = 10.0


class ParamError(ValueError):
    """Invalid user input; the message is shown on the page."""


def _parse_day(value, name):
    if isinstance(value, dt.date):
        return value
    try:
        return dt.datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        raise ParamError("{}: neplatný dátum '{}'".format(name, value))


def validate(raw, data_start=None, data_end=None):
    """Raw form / JSON values -> complete, typed parameter dict. Raises ParamError."""
    out = {}
    errors = []
    for name, default, lo, hi, kind, label, unit, _ in SPEC:
        value = raw.get(name, default)
        if value is None or (isinstance(value, str) and not value.strip()):
            value = default
        try:
            value = kind(float(str(value).replace(",", ".")))
        except ValueError:
            errors.append("{}: nie je číslo".format(label))
            continue
        if not lo <= value <= hi:
            errors.append("{}: povolený rozsah {}–{} {}".format(label, lo, hi, unit))
            continue
        out[name] = value
    if errors:
        raise ParamError("; ".join(errors))
    if out["soc_max"] - out["soc_min"] < 0.3:
        raise ParamError("Rozdiel maximálneho a minimálneho SoC musí byť aspoň 0,3.")
    if not out["soc_min"] <= SOC_START <= out["soc_max"]:
        raise ParamError("Rozsah SoC musí obsahovať štartovací SoC 0,5.")

    from bri.calculator import presets  # noqa: avoid an import cycle

    preset = str(raw.get("preset") or "")
    if preset and preset not in presets.BY_ID:
        raise ParamError("Neznámy typ batérie '{}'.".format(preset))
    out["preset"] = preset
    try:
        out["preset_units"] = max(1, min(1000, int(float(raw.get("preset_units") or 1)))) if preset else None
    except ValueError:
        raise ParamError("Počet jednotiek nie je číslo.")

    strategy = str(raw.get("strategy") or "DA").upper()
    if strategy not in STRATEGIES:
        raise ParamError("Neznáma stratégia trhu '{}'.".format(strategy))
    out["strategy"] = strategy

    if raw.get("period_start") or raw.get("period_end"):
        start = _parse_day(raw.get("period_start") or data_start, "Začiatok obdobia")
        end = _parse_day(raw.get("period_end") or data_end, "Koniec obdobia")
    else:  # default: the last 365 days with data
        if data_end is None:
            raise ParamError("V databáze nie sú žiadne vstupné dáta.")
        end = data_end
        start = max(data_start or end, end - dt.timedelta(days=364))
    if start is None or end is None or end < start:
        raise ParamError("Koniec obdobia musí byť po jeho začiatku.")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise ParamError("Obdobie môže mať najviac {} dní.".format(MAX_PERIOD_DAYS))
    if (end - start).days + 1 < 7:
        raise ParamError("Obdobie musí mať aspoň 7 dní.")
    out["period_start"], out["period_end"] = start.isoformat(), end.isoformat()
    return out


def tech(params):
    """Parameters that change the dispatch of the normalized battery (1 MW)."""
    cycles = params["cycles_per_day"]
    return {
        "duration_h": params["duration_h"],
        "cycles_per_day": cycles,
        "efficiency": params["efficiency"],
        "soc_min": params["soc_min"],
        "soc_max": params["soc_max"],
        "soc_start": SOC_START,
        "dod": DOD,
        "aging_eur_mwh": round(DISPATCH_COST_EUR_KWH * 1000 / (DISPATCH_LIFE_YEARS * cycles * 365), 9),
    }


def tech_hash_of(tech_params):
    payload = json.dumps({"tech": tech_params, "model": MODEL_VERSION}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tech_hash(params):
    return tech_hash_of(tech(params))
