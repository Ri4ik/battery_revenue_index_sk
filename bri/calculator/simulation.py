"""Revenue simulation of any battery on the historical prices in the database.

The battery is normalized to 1 MW with energy = duration (MWh), computed with the same models
as the official index (bri.models), and the result is scaled by the investor's power.
Per market and day the revenue and the equivalent full cycles (EFC) are cached in
scenario_revenue_cache; an entry is reused while the day's input hash (market_day) and the
model version are unchanged, so repeated or similar scenarios are fast.
"""

import concurrent.futures
import copy
import os

import numpy as np
import pandas as pd

from bri import MODEL_VERSION, calc, models, readiness
from bri.calculator import params as P
from bri.db import execute_values


def battery_configs(tech):
    """(battery_config, market_config) of the normalized battery in the shape bri.models expects."""
    from calculation_config_slovakia import build_configs

    _, battery, market = build_configs(tech["duration_h"], tech["cycles_per_day"])
    battery.update({
        "efficiency": tech["efficiency"], "minSOC": tech["soc_min"], "maxSOC": tech["soc_max"],
        "startSOC": tech["soc_start"], "DoD": tech["dod"], "aging_costs": tech["aging_eur_mwh"],
    })
    return battery, market


def efc_of(soc, soc_start):
    """Equivalent full cycles of a SoC series (fractions of capacity): sum of |dSoC| / 2."""
    values = np.asarray(soc, dtype=float)
    if not len(values) or np.isnan(values).all():
        return 0.0
    values = pd.Series(values).ffill().fillna(soc_start).values
    return float(np.abs(np.diff(np.concatenate([[soc_start], values]))).sum() / 2)


def _compute(task):
    market, day, data, battery, market_cfg = task
    try:
        frame = calc._compute(market, data, day, copy.deepcopy(battery), copy.deepcopy(market_cfg))["total"]
    except models.NotCalculable:
        return market, day, None, None
    return market, day, float(frame["revenue_eur"].sum()), efc_of(frame["soc"], battery["startSOC"])


def data_range(conn):
    """(first, last) delivery day with complete inputs of any market, or (None, None)."""
    with conn.cursor() as cur:
        cur.execute("SELECT min(delivery_day), max(delivery_day) FROM market_day WHERE status = 'complete'")
        first, last = cur.fetchone()
    conn.commit()
    return first, last


def day_hashes(conn, markets, start, end):
    """{(market, day): input hash} of calculable market/days in [start, end]."""
    with conn.cursor() as cur:
        cur.execute("""SELECT m.code, d.delivery_day, d.component, d.status, d.input_sha256
                       FROM market_day d JOIN market m USING (market_id)
                       WHERE m.code = ANY(%s) AND d.delivery_day BETWEEN %s AND %s""",
                    (list(markets), start, end))
        rows = cur.fetchall()
    conn.commit()
    comps = {}
    for market, day, comp, status, sha in rows:
        comps.setdefault((market, day), {})[comp] = (status, sha)
    out = {}
    for key, c in comps.items():
        if readiness.calculable({k: v[0] for k, v in c.items()}, key[0]):
            out[key] = "|".join("{}:{}".format(k, c[k][1]) for k in sorted(c) if k != "total")
    return out


def _workers():
    try:
        return max(1, int(os.environ.get("BRI_CALC_WORKERS", "0")) or min(4, os.cpu_count() or 1))
    except ValueError:
        return 1


def simulate(conn, tech, markets, start, end, progress=None, workers=None):
    """DataFrame market, delivery_day, revenue_eur_mw, efc for calculable days (normalized 1 MW)."""
    thash = P.tech_hash_of(tech)
    hashes = day_hashes(conn, markets, start, end)
    with conn.cursor() as cur:
        cur.execute("""SELECT market, delivery_day, input_sha256, revenue_eur_mw, efc
                       FROM scenario_revenue_cache
                       WHERE tech_hash = %s AND model_version = %s AND market = ANY(%s)
                         AND delivery_day BETWEEN %s AND %s""",
                    (thash, MODEL_VERSION, list(markets), start, end))
        cached = {(m, d): (sha, rev, efc) for m, d, sha, rev, efc in cur.fetchall()}
    conn.commit()

    results = {}
    todo = []
    for key, sha in sorted(hashes.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        hit = cached.get(key)
        if hit and hit[0] == sha:
            results[key] = (hit[1], hit[2])
        else:
            todo.append(key)

    battery, market_cfg = battery_configs(tech)
    total = len(todo)
    workers = workers or _workers()
    pool = concurrent.futures.ProcessPoolExecutor(workers) if workers > 1 and total > 20 else None
    try:
        for offset in range(0, total, 60):
            chunk = todo[offset:offset + 60]
            tasks = []
            with conn.cursor() as cur:
                for market, day in chunk:
                    data, _ = calc._load_inputs(cur, market, day)
                    tasks.append((market, day, data, battery, market_cfg))
            conn.commit()
            computed = list(pool.map(_compute, tasks)) if pool else [_compute(t) for t in tasks]
            rows = []
            for market, day, revenue, efc in computed:
                if revenue is None:
                    continue
                results[(market, day)] = (revenue, efc)
                rows.append((thash, market, day, MODEL_VERSION, hashes[(market, day)], revenue, efc))
            if rows:
                with conn.cursor() as cur:
                    execute_values(cur, """INSERT INTO scenario_revenue_cache (tech_hash, market, delivery_day,
                                               model_version, input_sha256, revenue_eur_mw, efc) VALUES %s
                                           ON CONFLICT (tech_hash, market, delivery_day) DO UPDATE SET
                                               model_version = EXCLUDED.model_version,
                                               input_sha256 = EXCLUDED.input_sha256,
                                               revenue_eur_mw = EXCLUDED.revenue_eur_mw,
                                               efc = EXCLUDED.efc, computed_at = now()""", rows)
                conn.commit()
            if progress:
                progress(min(offset + len(chunk), total), total)
    finally:
        if pool:
            pool.shutdown()

    frame = pd.DataFrame(
        [(m, d, rev, efc) for (m, d), (rev, efc) in results.items()],
        columns=["market", "delivery_day", "revenue_eur_mw", "efc"])
    return frame.sort_values(["delivery_day", "market"]).reset_index(drop=True)


def apply_strategy(frame, strategy):
    """Daily series of the strategy: delivery_day, market, revenue_eur_mw, efc.

    One market: its calculable days. BEST: the market with the highest revenue of each day
    (an upper bound - the market choice is known only afterwards).
    """
    if frame.empty:
        return frame.copy()
    if strategy != "BEST":
        return frame[frame["market"] == strategy].reset_index(drop=True)
    idx = frame.groupby("delivery_day")["revenue_eur_mw"].idxmax()
    return frame.loc[idx].sort_values("delivery_day").reset_index(drop=True)
