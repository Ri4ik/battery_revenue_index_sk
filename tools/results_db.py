"""Normalized dashboard storage. Modified 2026-10-20: foreign keys and upserts.

Revenue remains floating point scientific results in EUR/day at 1 MW.
"""
import math
import os

import pandas as pd
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, Float, ForeignKey, Integer, MetaData,
    PrimaryKeyConstraint, String, Table, URL, create_engine, event, func, select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

DEFAULT_DATASET = "Slovakia_2025-2026 (01.03)"
FRAME_COLUMNS = ["day", "market", "daily_revenue_eur"]
metadata = MetaData()
datasets = Table(
    "datasets", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("is_published", Boolean, nullable=False, server_default="false"),
)
markets = Table(
    "markets", metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(64), nullable=False, unique=True),
)
daily_revenue = Table(
    "daily_revenue", metadata,
    Column("dataset_id", Integer, ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False),
    Column("system_type", Integer, nullable=False),
    Column("cycle_limit", Integer, nullable=False),
    Column("day", Date, nullable=False),
    Column("market_id", Integer, ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False),
    Column("daily_revenue_eur", Float, nullable=False),
    PrimaryKeyConstraint("dataset_id", "system_type", "cycle_limit", "day", "market_id",
                         name="pk_daily_revenue_v1"),
    CheckConstraint("system_type > 0", name="positive_system_type"),
    CheckConstraint("cycle_limit > 0", name="positive_cycle_limit"),
)


def database_url():
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if os.environ.get("PGHOST"):
        return URL.create(
            "postgresql+psycopg2", username=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"], host=os.environ["PGHOST"],
            port=int(os.environ.get("PGPORT", "5432")), database=os.environ["PGDATABASE"],
        )
    return None


def get_engine(url=None):
    url = url or database_url()
    if not url:
        raise RuntimeError("Set DATABASE_URL or PGHOST/PGUSER/PGPASSWORD/PGDATABASE.")
    options = {"pool_pre_ping": True}
    if str(url).startswith("postgresql"):
        options["connect_args"] = {"connect_timeout": 5, "options": "-c statement_timeout=30000"}
    engine = create_engine(url, **options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


def init_schema(engine):
    from tools.migrate_results_db import migrate
    migrate(engine)


def _insert(conn, table):
    if conn.dialect.name == "postgresql":
        return pg_insert(table)
    if conn.dialect.name == "sqlite":
        return sqlite_insert(table)
    raise RuntimeError("Supported databases: PostgreSQL (production), SQLite (unit tests).")


def _catalog_id(conn, table, column, value):
    values = {column: value}
    if table is datasets:
        values["is_published"] = value == DEFAULT_DATASET
    conn.execute(_insert(conn, table).values(**values).on_conflict_do_nothing(index_elements=[column]))
    return conn.execute(select(table.c.id).where(table.c[column] == value)).scalar_one()


def validate_frame(df):
    if not set(FRAME_COLUMNS).issubset(df.columns):
        raise ValueError("Expected columns: day, market, daily_revenue_eur.")
    records, seen = [], set()
    for row in df[FRAME_COLUMNS].itertuples(index=False):
        day = pd.Timestamp(row.day)
        if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
            raise ValueError("day must be a delivery date without time or time zone.")
        if not isinstance(row.market, str) or not row.market.strip() or len(row.market) > 64:
            raise ValueError("Invalid market code.")
        value = row.daily_revenue_eur
        if isinstance(value, (bool, str)) or not math.isfinite(float(value)):
            raise ValueError("Revenue must be a finite number.")
        key = (day.date(), row.market)
        if key in seen:
            raise ValueError("Duplicate day/market in input.")
        seen.add(key)
        records.append({"day": key[0], "market": row.market, "daily_revenue_eur": float(value)})
    return records


def write_combo(engine, dataset, system_type, cycle_limit, df, *, replace=False):
    if not isinstance(dataset, str) or not dataset.strip() or len(dataset) > 255:
        raise ValueError("Invalid dataset name.")
    if any(type(x) is not int or x <= 0 for x in (system_type, cycle_limit)):
        raise ValueError("Battery duration and cycle limit must be positive integers.")
    rows = validate_frame(df)
    if replace and not rows:
        raise ValueError("Refusing to replace a combo with empty input.")
    with engine.begin() as conn:
        dataset_id = _catalog_id(conn, datasets, "name", dataset)
        if conn.dialect.name == "postgresql":
            conn.execute(select(datasets.c.id).where(datasets.c.id == dataset_id).with_for_update())
        market_ids = {code: _catalog_id(conn, markets, "code", code)
                      for code in sorted({r["market"] for r in rows})}
        if replace:
            conn.execute(daily_revenue.delete().where(
                daily_revenue.c.dataset_id == dataset_id,
                daily_revenue.c.system_type == system_type,
                daily_revenue.c.cycle_limit == cycle_limit,
            ))
        for row in rows:
            row["market_id"] = market_ids[row.pop("market")]
            row.update(dataset_id=dataset_id, system_type=system_type, cycle_limit=cycle_limit)
        for offset in range(0, len(rows), 1000):
            statement = _insert(conn, daily_revenue).values(rows[offset:offset + 1000])
            conn.execute(statement.on_conflict_do_update(
                index_elements=["dataset_id", "system_type", "cycle_limit", "day", "market_id"],
                set_={"daily_revenue_eur": statement.excluded.daily_revenue_eur},
            ))
    return len(rows)


def replace_combo(engine, dataset, system_type, cycle_limit, df):
    return write_combo(engine, dataset, system_type, cycle_limit, df, replace=True)


def combo_exists(engine, dataset, system_type, cycle_limit):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(daily_revenue.join(datasets)).where(
            datasets.c.name == dataset, daily_revenue.c.system_type == system_type,
            daily_revenue.c.cycle_limit == cycle_limit,
        )).scalar_one() > 0


def list_datasets(engine, published_only=True):
    q = select(datasets.c.name).where(select(daily_revenue.c.dataset_id).where(
        daily_revenue.c.dataset_id == datasets.c.id).exists())
    if published_only:
        q = q.where(datasets.c.is_published.is_(True))
    with engine.connect() as conn:
        return list(conn.execute(q.order_by(datasets.c.name.desc())).scalars())


def list_combos(engine, dataset):
    q = select(daily_revenue.c.system_type, daily_revenue.c.cycle_limit).select_from(
        daily_revenue.join(datasets)).where(datasets.c.name == dataset).distinct()
    with engine.connect() as conn:
        return sorted(tuple(row) for row in conn.execute(q))


def load_daily_revenue(engine, dataset, system_type, cycle_limit):
    q = select(daily_revenue.c.day, markets.c.code.label("market"),
               daily_revenue.c.daily_revenue_eur).select_from(
        daily_revenue.join(datasets).join(markets)).where(
        datasets.c.name == dataset, daily_revenue.c.system_type == system_type,
        daily_revenue.c.cycle_limit == cycle_limit,
    ).order_by(daily_revenue.c.day, markets.c.code)
    with engine.connect() as conn:
        df = pd.DataFrame(conn.execute(q).fetchall(), columns=FRAME_COLUMNS)
    df["day"] = pd.to_datetime(df["day"])
    return df
