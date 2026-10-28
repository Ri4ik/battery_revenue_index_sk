"""Local delivery days (Europe/Bratislava) and their UTC boundaries."""

import datetime as dt

import pandas as pd

from bri.settings import TZ

UTC = "UTC"


def day_bounds(day):
    """[start, end) of a local delivery day as tz-aware UTC timestamps (23, 24 or 25 hours)."""
    start = pd.Timestamp(str(day)).tz_localize(TZ)
    end = (pd.Timestamp(str(day)) + pd.Timedelta(days=1)).tz_localize(TZ)
    return start.tz_convert(UTC), end.tz_convert(UTC)


def local_day(ts):
    """Local delivery day of a UTC timestamp."""
    return pd.Timestamp(ts).tz_convert(TZ).date()


def days(start, end):
    """Inclusive list of datetime.date between two days."""
    return [d.date() for d in pd.date_range(str(start), str(end), freq="D")]


def today_local():
    return pd.Timestamp.now(tz=TZ).date()


def yesterday_local():
    return today_local() - dt.timedelta(days=1)


def local_hour_to_utc(day, hour):
    """UTC instant of local wall-clock hour (0..24) on a delivery day; 24 = next midnight."""
    base = pd.Timestamp(str(day))
    return (base + pd.Timedelta(hours=int(hour))).tz_localize(TZ, nonexistent="shift_forward",
                                                              ambiguous=True).tz_convert(UTC)


def quarter_hour_start(day, index_1_based, minutes=15):
    """Start of the n-th interval of a local day, counted from local midnight in real time.

    Works across DST changes because it adds elapsed time to the UTC instant of midnight.
    """
    start, _ = day_bounds(day)
    return start + pd.Timedelta(minutes=minutes * (int(index_1_based) - 1))
