"""Input readiness of each market and delivery day (table market_day).

A day is complete when its current intervals cover the local day [00:00, 24:00) without
gaps or overlaps - checked on real time, so 23/24/25-hour days need no special case.
"""

import pandas as pd

from bri import inputs
from bri.db import execute_values
from bri.timeutil import day_bounds

PRICE_MARKETS = ("DA", "IDM15", "IDM60", "IMB")
# Intraday products without trades have no price; the model interpolates such gaps
# when they are at most this share of the day.
IDM_MAX_GAP_SHARE = 0.25
IDM_STEP = {"IDM15": pd.Timedelta(minutes=15), "IDM60": pd.Timedelta(hours=1)}


def coverage(df, day):
    """(status, detail) of interval rows against the local day."""
    if df.empty:
        return "missing", "žiadne dáta"
    start, end = day_bounds(day)
    starts, ends = list(df["interval_start"]), list(df["interval_end"])
    for s, e, s_next in zip(starts, ends, starts[1:]):
        if s_next < e:
            return "invalid", "prekrývajúce sa intervaly {} a {}".format(s, s_next)
    covered = sum(((e - s) for s, e in zip(starts, ends)), pd.Timedelta(0))
    if starts[0] == start and ends[-1] == end and covered == end - start:
        return "complete", "{} intervalov".format(len(df))
    missing = (end - start) - covered
    return "incomplete", "chýba {} z {}".format(missing, end - start)


def idm_gaps(df, market, day):
    """Number of expected product intervals without a price, and expected count."""
    start, end = day_bounds(day)
    expected = int((end - start) / IDM_STEP[market])
    return expected - len(df), expected


def check_day(cur, market, day):
    """Rows to upsert into market_day for one market and day."""
    out = []
    if market in PRICE_MARKETS:
        df = inputs.day_prices(cur, market, day)
        status, detail = coverage(df, day)
        if market in IDM_STEP and status == "incomplete" and not df.empty:
            gaps, expected = idm_gaps(df, market, day)
            detail = "{} z {} produktov bez obchodu".format(gaps, expected)
        if market == "IMB" and not df.empty:
            detail += "; hodnotenie: " + ", ".join(sorted(df["evaluation"].unique()))
        cols = ["interval_start", "interval_end", "price"] + (["evaluation"] if market == "IMB" else [])
        out.append((market, "prices", status, detail,
                    int(df["batch_id"].max()) if not df.empty else None,
                    inputs.frame_sha256(df, cols) if not df.empty else None))
    else:  # AFRR
        cap = inputs.day_afrr_capacity(cur, day)
        status, detail = capacity_coverage(cap, day)
        out.append(("AFRR", "capacity", status, detail,
                    int(cap["batch_id"].max()) if not cap.empty else None,
                    inputs.frame_sha256(cap, ["direction", "block_start_h", "block_end_h", "price", "procured_mw"])
                    if not cap.empty else None))
        en = inputs.day_afrr_energy(cur, day)
        status, detail = coverage(en, day)
        out.append(("AFRR", "energy", status, detail,
                    int(en["batch_id"].max()) if not en.empty else None,
                    inputs.frame_sha256(en, ["interval_start", "interval_end", "up_volume_mwh", "up_price_eur_mwh",
                                             "down_volume_mwh", "down_price_eur_mwh"]) if not en.empty else None))
    return out


def capacity_coverage(cap, day):
    """Each direction needs contiguous blocks from local 00:00 to 24:00."""
    if cap.empty:
        return "missing", "žiadne dáta"
    for direction in ("POS", "NEG"):
        blocks = cap[cap["direction"] == direction].sort_values("block_start_h")
        if blocks.empty:
            return "incomplete", "chýba smer {}".format(direction)
        hours = list(zip(blocks["block_start_h"], blocks["block_end_h"]))
        if hours[0][0] != 0 or hours[-1][1] != 24 or any(a[1] != b[0] for a, b in zip(hours, hours[1:])):
            return "incomplete", "bloky {} nepokrývajú celý deň".format(direction)
    return "complete", "{} blokov".format(len(cap))


def refresh(conn, days, markets=PRICE_MARKETS + ("AFRR",)):
    """Recompute market_day for the given days."""
    rows = []
    with conn.cursor() as cur:
        for day in days:
            for market in markets:
                for m, comp, status, detail, max_batch, sha in check_day(cur, market, day):
                    rows.append((m, day, comp, status, detail, max_batch, sha))
        execute_values(
            cur,
            """INSERT INTO market_day (market_id, delivery_day, component, status, detail,
                                       input_max_batch_id, input_sha256, checked_at)
               SELECT m.market_id, v.day::date, v.comp, v.status, v.detail, v.max_batch::bigint, v.sha, now()
               FROM (VALUES %s) AS v(market, day, comp, status, detail, max_batch, sha)
               JOIN market m ON m.code = v.market
               ON CONFLICT (market_id, delivery_day, component) DO UPDATE SET
                   status = EXCLUDED.status, detail = EXCLUDED.detail,
                   input_max_batch_id = EXCLUDED.input_max_batch_id,
                   input_sha256 = EXCLUDED.input_sha256, checked_at = now()""",
            rows,
        )
    conn.commit()
    return len(rows)


def calculable(status_by_component, market, detail=None):
    """Whether a market/day has inputs the model can use."""
    if market == "AFRR":
        return status_by_component.get("capacity") == "complete" and status_by_component.get("energy") == "complete"
    st = status_by_component.get("prices")
    return st == "complete" or (market in IDM_STEP and st == "incomplete")
