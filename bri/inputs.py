"""Writing input revisions and reading the current input values of a delivery day.

Input rows are never updated. A value is written only when it differs from the current
value of the same key, so re-importing identical data adds nothing.
"""

import hashlib
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from bri.db import execute_values
from bri.timeutil import day_bounds

PRICE_TABLES = {"DA": "price_da", "IDM15": "price_idm15", "IDM60": "price_idm60"}


def q4(value):
    """Round like numeric(12,4) so comparisons with stored values are exact."""
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def q6(value):
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _check_no_overlap(intervals, what):
    """intervals: list of (start, end); raises ValueError on duplicates or overlaps."""
    ordered = sorted(intervals)
    for (s1, e1), (s2, e2) in zip(ordered, ordered[1:]):
        if s2 < e1:
            raise ValueError("{}: overlapping intervals {}..{} and {}..{}".format(what, s1, e1, s2, e2))


def write_price_rows(cur, market, rows, batch_id):
    """rows: [(interval_start, interval_end, price)] for DA / IDM15 / IDM60."""
    table = PRICE_TABLES[market]
    _check_no_overlap([(r[0], r[1]) for r in rows], market)
    if not rows:
        return 0
    starts = [r[0] for r in rows]
    cur.execute(
        "SELECT interval_start, interval_end, price_eur_mwh FROM {}_current "
        "WHERE interval_start = ANY(%s)".format(table),
        (starts,),
    )
    current = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    new = []
    for start, end, price in rows:
        value = (pd.Timestamp(end).to_pydatetime(), q4(price))
        old = current.get(pd.Timestamp(start).to_pydatetime())
        if old is None or (pd.Timestamp(old[0]).to_pydatetime(), old[1]) != value:
            new.append((start, end, q4(price), batch_id))
    if new:
        execute_values(
            cur,
            "INSERT INTO {} (interval_start, interval_end, price_eur_mwh, batch_id) VALUES %s".format(table),
            new,
        )
    return len(new)


def write_imb_rows(cur, rows, batch_id):
    """rows: [(interval_start, interval_end, evaluation, price)]."""
    _check_no_overlap([(r[0], r[1]) for r in rows], "IMB")
    if not rows:
        return 0
    starts = [r[0] for r in rows]
    cur.execute(
        """SELECT DISTINCT ON (p.interval_start, p.evaluation)
                  p.interval_start, p.evaluation, p.interval_end, p.price_eur_mwh
           FROM price_imb p JOIN import_batch b USING (batch_id)
           WHERE b.status = 'success' AND p.interval_start = ANY(%s)
           ORDER BY p.interval_start, p.evaluation, p.batch_id DESC""",
        (starts,),
    )
    current = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}
    new = []
    for start, end, evaluation, price in rows:
        old = current.get((pd.Timestamp(start).to_pydatetime(), evaluation))
        value = (pd.Timestamp(end).to_pydatetime(), q4(price))
        if old is None or (pd.Timestamp(old[0]).to_pydatetime(), old[1]) != value:
            new.append((start, end, evaluation, q4(price), batch_id))
    if new:
        execute_values(
            cur,
            "INSERT INTO price_imb (interval_start, interval_end, evaluation, price_eur_mwh, batch_id) VALUES %s",
            new,
        )
    return len(new)


def write_afrr_capacity_rows(cur, rows, batch_id):
    """rows: [(delivery_day, direction, block_start_h, block_end_h, price_eur_mw_h, procured_mw)]."""
    if not rows:
        return 0
    days = sorted({r[0] for r in rows})
    cur.execute(
        """SELECT delivery_day, direction, block_start_h, block_end_h, price_eur_mw_h, procured_mw
           FROM afrr_capacity_current WHERE delivery_day = ANY(%s)""",
        (days,),
    )
    current = {(r[0], r[1], r[2]): (r[3], r[4], r[5]) for r in cur.fetchall()}
    new = []
    for day, direction, b_start, b_end, price, mw in rows:
        value = (int(b_end), q4(price), q4(mw))
        if current.get((day, direction, int(b_start))) != value:
            new.append((day, direction, int(b_start), int(b_end), q4(price), q4(mw), batch_id))
    if new:
        execute_values(
            cur,
            """INSERT INTO afrr_capacity (delivery_day, direction, block_start_h, block_end_h,
                                          price_eur_mw_h, procured_mw, batch_id) VALUES %s""",
            new,
        )
    return len(new)


def write_afrr_energy_rows(cur, rows, batch_id):
    """rows: [(interval_start, interval_end, up_mwh, up_price, down_mwh, down_price)]."""
    _check_no_overlap([(r[0], r[1]) for r in rows], "aFRR energy")
    if not rows:
        return 0
    starts = [r[0] for r in rows]
    cur.execute(
        """SELECT interval_start, interval_end, up_volume_mwh, up_price_eur_mwh,
                  down_volume_mwh, down_price_eur_mwh
           FROM afrr_energy_current WHERE interval_start = ANY(%s)""",
        (starts,),
    )
    current = {r[0]: (pd.Timestamp(r[1]).to_pydatetime(),) + tuple(r[2:]) for r in cur.fetchall()}
    new = []
    for start, end, up_v, up_p, down_v, down_p in rows:
        value = (pd.Timestamp(end).to_pydatetime(), q6(up_v), q4(up_p), q6(down_v), q4(down_p))
        if current.get(pd.Timestamp(start).to_pydatetime()) != value:
            new.append((start, end, q6(up_v), q4(up_p), q6(down_v), q4(down_p), batch_id))
    if new:
        execute_values(
            cur,
            """INSERT INTO afrr_energy (interval_start, interval_end, up_volume_mwh, up_price_eur_mwh,
                                        down_volume_mwh, down_price_eur_mwh, batch_id) VALUES %s""",
            new,
        )
    return len(new)


# ---------------------------------------------------------------- reading current values

def _frame(cur, sql, params, columns):
    cur.execute(sql, params)
    df = pd.DataFrame(cur.fetchall(), columns=columns)
    for col in ("interval_start", "interval_end"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True)
    return df


def day_prices(cur, market, day, as_of_batch=None):
    """Current (or as-of a batch) values of one delivery day.

    Columns: interval_start, interval_end, price (+ evaluation for IMB), batch_id.
    """
    start, end = day_bounds(day)
    limit = "AND p.batch_id <= %(asof)s" if as_of_batch else ""
    params = {"s": start.to_pydatetime(), "e": end.to_pydatetime(), "asof": as_of_batch}
    if market == "IMB":
        sql = """
            SELECT DISTINCT ON (p.interval_start) p.interval_start, p.interval_end,
                   p.price_eur_mwh, p.evaluation, p.batch_id
            FROM price_imb p JOIN import_batch b USING (batch_id) JOIN imb_evaluation e USING (evaluation)
            WHERE b.status = 'success' AND p.interval_start >= %(s)s AND p.interval_start < %(e)s {}
            ORDER BY p.interval_start, e.rank DESC, p.batch_id DESC""".format(limit)
        cols = ["interval_start", "interval_end", "price", "evaluation", "batch_id"]
    else:
        sql = """
            SELECT DISTINCT ON (p.interval_start) p.interval_start, p.interval_end,
                   p.price_eur_mwh, p.batch_id
            FROM {} p JOIN import_batch b USING (batch_id)
            WHERE b.status = 'success' AND p.interval_start >= %(s)s AND p.interval_start < %(e)s {}
            ORDER BY p.interval_start, p.batch_id DESC""".format(PRICE_TABLES[market], limit)
        cols = ["interval_start", "interval_end", "price", "batch_id"]
    df = _frame(cur, sql, params, cols).sort_values("interval_start").reset_index(drop=True)
    df["price"] = df["price"].astype(float)
    return df


def day_afrr_capacity(cur, day, as_of_batch=None):
    limit = "AND c.batch_id <= %(asof)s" if as_of_batch else ""
    sql = """
        SELECT DISTINCT ON (c.direction, c.block_start_h) c.direction, c.block_start_h, c.block_end_h,
               c.price_eur_mw_h, c.procured_mw, c.batch_id
        FROM afrr_capacity c JOIN import_batch b USING (batch_id)
        WHERE b.status = 'success' AND c.delivery_day = %(d)s {}
        ORDER BY c.direction, c.block_start_h, c.batch_id DESC""".format(limit)
    df = _frame(cur, sql, {"d": day, "asof": as_of_batch},
                ["direction", "block_start_h", "block_end_h", "price", "procured_mw", "batch_id"])
    df["price"] = df["price"].astype(float)
    df["procured_mw"] = df["procured_mw"].astype(float)
    return df


def day_afrr_energy(cur, day, as_of_batch=None):
    start, end = day_bounds(day)
    limit = "AND x.batch_id <= %(asof)s" if as_of_batch else ""
    sql = """
        SELECT DISTINCT ON (x.interval_start) x.interval_start, x.interval_end, x.up_volume_mwh,
               x.up_price_eur_mwh, x.down_volume_mwh, x.down_price_eur_mwh, x.batch_id
        FROM afrr_energy x JOIN import_batch b USING (batch_id)
        WHERE b.status = 'success' AND x.interval_start >= %(s)s AND x.interval_start < %(e)s {}
        ORDER BY x.interval_start, x.batch_id DESC""".format(limit)
    df = _frame(cur, sql, {"s": start.to_pydatetime(), "e": end.to_pydatetime(), "asof": as_of_batch},
                ["interval_start", "interval_end", "up_volume_mwh", "up_price_eur_mwh",
                 "down_volume_mwh", "down_price_eur_mwh", "batch_id"])
    for col in ("up_volume_mwh", "up_price_eur_mwh", "down_volume_mwh", "down_price_eur_mwh"):
        df[col] = df[col].astype(float)
    return df.sort_values("interval_start").reset_index(drop=True)


def frame_sha256(df, columns):
    """Stable hash of the values the calculation uses."""
    h = hashlib.sha256()
    for row in df[columns].itertuples(index=False):
        h.update("|".join(
            pd.Timestamp(v).isoformat() if isinstance(v, (pd.Timestamp,)) else repr(v) for v in row
        ).encode())
        h.update(b"\n")
    return h.hexdigest()
