import os

import pytest
from sqlalchemy import text

from tools import results_db as db


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request, tmp_path):
    if request.param == "postgresql":
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set TEST_DATABASE_URL for isolated PostgreSQL integration tests.")
        engine = db.get_engine(url)
        if not engine.url.database.endswith("_test"):
            pytest.fail("TEST_DATABASE_URL must point to a database ending in _test.")
        with engine.begin() as conn:
            for table in ("daily_revenue", "daily_revenue_legacy_v0", "datasets", "markets", "schema_migrations"):
                conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    else:
        engine = db.get_engine(f"sqlite:///{tmp_path / 'results.sqlite'}")
    yield engine
    engine.dispose()


@pytest.fixture
def initialized(engine):
    db.init_schema(engine)
    return engine
