"""Battery Calculator: finance and degradation on hand-checked examples, strategies, and the
simulation against the official index on the test database."""

import pandas as pd
import pytest

from bri import calc, readiness
from bri.calculator import finance, params as P, scenario, simulation
from bri.importers import okte
from tests.conftest import D, fixture_bytes


def _params(**overrides):
    raw = dict(period_start="2025-09-25", period_end="2025-10-01")
    raw.update(overrides)
    return P.validate(raw)


def _plain(**overrides):
    """1 MW / 1 MWh, no degradation, costs, tax or debt - easy to check by hand."""
    base = dict(duration_h=1, calendar_fade_pct=0, cycle_fade_pct_per_100efc=0, aggregator_fee_pct=0,
                opex_pct_capex=0, tax_rate_pct=0, discount_rate_pct=0, project_life_years=10)
    base.update(overrides)
    return _params(**base)


# ---------------------------------------------------------------- finance primitives

def test_annuity_npv_irr_payback_known_values():
    assert finance.annuity_payment(100000, 0.05, 10) == pytest.approx(12950.46, abs=0.01)
    assert finance.annuity_payment(1000, 0.0, 4) == 250
    assert finance.npv(0.1, [-100, 110]) == pytest.approx(0.0)
    assert finance.irr([-100, 110]) == pytest.approx(0.10, abs=1e-6)
    assert finance.irr([-100, -10]) is None
    # a late negative cash flow (cell replacement) adds a spurious negative root; the IRR is the
    # rate where NPV falls through zero, consistent with NPV > 0 at a lower discount rate
    flows = [-250000] + [40000] * 13 + [-120000, 42000]
    rate = finance.irr(flows)
    assert finance.npv(rate, flows) == pytest.approx(0, abs=1e-3)
    assert rate > 0.05 and finance.npv(0.05, flows) > 0
    assert finance.payback([-100, 50, 50, 50]) == pytest.approx(2.0)
    assert finance.payback([-100, 60, 60]) == pytest.approx(1 + 40 / 60)
    assert finance.payback([-100, 10, 10]) is None
    # discounted: 60/1.1 + 60/1.21 = 104.13 -> paid back during year 2
    assert finance.payback([-100, 60, 60], 0.1) == pytest.approx(1 + (100 - 60 / 1.1) / (60 / 1.21))


def test_project_without_costs_matches_hand_calculation():
    p = _plain()
    rows, kpi = finance.project(p, annual_revenue=50000, annual_efc=300)
    assert kpi["capex_eur"] == 250000  # 250 EUR/kWh x 1000 kWh
    assert len(rows) == 11 and rows[0]["cf_project"] == -250000
    assert all(r["cf_project"] == pytest.approx(50000) for r in rows[1:])
    assert kpi["npv_project"] == pytest.approx(250000)
    assert kpi["payback_years"] == pytest.approx(5.0)
    # 10-year annuity factor 5.0 -> IRR 15.098 %
    assert kpi["irr_project"] == pytest.approx(0.15098, abs=1e-4)
    # LCOS at 0 % discount: 250000 EUR / (10 years x 300 EFC x 1 MWh)
    assert kpi["lcos_eur_mwh"] == pytest.approx(250000 / 3000)


def test_degradation_reduces_revenue_and_triggers_replacement():
    p = _plain(calendar_fade_pct=1.0, cycle_fade_pct_per_100efc=0.35, project_life_years=20,
               eol_soh_pct=70, replacement_cost_pct=60)
    rows, kpi = finance.project(p, annual_revenue=50000, annual_efc=365)
    loss = 0.01 + 0.0035 * 3.65  # 2.2775 % per year
    assert rows[1]["soh_end"] == pytest.approx(1 - loss)
    assert rows[1]["revenue_gross"] == pytest.approx(50000 * (1 - loss / 2))
    # SoH <= 70 % after ceil(0.3 / 0.022775) = 14 years -> cells replaced in year 14
    assert kpi["replacement_years"] == [14]
    assert rows[14]["capex"] == pytest.approx(0.6 * 250000)
    assert rows[15]["soh_start"] == 1.0
    no_repl = finance.project(dict(p, replace_at_eol=0), 50000, 365)[1]
    assert no_repl["replacement_years"] == [] and no_repl["soh_end"] < 0.7
    # no replacement shortly before the end of the project
    late = finance.project(dict(p, project_life_years=15), 50000, 365)[1]
    assert late["replacement_years"] == [] and late["soh_end"] < 0.7


def test_loan_is_repaid_and_interest_lowers_tax():
    p = _plain(debt_share_pct=60, interest_rate_pct=6, loan_tenor_years=8, tax_rate_pct=21,
               depreciation_years=10, discount_rate_pct=7)
    rows, kpi = finance.project(p, annual_revenue=60000, annual_efc=300)
    assert kpi["debt_eur"] == pytest.approx(150000)
    assert sum(r["principal"] for r in rows) == pytest.approx(150000)
    assert rows[9]["interest"] == 0 and rows[9]["principal"] == 0
    payment = finance.annuity_payment(150000, 0.06, 8)
    assert rows[1]["interest"] + rows[1]["principal"] == pytest.approx(payment)
    # year 1: EBITDA 60000, depreciation 25000, interest 9000 -> tax 21 % of 26000
    assert rows[1]["tax"] == pytest.approx(0.21 * (60000 - 25000 - 9000))
    assert rows[1]["dscr"] == pytest.approx((60000 - rows[1]["tax"]) / payment)
    assert kpi["irr_equity"] > kpi["irr_project"]  # cheap debt levers the equity return


def test_tax_losses_are_carried_forward():
    p = _plain(tax_rate_pct=20, depreciation_years=5, project_life_years=6)
    rows, _ = finance.project(p, annual_revenue=40000, annual_efc=100)
    # years 1-5: 40000 - 50000 depreciation = loss 10000 each, year 6 profit 40000 - 50000 loss
    assert all(r["tax"] == 0 for r in rows[1:7])
    rows, _ = finance.project(dict(p, project_life_years=7), 40000, 100)
    assert rows[7]["tax"] == pytest.approx(0.2 * (40000 - 10000))


def test_sensitivity_moves_npv_in_the_expected_direction():
    p = _plain(discount_rate_pct=7)
    sens = {r["key"]: r for r in finance.sensitivity(p, 50000, 300)}
    assert sens["revenue"]["low"] < sens["revenue"]["base"] < sens["revenue"]["high"]
    assert sens["capex"]["low"] > sens["capex"]["base"] > sens["capex"]["high"]
    assert sens["discount"]["low"] > sens["discount"]["high"]


# ---------------------------------------------------------------- params, EFC and strategies

def test_params_defaults_ranges_and_period():
    p = P.validate({}, D(2025, 3, 1), D(2026, 3, 1))
    assert p["period_end"] == "2026-03-01" and p["period_start"] == "2025-03-02"
    assert p["strategy"] == "DA" and p["power_mw"] == 1.0
    with pytest.raises(P.ParamError):
        P.validate({"power_mw": "1000"}, D(2025, 3, 1), D(2026, 3, 1))
    with pytest.raises(P.ParamError):
        P.validate({"period_start": "2025-01-01", "period_end": "2027-06-01"})
    with pytest.raises(P.ParamError):
        P.validate({"strategy": "FCR"}, D(2025, 3, 1), D(2026, 3, 1))
    assert P.validate({"power_mw": "2,5"}, D(2025, 3, 1), D(2026, 3, 1))["power_mw"] == 2.5
    # the investor's CAPEX does not change the dispatch (cache key), the battery does
    a = P.validate({}, D(2025, 3, 1), D(2026, 3, 1))
    assert P.tech_hash(a) == P.tech_hash(dict(a, capex_energy_eur_kwh=400))
    assert P.tech_hash(a) != P.tech_hash(dict(a, duration_h=1))


def test_efc_and_best_market_strategy():
    assert simulation.efc_of([1.0, 0.5], 0.5) == pytest.approx(0.5)
    assert simulation.efc_of([float("nan")] * 4, 0.5) == 0.0
    frame = pd.DataFrame([
        ("DA", D(2025, 5, 1), 100.0, 1.0), ("IMB", D(2025, 5, 1), 300.0, 1.5),
        ("DA", D(2025, 5, 2), 200.0, 1.0), ("IMB", D(2025, 5, 2), 50.0, 1.2),
    ], columns=["market", "delivery_day", "revenue_eur_mw", "efc"])
    best = simulation.apply_strategy(frame, "BEST")
    assert list(best["market"]) == ["IMB", "DA"] and list(best["revenue_eur_mw"]) == [300.0, 200.0]
    assert list(simulation.apply_strategy(frame, "DA")["revenue_eur_mw"]) == [100.0, 200.0]

    p = dict(_plain(power_mw=2), strategy="BEST")
    daily, years, summary = scenario.evaluate(p, frame)
    assert summary["annual_revenue_eur"] == pytest.approx((300 + 200) / 2 * 2 * 365)
    assert {m["market"]: m["chosen_days"] for m in summary["by_market"]} == {"DA": 1, "IMB": 1}


# ---------------------------------------------------------------- database

def _da_day(conn, monkeypatch, day):
    monkeypatch.setattr(okte, "_get", lambda url, params: fixture_bytes("okte_dam_{}.json".format(day)))
    okte.import_da(conn, day)
    readiness.refresh(conn, [day], markets=("DA",))


def test_simulation_reproduces_the_official_index_and_uses_the_cache(conn, monkeypatch):
    day = D(2025, 10, 1)
    _da_day(conn, monkeypatch, day)
    calc.run(conn, calc.select_tasks(conn, [day], markets=("DA",)), trigger="test")
    with conn.cursor() as cur:
        cur.execute("""SELECT config_code, revenue_eur FROM published_daily_total
                       WHERE market = 'DA' AND delivery_day = %s""", (day,))
        official = {code: float(rev) for code, rev in cur.fetchall()}
        cur.execute("SELECT count(*) FROM result_publication")
        publications = cur.fetchone()[0]
    conn.commit()

    for energy in (1, 2):
        for cycles in (1, 2):
            p = _params(duration_h=energy, cycles_per_day=cycles)
            frame = simulation.simulate(conn, P.tech(p), ["DA"], day, day, workers=1)
            assert len(frame) == 1
            assert frame["revenue_eur_mw"].iloc[0] == pytest.approx(official["{}h_{}c".format(energy, cycles)])
            assert 0 < frame["efc"].iloc[0] <= cycles + 1e-9

    # second run: everything from the cache, nothing recomputed
    monkeypatch.setattr(simulation, "_compute", lambda task: (_ for _ in ()).throw(AssertionError("recomputed")))
    p = _params(duration_h=1, cycles_per_day=1)
    again = simulation.simulate(conn, P.tech(p), ["DA"], day, day, workers=1)
    assert again["revenue_eur_mw"].iloc[0] == pytest.approx(official["1h_1c"])
    with conn.cursor() as cur:  # the official index is untouched
        cur.execute("SELECT count(*) FROM result_publication")
        assert cur.fetchone()[0] == publications
    conn.commit()


def test_scenario_end_to_end(conn, monkeypatch):
    for day in (D(2025, 9, 30), D(2025, 10, 1)):
        _da_day(conn, monkeypatch, day)
    sid = scenario.create(conn, {"power_mw": "5", "duration_h": "2", "strategy": "DA",
                                 "period_start": "2025-09-25", "period_end": "2025-10-01",
                                 "debt_share_pct": "50", "project_life_years": "12"})
    assert scenario.run(conn, sid, log=lambda *a: None) == "success"
    s, daily, years = scenario.load(conn, sid)
    assert s["status"] == "success" and s["days_used"] == 2
    assert len(daily) == 2 and len(years) == 13
    k = s["summary"]["kpi"]
    assert k["capex_eur"] == pytest.approx(5 * 2 * 1000 * 250)
    assert s["summary"]["annual_revenue_eur"] == pytest.approx(daily["revenue_eur"].mean() * 365)
    assert k["debt_eur"] == pytest.approx(k["capex_eur"] / 2)

    with pytest.raises(P.ParamError):
        scenario.create(conn, {"duration_h": "20"})
    failed = scenario.create(conn, {"strategy": "AFRR", "period_start": "2025-09-25",
                                    "period_end": "2025-10-01"})
    assert scenario.run(conn, failed, log=lambda *a: None) == "failed"  # no aFRR inputs in the period


def test_calculator_pages(conn, monkeypatch):
    from flask import Flask

    from tools import battery_calculator_web as web

    _da_day(conn, monkeypatch, D(2025, 10, 1))
    monkeypatch.setattr(web, "_conn", lambda: _Unclosable(conn))
    launched = []
    monkeypatch.setattr(scenario, "launch", lambda sid: launched.append(sid))
    app = Flask(__name__)
    app.register_blueprint(web.bp)
    client = app.test_client()

    page = client.get("/kalkulacka")
    assert page.status_code == 200 and "Kalkulačka batérie" in page.get_data(as_text=True)
    assert "Tesla Megapack 2 XL" in page.get_data(as_text=True)
    bad = client.post("/kalkulacka", data={"power_mw": "abc"})
    assert bad.status_code == 200 and "nie je číslo" in bad.get_data(as_text=True)
    resp = client.post("/kalkulacka", data={"period_start": "2025-09-25", "period_end": "2025-10-01",
                                            "preset": "catl_tener", "preset_units": "2",
                                            "power_mw": "6.252", "duration_h": "1.999"})
    assert resp.status_code == 302 and launched
    sid = launched[0]
    assert "Čaká na spustenie" in client.get("/kalkulacka/{}".format(sid)).get_data(as_text=True)
    scenario.run(conn, sid, log=lambda *a: None)
    result = client.get("/kalkulacka/{}".format(sid)).get_data(as_text=True)
    assert "NPV projektu" in result and "Citlivostná analýza" in result and "2 × CATL TENER" in result
    csv = client.get("/kalkulacka/{}.csv".format(sid))
    assert csv.status_code == 200 and b"cf_project" in csv.data
    assert client.get("/kalkulacka/metodika").status_code == 200


class _Unclosable:
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ---------------------------------------------------------------- commercial battery presets

def test_presets_fill_valid_parameters():
    from bri.calculator import presets as PR

    assert len(PR.PRESETS) >= 5
    for preset in PR.PRESETS:
        raw = dict(PR.to_params(preset, units=3), period_start="2025-09-25", period_end="2025-10-01")
        p = P.validate(raw)
        assert p["preset"] == preset["id"] and p["preset_units"] == 3
        assert p["power_mw"] == pytest.approx(3 * preset["unit_power_mw"], abs=1e-3)
        assert p["power_mw"] * p["duration_h"] == pytest.approx(3 * preset["unit_energy_mwh"], rel=1e-3)
        low, high = preset["price_usd_kwh"]
        assert p["capex_energy_eur_kwh"] == pytest.approx((low + high) / 2 * PR.EUR_PER_USD, abs=0.1)
        assert (p["capex_power_eur_kw"] == 0) == preset["ac_block"]

    tesla = PR.to_params(PR.BY_ID["tesla_megapack2xl_2h"])
    assert tesla["efficiency"] == pytest.approx(0.92 ** 0.5, abs=1e-4)
    assert PR.to_params(PR.BY_ID["catl_tener"])["cycle_fade_pct_per_100efc"] == 0.375
    with pytest.raises(P.ParamError):
        P.validate({"preset": "unknown", "period_start": "2025-09-25", "period_end": "2025-10-01"})


def test_capex_includes_installation_share():
    p = _plain(capex_power_eur_kw=60, capex_installation_pct=50, capex_fixed_eur=10000)
    total, energy = finance.capex(p)
    assert energy == 250000
    assert total == pytest.approx((250000 + 60000) * 1.5 + 10000)
