"""Calculation runs: choose what to (re)compute, compute, store and publish.

A market/day is recomputed for a battery configuration when its published result was made
from a different input snapshot (input_sha256) or by a different model version.
Results are written under a new calc_run; publication happens once at the end of the run,
in the same transaction that marks the run successful, so the website never shows a
half-finished run and keeps the previous version when a run fails.
"""

import traceback
from collections import defaultdict

import pandas as pd

from bri import MODEL_VERSION, inputs, models, readiness, settings
from bri.db import execute_values

MARKET_CONFIG_KEY = {  # market_param (market, component) -> key used by the project models
    ("DA", "prices"): "DA",
    ("IDM15", "prices"): "IDM15",
    ("IDM60", "prices"): "IDM60",
    ("IMB", "prices"): "IMB",
    ("AFRR", "capacity"): "aFRR Capacity",
    ("AFRR", "energy"): "aFRR Energy",
}
MARKETS = ("DA", "IDM15", "IDM60", "IMB", "AFRR")


# ---------------------------------------------------------------- configurations

def seed_configs(conn):
    """Insert the four benchmark configurations from calculation_config_slovakia.build_configs."""
    from calculation_config_slovakia import build_configs

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM battery_config")
        if cur.fetchone()[0]:
            conn.commit()
            return 0
        config_id = 0
        for energy in (1, 2):
            for cycles in (1, 2):
                config_id += 1
                _, b, m = build_configs(energy, cycles)
                cur.execute(
                    """INSERT INTO battery_config (config_id, code, energy_mwh, power_mw, cycle_limit, efficiency,
                           soc_min, soc_max, soc_start, dod, capex_eur_kwh, service_life_years)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (config_id, "{}h_{}c".format(energy, cycles), b["energy"], b["power"], b["cycle_limit"],
                     b["efficiency"], b["minSOC"], b["maxSOC"], b["startSOC"], b["DoD"], b["costs"],
                     b["service_life"]),
                )
                for (market, component), key in MARKET_CONFIG_KEY.items():
                    p = m[key]
                    cur.execute(
                        """INSERT INTO market_param (config_id, market_id, component, power_share, capture_rate,
                               capacity_share, cycle_share, init_position)
                           SELECT %s, market_id, %s, %s, %s, %s, %s, %s FROM market WHERE code = %s""",
                        (config_id, component, p["power_share"], p["capture_rate"], p["capacity_share"],
                         p.get("cycle_share"), p.get("init_position"), market),
                    )
    conn.commit()
    return 4


def load_configs(conn):
    """{config_id: (code, battery_config, market_config)} in the shape the models expect."""
    configs = {}
    with conn.cursor() as cur:
        cur.execute("""SELECT config_id, code, energy_mwh, power_mw, cycle_limit, efficiency, soc_min, soc_max,
                              soc_start, dod, capex_eur_kwh, service_life_years FROM battery_config""")
        for row in cur.fetchall():
            cid, code, e, p, cyc, eff, smin, smax, sstart, dod, capex, life = [
                float(v) if i > 1 else v for i, v in enumerate(row)]
            battery = {"energy": e, "power": p, "cycle_limit": cyc, "efficiency": eff, "minSOC": smin,
                       "maxSOC": smax, "startSOC": sstart, "DoD": dod, "costs": capex, "service_life": life}
            battery["aging_costs"] = capex * 1000 / (life * cyc * 365)
            configs[int(cid)] = (code, battery, {})
        cur.execute("""SELECT p.config_id, m.code, p.component, p.power_share, p.capture_rate, p.capacity_share,
                              p.cycle_share, p.init_position
                       FROM market_param p JOIN market m USING (market_id)""")
        for cid, market, comp, ps, cr, cs, cyc_share, init in cur.fetchall():
            params = {"power_share": float(ps), "capture_rate": float(cr), "capacity_share": float(cs)}
            if cyc_share is not None:
                params["cycle_share"] = float(cyc_share)
            if init is not None:
                params["init_position"] = float(init)
            configs[int(cid)][2][MARKET_CONFIG_KEY[(market, comp)]] = params
    conn.commit()
    return configs


# ---------------------------------------------------------------- selection

def select_tasks(conn, days, markets=MARKETS, force=False):
    """[(market, day, [config_ids])] that need a (re)calculation."""
    with conn.cursor() as cur:
        cur.execute("""SELECT m.code, d.delivery_day, d.component, d.status, d.input_sha256
                       FROM market_day d JOIN market m USING (market_id)
                       WHERE d.delivery_day = ANY(%s)""", (list(days),))
        status = defaultdict(dict)
        for market, day, comp, st, sha in cur.fetchall():
            status[(market, day)][comp] = (st, sha)
        cur.execute("""SELECT p.config_id, m.code, p.delivery_day, r.model_version, ci.component, ci.input_sha256
                       FROM result_publication p JOIN market m USING (market_id)
                       JOIN calc_run r USING (run_id)
                       LEFT JOIN calc_input ci ON ci.run_id = p.run_id AND ci.market_id = p.market_id
                                              AND ci.delivery_day = p.delivery_day
                       WHERE p.delivery_day = ANY(%s)""", (list(days),))
        published = defaultdict(dict)
        model_of = {}
        for cid, market, day, model, comp, sha in cur.fetchall():
            model_of[(cid, market, day)] = model
            if comp:
                published[(cid, market, day)][comp] = sha
        cur.execute("SELECT config_id FROM battery_config ORDER BY config_id")
        config_ids = [r[0] for r in cur.fetchall()]
    conn.commit()

    tasks = []
    for day in days:
        for market in markets:
            comps = status.get((market, day), {})
            if not readiness.calculable({c: v[0] for c, v in comps.items()}, market):
                continue
            current = {c: v[1] for c, v in comps.items() if c != "total"}
            todo = [cid for cid in config_ids
                    if force or model_of.get((cid, market, day)) != MODEL_VERSION
                    or published.get((cid, market, day)) != current]
            if todo:
                tasks.append((market, day, todo))
    return tasks


# ---------------------------------------------------------------- execution

def _load_inputs(cur, market, day):
    if market == "AFRR":
        cap = inputs.day_afrr_capacity(cur, day)
        en = inputs.day_afrr_energy(cur, day)
        snapshot = {
            "capacity": (int(cap["batch_id"].max()),
                         inputs.frame_sha256(cap, ["direction", "block_start_h", "block_end_h", "price", "procured_mw"])),
            "energy": (int(en["batch_id"].max()),
                       inputs.frame_sha256(en, ["interval_start", "interval_end", "up_volume_mwh",
                                                "up_price_eur_mwh", "down_volume_mwh", "down_price_eur_mwh"])),
        }
        return (cap, en), snapshot
    df = inputs.day_prices(cur, market, day)
    cols = ["interval_start", "interval_end", "price"] + (["evaluation"] if market == "IMB" else [])
    return df, {"prices": (int(df["batch_id"].max()), inputs.frame_sha256(df, cols))}


def _compute(market, data, day, battery, market_cfg):
    if market == "AFRR":
        cap, en = data
        return models.afrr(cap, en, day, battery, market_cfg)
    return models.wholesale(market, data, day, battery, market_cfg[market])


def run(conn, tasks, trigger="manual", log=print):
    """Compute tasks under one calc_run; returns (run_id, days_published, errors)."""
    if not tasks:
        return None, 0, []
    configs = load_configs(conn)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO calc_run (status, trigger, code_version, model_version)
                       VALUES ('running', %s, %s, %s) RETURNING run_id""",
                    (trigger, settings.code_version(), MODEL_VERSION))
        run_id = cur.fetchone()[0]
        cur.execute("SELECT code, market_id FROM market")
        market_ids = dict(cur.fetchall())
    conn.commit()

    done, errors = [], []
    for n, (market, day, config_ids) in enumerate(tasks, 1):
        try:
            with conn.cursor() as cur:
                data, snapshot = _load_inputs(cur, market, day)
                daily_rows, interval_rows = [], []
                for cid in config_ids:
                    _, battery, market_cfg = configs[cid]
                    for component, frame in _compute(market, data, day, battery, market_cfg).items():
                        daily_rows.append((run_id, cid, market_ids[market], day, component,
                                           float(frame["revenue_eur"].sum())))
                        interval_rows.extend(
                            (run_id, cid, market_ids[market], component, s, e, float(r),
                             None if pd.isna(soc) else float(soc))
                            for s, e, r, soc in frame[["interval_start", "interval_end", "revenue_eur", "soc"]]
                            .itertuples(index=False))
                execute_values(cur, """INSERT INTO calc_input (run_id, market_id, delivery_day, component,
                                           input_max_batch_id, input_sha256) VALUES %s""",
                               [(run_id, market_ids[market], day, comp, mb, sha)
                                for comp, (mb, sha) in snapshot.items()])
                execute_values(cur, """INSERT INTO result_daily (run_id, config_id, market_id, delivery_day,
                                           component, revenue_eur) VALUES %s""", daily_rows)
                execute_values(cur, """INSERT INTO result_interval (run_id, config_id, market_id, component,
                                           interval_start, interval_end, revenue_eur, soc) VALUES %s""",
                               interval_rows, page_size=5000)
            conn.commit()
            done.append((market, day, config_ids))
        except models.NotCalculable as exc:
            conn.rollback()
            errors.append("{} {}: {}".format(market, day, exc))
        except Exception as exc:
            conn.rollback()
            errors.append("{} {}: {}".format(market, day, exc))
            log(traceback.format_exc())
        if n % 100 == 0:
            log("  calc {}/{}".format(n, len(tasks)))

    with conn.cursor() as cur:
        if done:
            execute_values(cur, """INSERT INTO result_publication (config_id, market_id, delivery_day, run_id)
                                   VALUES %s ON CONFLICT (config_id, market_id, delivery_day) DO UPDATE
                                   SET run_id = EXCLUDED.run_id, published_at = now()""",
                           [(cid, market_ids[m], d, run_id) for m, d, cids in done for cid in cids])
        cur.execute("""UPDATE calc_run SET status = %s, finished_at = now(), error_summary = %s
                       WHERE run_id = %s""",
                    ("success" if done or not errors else "failed",
                     "\n".join(errors)[:4000] or None, run_id))
    conn.commit()
    return run_id, len(done), errors


def mark_stale_runs(conn, older_than_minutes=360):
    with conn.cursor() as cur:
        cur.execute("""UPDATE calc_run SET status = 'failed', finished_at = now(),
                              error_summary = 'interrupted (process stopped while running)'
                       WHERE status = 'running' AND started_at < now() - make_interval(mins => %s)""",
                    (older_than_minutes,))
    conn.commit()
