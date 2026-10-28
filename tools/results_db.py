"""
Database storage for daily revenue results (used by the web dashboard).

Connection string comes from the DATABASE_URL environment variable, e.g.
    postgresql+psycopg2://bri:bri@db:5432/battery_revenue_index
"""

import os
from typing import Iterable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import (
    Column,
    Date,
    Float,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    and_,
    create_engine,
    func,
    select,
    text,
)

metadata = MetaData()

daily_revenue = Table(
    "daily_revenue",
    metadata,
    Column("dataset", String(255), nullable=False),
    Column("system_type", Integer, nullable=False),
    Column("cycle_limit", Integer, nullable=False),
    Column("day", Date, nullable=False),
    Column("market", String(64), nullable=False),
    Column("daily_revenue_eur", Float, nullable=False),
    PrimaryKeyConstraint("dataset", "system_type", "cycle_limit", "day", "market"),
)


def database_url() -> Optional[str]:
    return os.environ.get("DATABASE_URL") or None


def get_engine(url: Optional[str] = None):
    url = url or database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    return create_engine(url, pool_pre_ping=True)


def init_schema(engine) -> None:
    metadata.create_all(engine)


def combo_exists(engine, dataset: str, system_type: int, cycle_limit: int) -> bool:
    q = (
        select([func.count()])
        .select_from(daily_revenue)
        .where(
            and_(
                daily_revenue.c.dataset == dataset,
                daily_revenue.c.system_type == system_type,
                daily_revenue.c.cycle_limit == cycle_limit,
            )
        )
    )
    with engine.connect() as conn:
        return conn.execute(q).scalar() > 0


def replace_combo(
    engine, dataset: str, system_type: int, cycle_limit: int, df: pd.DataFrame
) -> int:
    """Replace all rows of one dataset/battery combo with df (day, market, daily_revenue_eur)."""
    rows = [
        {
            "dataset": dataset,
            "system_type": system_type,
            "cycle_limit": cycle_limit,
            "day": pd.Timestamp(r.day).date(),
            "market": r.market,
            "daily_revenue_eur": float(r.daily_revenue_eur),
        }
        for r in df.itertuples(index=False)
    ]
    with engine.begin() as conn:
        conn.execute(
            daily_revenue.delete().where(
                and_(
                    daily_revenue.c.dataset == dataset,
                    daily_revenue.c.system_type == system_type,
                    daily_revenue.c.cycle_limit == cycle_limit,
                )
            )
        )
        if rows:
            conn.execute(daily_revenue.insert(), rows)
    return len(rows)


def upsert_rows(
    engine, dataset: str, system_type: int, cycle_limit: int, df: pd.DataFrame
) -> int:
    """Insert or replace single (day, market) rows of one combo; other rows stay untouched."""
    if df.empty:
        return 0
    keys = {(pd.Timestamp(r.day).date(), r.market) for r in df.itertuples(index=False)}
    combo = and_(
        daily_revenue.c.dataset == dataset,
        daily_revenue.c.system_type == system_type,
        daily_revenue.c.cycle_limit == cycle_limit,
    )
    rows = [
        {
            "dataset": dataset,
            "system_type": system_type,
            "cycle_limit": cycle_limit,
            "day": pd.Timestamp(r.day).date(),
            "market": r.market,
            "daily_revenue_eur": float(r.daily_revenue_eur),
        }
        for r in df.itertuples(index=False)
    ]
    with engine.begin() as conn:
        for day, market in keys:
            conn.execute(
                daily_revenue.delete().where(
                    and_(combo, daily_revenue.c.day == day, daily_revenue.c.market == market)
                )
            )
        conn.execute(daily_revenue.insert(), rows)
    return len(rows)


PUBLISHED_BASE = "Zverejnené výsledky (databáza)"


def has_published(engine) -> bool:
    """True when the normalized schema (bri) exists and holds published results."""
    with engine.connect() as conn:
        if conn.execute(text("SELECT to_regclass('bri.result_publication')")).scalar() is None:
            return False
        return bool(conn.execute(text("SELECT EXISTS (SELECT 1 FROM bri.result_publication)")).scalar())


def load_published(engine, energy_mwh: int, cycle_limit: int) -> pd.DataFrame:
    """Published daily totals (one result version per day) in the dashboard's shape."""
    q = text("""SELECT delivery_day, market, revenue_eur FROM bri.published_daily_total
                WHERE energy_mwh = :e AND cycle_limit = :c ORDER BY delivery_day, market""")
    with engine.connect() as conn:
        rows = conn.execute(q, {"e": energy_mwh, "c": cycle_limit}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["day", "market", "daily_revenue_eur"])
    df["day"] = pd.to_datetime(df["day"])
    df["market"] = df["market"].replace({"AFRR": "aFRR"})
    df["daily_revenue_eur"] = df["daily_revenue_eur"].astype(float)
    return df


def published_status(engine) -> dict:
    """Last publication time and days whose aFRR energy (SEPS Excel) is still missing."""
    with engine.connect() as conn:
        last = conn.execute(text("SELECT max(published_at) FROM bri.result_publication")).scalar()
        waiting = [r[0] for r in conn.execute(text("""
            SELECT e.delivery_day FROM bri.market_day e JOIN bri.market m USING (market_id)
            WHERE m.code = 'AFRR' AND e.component = 'energy' AND e.status <> 'complete'
              AND e.delivery_day <= (SELECT max(delivery_day) FROM bri.result_publication)
            ORDER BY 1""")).fetchall()]
    return {"last_published": last, "afrr_waiting": waiting}


def list_datasets(engine) -> List[str]:
    q = select([daily_revenue.c.dataset]).distinct()
    with engine.connect() as conn:
        return sorted((r[0] for r in conn.execute(q)), reverse=True)


def list_combos(engine, dataset: str) -> List[Tuple[int, int]]:
    q = (
        select([daily_revenue.c.system_type, daily_revenue.c.cycle_limit])
        .where(daily_revenue.c.dataset == dataset)
        .distinct()
    )
    with engine.connect() as conn:
        return sorted((int(r[0]), int(r[1])) for r in conn.execute(q))


def load_daily_revenue(
    engine, dataset: str, system_type: int, cycle_limit: int
) -> pd.DataFrame:
    """Same shape as the file parser: columns day (datetime64), market, daily_revenue_eur."""
    q = (
        select(
            [
                daily_revenue.c.day,
                daily_revenue.c.market,
                daily_revenue.c.daily_revenue_eur,
            ]
        )
        .where(
            and_(
                daily_revenue.c.dataset == dataset,
                daily_revenue.c.system_type == system_type,
                daily_revenue.c.cycle_limit == cycle_limit,
            )
        )
        .order_by(daily_revenue.c.day, daily_revenue.c.market)
    )
    with engine.connect() as conn:
        rows: Iterable = conn.execute(q).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["day", "market", "daily_revenue_eur"])
    df["day"] = pd.to_datetime(df["day"])
    return df
