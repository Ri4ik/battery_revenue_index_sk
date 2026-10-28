"""Revenue models on native input intervals.

Wholesale markets (DA, IDM15, IDM60, IMB) use the project's spread-trading model
(WholesaleMarket.calculate_market_trades_fast) with the interval length of the day as
t_delivery, so energy = power x interval duration (1 h before / 15 min after the DAM switch).
aFRR follows markets/aFRR_market.py: capacity per 4-hour block and direction, energy from
SEPS/Damas activations; block and interval lengths are taken in real time (DST-safe).
"""

import copy

import numpy as np
import pandas as pd

from bri.readiness import IDM_MAX_GAP_SHARE, IDM_STEP
from bri.timeutil import day_bounds, local_hour_to_utc
from markets.wholesale_market import WholesaleMarket

SEPS_MAX_ABS_PRICE = 10000.0  # same rule as tools/import_seps_damas_energy.py


class NotCalculable(Exception):
    """Inputs exist but cannot be used (e.g. too many intraday gaps)."""


def _hours(td):
    return td / pd.Timedelta(hours=1)


def _fill_idm_gaps(df, market, day):
    start, end = day_bounds(day)
    grid = pd.date_range(start, end, freq=IDM_STEP[market], closed="left")
    full = pd.DataFrame({"interval_start": grid})
    full["interval_end"] = full["interval_start"] + IDM_STEP[market]
    merged = full.merge(df[["interval_start", "price"]], on="interval_start", how="left")
    missing = int(merged["price"].isna().sum())
    if missing > IDM_MAX_GAP_SHARE * len(merged):
        raise NotCalculable("{} z {} produktov bez obchodu".format(missing, len(merged)))
    merged["price"] = merged["price"].interpolate(method="linear").ffill().bfill()
    return merged


def wholesale(market, prices, day, battery_config, market_config):
    """prices: interval_start, interval_end, price -> per-interval revenue and SOC."""
    df = prices.sort_values("interval_start").reset_index(drop=True)
    if market in IDM_STEP:
        df = _fill_idm_gaps(df, market, day)
    durations = (df["interval_end"] - df["interval_start"]).unique()
    if len(durations) != 1:
        raise NotCalculable("rôzne dĺžky intervalov v jednom dni")
    t_delivery = _hours(pd.Timedelta(durations[0]))

    bc = copy.deepcopy(battery_config)
    mc = copy.deepcopy(market_config)
    mc.pop("marketable_power", None)
    mc["t_delivery"] = t_delivery
    model = WholesaleMarket.__new__(WholesaleMarket)
    model.prices = {}
    if market == "DA":
        model.set_marketable_power_da(bc, mc)
    else:  # the project uses the ID1 rule for intraday and imbalance
        model.set_marketable_power_id1(bc, mc)
    series = pd.Series(df["price"].values, index=pd.DatetimeIndex(df["interval_start"]))
    _, _, result = model.calculate_market_trades_fast(series.to_frame("price"), bc, mc)

    out = pd.DataFrame({
        "interval_start": df["interval_start"].values,
        "interval_end": df["interval_end"].values,
        "revenue_eur": result["revenue"].astype(float).values * mc["capture_rate"],
        "soc": result["soc"].astype(float).values,
    })
    return {"total": out}


def _clean_seps_prices(energy):
    df = energy.copy()
    for col in ("up_price_eur_mwh", "down_price_eur_mwh"):
        s = df[col].astype(float)
        s = s.mask(s.abs() > SEPS_MAX_ABS_PRICE)
        df[col] = s.interpolate(method="linear").ffill().bfill().fillna(0.0)
    return df


def afrr(capacity, energy, day, battery_config, market_config):
    """capacity: direction, block_start_h, block_end_h, price; energy: SEPS intervals."""
    cap_cfg = market_config["aFRR Capacity"]
    en_cfg = market_config["aFRR Energy"]
    e_mwh = float(battery_config["energy"])
    p_mw = float(battery_config["power"])

    # marketable power for symmetric bids (aFRRmarket.set_marketable_power_afrr_capacity)
    afrr_power = min(e_mwh / 2, p_mw * cap_cfg["power_share"])

    blocks = []
    for b_start in sorted(capacity["block_start_h"].unique()):
        rows = capacity[capacity["block_start_h"] == b_start]
        b_end = int(rows["block_end_h"].iloc[0])
        prices = rows.set_index("direction")["price"]
        start_utc, end_utc = local_hour_to_utc(day, b_start), local_hour_to_utc(day, b_end)
        hours = _hours(end_utc - start_utc)
        revenue = (prices.get("POS", np.nan) + prices.get("NEG", np.nan)) * afrr_power * hours
        active = bool(revenue >= 0)  # NaN -> not active (select_afrr_blocks rule)
        blocks.append((start_utc, end_utc, hours, revenue if active else 0.0, active))

    en = _clean_seps_prices(energy.sort_values("interval_start").reset_index(drop=True))
    n = len(en)
    cap_rev = np.zeros(n)
    power_limit = np.zeros(n)
    energy_power = p_mw * en_cfg["power_share"]
    for i, start in enumerate(en["interval_start"]):
        for b_start, b_end, hours, b_rev, active in blocks:
            if b_start <= start < b_end:
                dur = _hours(en["interval_end"].iloc[i] - start)
                cap_rev[i] = b_rev * dur / hours * cap_cfg["capture_rate"]
                power_limit[i] = energy_power if active else 0.0
                break

    # energy (aFRRmarket.calculate_seps_damas_energy_revenue on real interval lengths)
    soc = float(battery_config["startSOC"])
    min_soc, max_soc = float(battery_config["minSOC"]), float(battery_config["maxSOC"])
    cycle_limit = float(battery_config["cycle_limit"]) * float(en_cfg.get("cycle_share", 1.0))
    max_throughput = max(0.0, 2.0 * e_mwh * cycle_limit)
    throughput = 0.0
    en_rev = np.zeros(n)
    socs = np.zeros(n)
    for i, row in enumerate(en.itertuples(index=False)):
        max_energy = power_limit[i] * _hours(row.interval_end - row.interval_start)
        remaining = max_throughput - throughput
        if remaining > 0 and max_energy > 0:
            up_v = max(row.up_volume_mwh, 0.0)
            down_v = max(row.down_volume_mwh, 0.0)
            up_p = row.up_price_eur_mwh if up_v else 0.0
            down_p = row.down_price_eur_mwh if down_v else 0.0
            net = up_v - down_v
            if net > 0:
                delivered = min(net, max_energy, remaining, max(0.0, (soc - min_soc) * e_mwh))
                en_rev[i] = delivered * up_p
                soc -= delivered / e_mwh
                throughput += delivered
            elif net < 0:
                delivered = min(-net, max_energy, remaining, max(0.0, (max_soc - soc) * e_mwh))
                en_rev[i] = delivered * down_p
                soc += delivered / e_mwh
                throughput += delivered
        socs[i] = soc
    en_rev = en_rev * en_cfg["capture_rate"]

    frame = lambda values, with_soc: pd.DataFrame({  # noqa: E731
        "interval_start": en["interval_start"].values,
        "interval_end": en["interval_end"].values,
        "revenue_eur": values,
        "soc": socs if with_soc else np.nan,
    })
    return {
        "capacity": frame(cap_rev, False),
        "energy": frame(en_rev, False),
        "total": frame(cap_rev + en_rev, True),
    }
