"""A missing market result must not appear as a zero or a fabricated moving average."""
import pandas as pd
import pytest

from tools.slovakia_revenue_dashboard import build_figure


@pytest.mark.parametrize("aggregation,expected", [
    ("absolute", (100.0, 300.0)),
    ("annualized", (36.5, 109.5)),
    ("avg30", (100.0, 200.0)),
    ("avg365", (100.0, 200.0)),
])
def test_market_gaps_remain_missing_in_chart_and_averages(aggregation, expected):
    days = pd.date_range("2026-05-25", periods=4, freq="D")
    rows = [{"day": day, "market": "DA", "daily_revenue_eur": 50.0} for day in days]
    rows += [
        {"day": days[0], "market": "aFRR", "daily_revenue_eur": 100.0},
        {"day": days[2], "market": "aFRR", "daily_revenue_eur": 300.0},
    ]
    fig = build_figure(pd.DataFrame(rows), ["DA", "aFRR"], "lines", None, False, aggregation)
    trace = next(trace for trace in fig.data if trace.name == "aFRR")
    values = list(trace.y)
    assert values[0] == pytest.approx(expected[0])
    assert values[2] == pytest.approx(expected[1])
    assert pd.isna(values[1])
    assert pd.isna(values[3])
