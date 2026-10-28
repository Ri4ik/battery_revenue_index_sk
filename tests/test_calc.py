"""Calculation, publication, versions and the pipeline lock."""

import copy
import json

import pandas as pd
import pytest

from bri import calc, db, inputs, models, readiness, artifacts
from bri.importers import okte, seps
from bri.timeutil import day_bounds
from calculation_config_slovakia import build_configs
from markets.wholesale_market import WholesaleMarket
from tests.conftest import D, TEST_URL, fixture_bytes, seps_xlsx


def _published(conn, market, day):
    with conn.cursor() as cur:
        cur.execute("""SELECT config_code, revenue_eur, run_id FROM published_daily_total
                       WHERE market = %s AND delivery_day = %s ORDER BY config_code""", (market, day))
        rows = cur.fetchall()
    conn.commit()
    return rows


def test_da_quarter_hours_publish_one_version_and_recompute_on_change(conn, monkeypatch):
    day = D(2025, 10, 1)
    payload = fixture_bytes("okte_dam_2025-10-01.json")
    monkeypatch.setattr(okte, "_get", lambda url, params: payload)
    okte.import_da(conn, day)
    readiness.refresh(conn, [day], markets=("DA",))
    run1, n, errors = calc.run(conn, calc.select_tasks(conn, [day], markets=("DA",)), trigger="test")
    assert n == 1 and not errors
    first = _published(conn, "DA", day)
    assert len(first) == 4 and {r[2] for r in first} == {run1}

    # interval results keep the native 15-minute length
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*), min(interval_end - interval_start), max(interval_end - interval_start)
                       FROM result_interval WHERE run_id = %s AND config_id = 1""", (run1,))
        count, dmin, dmax = cur.fetchone()
    conn.commit()
    assert count == 96 and dmin == dmax == pd.Timedelta(minutes=15)

    # unchanged inputs: nothing to do
    assert calc.select_tasks(conn, [day], markets=("DA",)) == []

    # a changed price creates a new run; the website switches to it, the old run stays as history
    changed = json.loads(payload)
    changed[40]["price"] = 999.0
    monkeypatch.setattr(okte, "_get", lambda url, params: json.dumps(changed).encode())
    okte.import_da(conn, day)
    readiness.refresh(conn, [day], markets=("DA",))
    run2, n, _ = calc.run(conn, calc.select_tasks(conn, [day], markets=("DA",)), trigger="test")
    second = _published(conn, "DA", day)
    assert {r[2] for r in second} == {run2} and run2 != run1
    with conn.cursor() as cur:
        cur.execute("SELECT count(DISTINCT run_id) FROM result_daily WHERE delivery_day = %s", (day,))
        assert cur.fetchone()[0] == 2
    conn.commit()


def test_failed_calculation_keeps_previous_publication(conn, monkeypatch):
    day = D(2025, 9, 30)
    monkeypatch.setattr(okte, "_get", lambda url, params: fixture_bytes("okte_dam_2025-09-30.json"))
    okte.import_da(conn, day)
    readiness.refresh(conn, [day], markets=("DA",))
    run1, _, _ = calc.run(conn, calc.select_tasks(conn, [day], markets=("DA",)), trigger="test")
    monkeypatch.setattr(models, "wholesale", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    run2, n, errors = calc.run(conn, [("DA", day, [1, 2, 3, 4])], trigger="test")
    assert n == 0 and errors
    assert {r[2] for r in _published(conn, "DA", day)} == {run1}


def _afrr_inputs(conn, day, quarters):
    content = seps_xlsx(day, quarters=quarters)
    seps.import_xlsx(conn, content, "test.xlsx", "local")
    rows = [(day, d, h, h + 4, 10.0, 100.0) for d in ("POS", "NEG") for h in range(0, 24, 4)]
    artifact = artifacts.store_artifact(conn, "ENTSOE", "api", repr(rows).encode(), "txt")
    artifacts.import_artifact(conn, artifact, "test", lambda cur, b: inputs.write_afrr_capacity_rows(cur, rows, b))


def test_afrr_total_is_published_once_and_equals_its_components(conn):
    day = D(2025, 6, 10)
    _afrr_inputs(conn, day, 96)
    readiness.refresh(conn, [day], markets=("AFRR",))
    run_id, n, errors = calc.run(conn, calc.select_tasks(conn, [day], markets=("AFRR",)), trigger="test")
    assert n == 1 and not errors
    with conn.cursor() as cur:
        cur.execute("""SELECT component, revenue_eur FROM result_daily
                       WHERE run_id = %s AND config_id = 1 AND delivery_day = %s""", (run_id, day))
        comp = dict(cur.fetchall())
    conn.commit()
    published = _published(conn, "AFRR", day)
    assert len(published) == 4  # one row per configuration, components are not added on top
    assert published[0][1] == pytest.approx(comp["capacity"] + comp["energy"])
    assert comp["total"] == pytest.approx(comp["capacity"] + comp["energy"])


def test_afrr_waits_for_energy(conn):
    day = D(2025, 6, 11)
    rows = [(day, d, h, h + 4, 10.0, 100.0) for d in ("POS", "NEG") for h in range(0, 24, 4)]
    artifact = artifacts.store_artifact(conn, "ENTSOE", "api", b"cap-only", "txt")
    artifacts.import_artifact(conn, artifact, "test", lambda cur, b: inputs.write_afrr_capacity_rows(cur, rows, b))
    readiness.refresh(conn, [day], markets=("AFRR",))
    assert calc.select_tasks(conn, [day], markets=("AFRR",)) == []


def test_afrr_capacity_uses_real_block_length_on_dst_day(conn):
    day = D(2025, 10, 26)  # 25-hour day: block 00-04 lasts 5 hours
    _afrr_inputs(conn, day, 100)
    with conn.cursor() as cur:
        cap = inputs.day_afrr_capacity(cur, day)
        en = inputs.day_afrr_energy(cur, day)
    conn.commit()
    _, battery, market_cfg = calc.load_configs(conn)[1]
    out = models.afrr(cap, en, day, battery, market_cfg)
    afrr_power = min(battery["energy"] / 2, battery["power"] * market_cfg["aFRR Capacity"]["power_share"])
    assert out["capacity"]["revenue_eur"].sum() == pytest.approx(20.0 * afrr_power * 25)
    assert len(out["total"]) == 100


def _trade(method, prices, energy, cycles):
    _, b, m = build_configs(energy, cycles)
    w = WholesaleMarket.__new__(WholesaleMarket)
    w.prices = {}
    bc, mc = copy.deepcopy(b), copy.deepcopy(m["DA"])
    mc["t_delivery"] = (prices.index[1] - prices.index[0]) / pd.Timedelta(hours=1)
    w.set_marketable_power_da(bc, mc)
    trades, profit, result = getattr(w, method)(prices.copy(), bc, mc)
    return trades, profit, result, mc, bc


def _fixture_prices(name, day):
    rows = okte.parse_delivery_rows(fixture_bytes(name), day, "price")
    return pd.DataFrame({"price": [r[2] for r in rows]},
                        index=pd.DatetimeIndex([r[0] for r in rows]).tz_convert(None))


def test_fast_model_equals_reference_model_for_all_configs():
    """Fast spread model == original loop model, also for 2 MWh batteries (unit fix)."""
    prices = _fixture_prices("okte_dam_2025-09-30.json", D(2025, 9, 30))
    for energy in (1, 2):
        for cycles in (1, 2):
            ref = _trade("calculate_market_trades", prices, energy, cycles)[1]
            fast = _trade("calculate_market_trades_fast", prices, energy, cycles)[1]
            assert fast == pytest.approx(ref), (energy, cycles)


def test_reference_model_never_trades_in_the_first_interval():
    """Known quirk of the original loop model; the fast model may use the first interval."""
    prices = _fixture_prices("okte_dam_2025-03-30.json", D(2025, 3, 30))
    ref_trades, ref, _, _, _ = _trade("calculate_market_trades", prices, 1, 1)
    fast_trades, fast, _, _, _ = _trade("calculate_market_trades_fast", prices, 1, 1)
    first = prices.index[0]
    assert first not in set(ref_trades["t_buy"]) | set(ref_trades["t_sell"])
    assert first in set(fast_trades["t_buy"]) | set(fast_trades["t_sell"])
    assert fast >= ref


def test_fast_model_respects_power_limit_on_quarter_hours():
    """No interval may move more energy than marketable power x interval length."""
    prices = _fixture_prices("okte_dam_2025-10-01.json", D(2025, 10, 1))
    for energy in (1, 2):
        for cycles in (1, 2):
            _, _, result, mc, bc = _trade("calculate_market_trades_fast", prices, energy, cycles)
            soc = [bc["startSOC"]] + list(result["soc"])
            max_step = mc["marketable_power"] * mc["t_delivery"] / bc["energy"]
            assert max(abs(b - a) for a, b in zip(soc, soc[1:])) <= max_step + 1e-9, (energy, cycles)


def test_only_one_pipeline_can_hold_the_lock(conn):
    other = db.connect(TEST_URL)
    try:
        assert db.try_pipeline_lock(conn)
        assert not db.try_pipeline_lock(other)
        db.release_pipeline_lock(conn)
        assert db.try_pipeline_lock(other)
        db.release_pipeline_lock(other)
    finally:
        other.close()
