"""Investor scenarios: create, run asynchronously, store and read results.

A scenario is computed in its own process (python -m bri.calculator run <id>), so a long
simulation neither blocks nor dies with a web worker. The number of scenarios running at the
same time is limited (BRI_CALC_MAX_RUNNING) to protect the server.
"""

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

import pandas as pd
from psycopg2.extras import Json

from bri import MODEL_VERSION
from bri.calculator import finance, params as P, simulation
from bri.db import execute_values

REPO = Path(__file__).resolve().parents[2]
MAX_RUNNING = int(os.environ.get("BRI_CALC_MAX_RUNNING", "2"))
DAYS_PER_YEAR = 365.0
YEAR_COLUMNS = ["year", "soh_start", "soh_end", "efc", "discharged_mwh", "revenue_gross", "aggregator_fee",
                "grid_fees", "opex", "ebitda", "capex", "depreciation", "interest", "principal", "tax",
                "cf_project", "cf_equity", "dscr"]


class Busy(RuntimeError):
    """Too many scenarios are running."""


def markets_of(strategy):
    return P.MARKETS if strategy == "BEST" else (strategy,)


def mark_stale(conn, older_than_minutes=30):
    with conn.cursor() as cur:
        cur.execute("""UPDATE calc_scenario SET status = 'failed', finished_at = now(),
                              message = 'výpočet bol prerušený'
                       WHERE status IN ('queued', 'running')
                         AND created_at < now() - make_interval(mins => %s)""", (older_than_minutes,))
    conn.commit()


def create(conn, raw):
    """Validate input and store a queued scenario; returns scenario_id. Raises ParamError / Busy."""
    first, last = simulation.data_range(conn)
    p = P.validate(raw, first, last)
    mark_stale(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM calc_scenario WHERE status IN ('queued', 'running')")
        if cur.fetchone()[0] >= MAX_RUNNING:
            conn.commit()
            raise Busy("Práve beží {} výpočtov, skúste to o chvíľu.".format(MAX_RUNNING))
        cur.execute("""INSERT INTO calc_scenario (params, tech_hash, model_version, period_start, period_end)
                       VALUES (%s, %s, %s, %s, %s) RETURNING scenario_id""",
                    (Json(p), P.tech_hash(p), MODEL_VERSION, p["period_start"], p["period_end"]))
        scenario_id = cur.fetchone()[0]
    conn.commit()
    return scenario_id


def launch(scenario_id):
    """Start the calculation in a separate process (does not wait)."""
    return subprocess.Popen(
        [sys.executable, "-m", "bri.calculator", "run", str(scenario_id)],
        cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _set(conn, scenario_id, **fields):
    cols = ", ".join("{} = %s".format(k) for k in fields)
    with conn.cursor() as cur:
        cur.execute("UPDATE calc_scenario SET {} WHERE scenario_id = %s".format(cols),
                    list(fields.values()) + [scenario_id])
    conn.commit()


def evaluate(p, daily_all):
    """Pure part of a scenario: strategy, annualization, projection, sensitivity.

    daily_all: simulation result (market, delivery_day, revenue_eur_mw, efc) of the normalized battery.
    Returns (daily, years, summary).
    """
    daily = simulation.apply_strategy(daily_all, p["strategy"]).copy()
    if daily.empty:
        raise ValueError("Pre zvolené obdobie a trh nie sú v databáze žiadne vypočítateľné dni.")
    daily["revenue_eur"] = daily["revenue_eur_mw"] * p["power_mw"]
    annual_revenue = float(daily["revenue_eur"].mean() * DAYS_PER_YEAR)
    annual_efc = float(daily["efc"].mean() * DAYS_PER_YEAR)

    years, kpi = finance.project(p, annual_revenue, annual_efc)
    by_market = []
    for market, group in daily_all.groupby("market"):
        by_market.append({
            "market": market,
            "days": int(len(group)),
            "annual_revenue_eur": float(group["revenue_eur_mw"].mean() * DAYS_PER_YEAR * p["power_mw"]),
            "annual_efc": float(group["efc"].mean() * DAYS_PER_YEAR),
            "chosen_days": int((daily["market"] == market).sum()),
        })
    by_market.sort(key=lambda r: r["annual_revenue_eur"], reverse=True)
    expected_days = (pd.Timestamp(p["period_end"]) - pd.Timestamp(p["period_start"])).days + 1
    summary = {
        "kpi": kpi,
        "annual_revenue_eur": annual_revenue,
        "annual_efc": annual_efc,
        "days_used": int(len(daily)),
        "days_in_period": int(expected_days),
        "by_market": by_market,
        "sensitivity": finance.sensitivity(p, annual_revenue, annual_efc),
    }
    return daily, years, summary


def run(conn, scenario_id, log=print):
    """Compute a stored scenario; returns its final status."""
    with conn.cursor() as cur:
        cur.execute("SELECT params, status FROM calc_scenario WHERE scenario_id = %s", (scenario_id,))
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise ValueError("scenario {} not found".format(scenario_id))
    p, status = row
    if status not in ("queued", "running"):
        return status
    _set(conn, scenario_id, status="running", started_at=pd.Timestamp.now(tz="UTC").to_pydatetime(),
         message="simulácia výnosov", progress=0.0)
    try:
        def progress(done, total):
            _set(conn, scenario_id, progress=0.95 * done / max(total, 1),
                 message="simulácia výnosov: {} z {} dní a trhov".format(done, total))

        daily_all = simulation.simulate(conn, P.tech(p), markets_of(p["strategy"]),
                                        p["period_start"], p["period_end"], progress=progress)
        daily, years, summary = evaluate(p, daily_all)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scenario_daily WHERE scenario_id = %s", (scenario_id,))
            cur.execute("DELETE FROM scenario_year WHERE scenario_id = %s", (scenario_id,))
            execute_values(cur, """INSERT INTO scenario_daily (scenario_id, delivery_day, market, revenue_eur, efc)
                                   VALUES %s""",
                           [(scenario_id, r.delivery_day, r.market, float(r.revenue_eur), float(r.efc))
                            for r in daily.itertuples(index=False)])
            execute_values(cur, "INSERT INTO scenario_year (scenario_id, {}) VALUES %s".format(
                ", ".join(YEAR_COLUMNS)), [(scenario_id,) + tuple(y[c] for c in YEAR_COLUMNS) for y in years])
            cur.execute("""UPDATE calc_scenario SET status = 'success', progress = 1, message = NULL,
                                  finished_at = now(), days_used = %s, summary = %s
                           WHERE scenario_id = %s""", (summary["days_used"], Json(summary), scenario_id))
        conn.commit()
        return "success"
    except Exception as exc:
        conn.rollback()
        log(traceback.format_exc())
        _set(conn, scenario_id, status="failed", finished_at=pd.Timestamp.now(tz="UTC").to_pydatetime(),
             message=str(exc)[:1000])
        return "failed"


def load(conn, scenario_id):
    """(scenario dict, daily DataFrame, years DataFrame) or (None, None, None)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT scenario_id, created_at, status, progress, message, params, summary,
                              period_start, period_end, days_used
                       FROM calc_scenario WHERE scenario_id = %s""", (scenario_id,))
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None, None, None
        keys = ["scenario_id", "created_at", "status", "progress", "message", "params", "summary",
                "period_start", "period_end", "days_used"]
        scenario = dict(zip(keys, row))
        cur.execute("""SELECT delivery_day, market, revenue_eur, efc FROM scenario_daily
                       WHERE scenario_id = %s ORDER BY delivery_day""", (scenario_id,))
        daily = pd.DataFrame(cur.fetchall(), columns=["delivery_day", "market", "revenue_eur", "efc"])
        cur.execute("SELECT {} FROM scenario_year WHERE scenario_id = %s ORDER BY year".format(
            ", ".join(YEAR_COLUMNS)), (scenario_id,))
        years = pd.DataFrame(cur.fetchall(), columns=YEAR_COLUMNS)
    conn.commit()
    return scenario, daily, years


def dumps(obj):
    return json.dumps(obj, default=str, ensure_ascii=False, indent=2)
