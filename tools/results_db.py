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
