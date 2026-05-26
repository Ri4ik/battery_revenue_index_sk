import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import urlopen

import pandas as pd

SYSTEM_IMBALANCE_API_URL = "https://iszo.okte.sk/api/v1/SystemImbalance"
DEMAND_SUPPLY_BALANCE_API_URL = "https://iszo.okte.sk/api/v1/DemandSupplyBalance"
IDM_RESULTS_API_URL = "https://isot.okte.sk/api/v1/idm/results"


def _to_num(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(" ", "", regex=False)
    # Handles values like "1,662.5" (thousands comma) and "62.32".
    s = s.str.replace(",", "", regex=False)
    return pd.to_numeric(s, errors="coerce")


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _fetch_okte_json(url: str, params: dict, timeout: int = 60) -> list:
    query = urlencode(params)
    with urlopen(f"{url}?{query}", timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read().decode(charset)
    parsed = json.loads(payload)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    raise ValueError(f"Unexpected API payload type for {url}: {type(parsed)}")


def _period_start_timestamp(date_value: str, period_value: int) -> pd.Timestamp:
    # OKTE period numbering is 1..96 for quarter-hourly data.
    return pd.to_datetime(date_value) + pd.to_timedelta((int(period_value) - 1) * 15, unit="m")


def _extract_system_imbalance_rows(raw: list, price_field: str) -> pd.DataFrame:
    rows = []
    for item in raw:
        periods = item.get("periods")
        if not isinstance(periods, list):
            continue
        date_value = item.get("date")
        if date_value is None:
            continue
        for period_row in periods:
            period_value = period_row.get("period")
            if period_value is None:
                continue
            timestamp = _period_start_timestamp(date_value, period_value)
            price = period_row.get(price_field)
            rows.append(
                {
                    "date": date_value,
                    "period": period_value,
                    "timestamp": timestamp,
                    "price": price,
                }
            )

    if not rows:
        return pd.DataFrame(columns=["timestamp", "price"])

    out = pd.DataFrame(rows)
    out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce")
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    out["period"] = pd.to_numeric(out["period"], errors="coerce")
    out = out.dropna(subset=["timestamp", "price", "period"]).sort_values(["date", "period"])
    return out


def import_system_imbalance_api(
    workspace: str,
    date_from: str,
    date_to: str,
    evaluation_type: str = "final",
    price_field: str = "isp",
):
    params = {
        "dateFrom": date_from,
        "dateTo": date_to,
        "evaluationType": evaluation_type,
    }
    try:
        raw = _fetch_okte_json(SYSTEM_IMBALANCE_API_URL, params)
    except HTTPError as exc:
        if exc.code != 400:
            raise
        # Some OKTE API calls reject large date ranges with 400.
        # Fallback: request day-by-day and concatenate.
        raw = []
        for day in pd.date_range(start=date_from, end=date_to, freq="D").strftime("%Y-%m-%d"):
            raw.extend(
                _fetch_okte_json(
                    SYSTEM_IMBALANCE_API_URL,
                    {
                        "dateFrom": day,
                        "dateTo": day,
                        "evaluationType": evaluation_type,
                    },
                )
            )
    prices = _extract_system_imbalance_rows(raw, price_field=price_field)
    if prices.empty:
        raise ValueError(
            "SystemImbalance API returned no quarter-hour rows. "
            "Check date range / evaluation type / response schema."
        )

    out_dir = os.path.join(workspace, "marketdata", "IMB")
    _ensure_dir(out_dir)
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").dt.date

    for day, group in prices.groupby("date"):
        out = group[["period", "price"]].copy()
        out = out.sort_values("period")
        out = out.groupby("period", as_index=False)["price"].mean()
        out = out[(out["period"] >= 1) & (out["period"] <= 96)]
        out["timestamp"] = pd.to_datetime(str(day)) + pd.to_timedelta((out["period"] - 1) * 15, unit="m")
        expected_idx = pd.date_range(start=f"{day} 00:00:00", periods=96, freq="15min")
        out = (
            out[["timestamp", "price"]]
            .set_index("timestamp")
            .reindex(expected_idx)
            .rename_axis("timestamp")
            .reset_index()
        )
        out["price"] = out["price"].interpolate(method="linear").ffill().bfill()
        out.to_csv(os.path.join(out_dir, f"IMB_{day}.csv"), index=False)

    print(
        f"System imbalance imported from API ({date_from}..{date_to}) "
        f"with field '{price_field}'."
    )


def import_demand_supply_balance_api(workspace: str, date_from: str, date_to: str):
    params = {
        "dateFrom": date_from,
        "dateTo": date_to,
    }
    try:
        raw = _fetch_okte_json(DEMAND_SUPPLY_BALANCE_API_URL, params)
    except HTTPError as exc:
        if exc.code != 400:
            raise
        raw = []
        for day in pd.date_range(start=date_from, end=date_to, freq="D").strftime("%Y-%m-%d"):
            raw.extend(
                _fetch_okte_json(
                    DEMAND_SUPPLY_BALANCE_API_URL,
                    {
                        "dateFrom": day,
                        "dateTo": day,
                    },
                )
            )
    df = pd.DataFrame(raw)
    if df.empty:
        raise ValueError("DemandSupplyBalance API returned no rows.")

    for col in ["period", "supply", "demand", "balance"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    out_dir = Path(workspace) / "marketdata" / "OKTE"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"demand_supply_balance_{date_from}_{date_to}.csv"
    df.to_csv(out_file, index=False)
    print(f"Demand/Supply balance imported from API: {out_file}")


def _extract_idm_results_rows(raw: list, price_field: str) -> pd.DataFrame:
    rows = []
    for item in raw:
        delivery_day = item.get("deliveryDay")
        period = item.get("period")
        if delivery_day is None or period is None:
            continue
        price = item.get(price_field)
        if price is None and price_field != "priceWeightedAverage":
            price = item.get("priceWeightedAverage")
        if price is None:
            price = item.get("priceAverage")
        rows.append(
            {
                "deliveryDay": delivery_day,
                "period": period,
                "deliveryStart": item.get("deliveryStart"),
                "deliveryEnd": item.get("deliveryEnd"),
                "price": price,
                "priceWeightedAverage": item.get("priceWeightedAverage"),
                "priceAverage": item.get("priceAverage"),
                "lastPrice": item.get("lastPrice"),
                "minimalPrice": item.get("minimalPrice"),
                "maximalPrice": item.get("maximalPrice"),
                "successfulVolume": item.get("successfulVolume"),
                "purchaseSuccessfulVolume": item.get("purchaseSuccessfulVolume"),
                "saleSuccessfulVolume": item.get("saleSuccessfulVolume"),
            }
        )

    if not rows:
        return pd.DataFrame(columns=["deliveryDay", "period", "timestamp", "price"])

    out = pd.DataFrame(rows)
    out["deliveryDay"] = pd.to_datetime(out["deliveryDay"], errors="coerce").dt.date
    out["period"] = pd.to_numeric(out["period"], errors="coerce")
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    for col in [
        "priceWeightedAverage",
        "priceAverage",
        "lastPrice",
        "minimalPrice",
        "maximalPrice",
        "successfulVolume",
        "purchaseSuccessfulVolume",
        "saleSuccessfulVolume",
    ]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["deliveryDay", "period"]).sort_values(["deliveryDay", "period"])
    return out


def import_idm_results_api(
    workspace: str,
    date_from: str,
    date_to: str,
    product_type: int,
    market_name: str = None,
    price_field: str = "priceWeightedAverage",
):
    if product_type not in (15, 60):
        raise ValueError("product_type must be 15 or 60")
    if market_name is None:
        market_name = "IDM15" if product_type == 15 else "IDM60"

    params = {
        "deliveryDayFrom": date_from,
        "deliveryDayTo": date_to,
        "productType": product_type,
    }
    day_span = len(pd.date_range(start=date_from, end=date_to, freq="D"))
    if day_span > 31:
        raw = []
        days = pd.date_range(start=date_from, end=date_to, freq="D").strftime("%Y-%m-%d")
        for idx, day in enumerate(days, 1):
            raw.extend(
                _fetch_okte_json(
                    IDM_RESULTS_API_URL,
                    {
                        "deliveryDayFrom": day,
                        "deliveryDayTo": day,
                        "productType": product_type,
                    },
                )
            )
            if idx % 50 == 0 or idx == len(days):
                print(f"OKTE IDM productType={product_type}: fetched {idx}/{len(days)} days")
    else:
        try:
            raw = _fetch_okte_json(IDM_RESULTS_API_URL, params)
        except HTTPError as exc:
            if exc.code != 400:
                raise
            raw = []
            for day in pd.date_range(start=date_from, end=date_to, freq="D").strftime("%Y-%m-%d"):
                raw.extend(
                    _fetch_okte_json(
                        IDM_RESULTS_API_URL,
                        {
                            "deliveryDayFrom": day,
                            "deliveryDayTo": day,
                            "productType": product_type,
                        },
                    )
                )
    try:
        len(raw)
    except NameError:
        raw = _fetch_okte_json(IDM_RESULTS_API_URL, params)

    rows = _extract_idm_results_rows(raw, price_field=price_field)
    if rows.empty:
        raise ValueError(f"OKTE IDM results API returned no rows for productType={product_type}.")

    raw_dir = Path(workspace) / "marketdata" / "OKTE"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_dir / f"idm_results_product{product_type}_{date_from}_{date_to}.csv"
    rows.to_csv(raw_file, index=False)

    out_dir = Path(workspace) / "marketdata" / market_name
    out_dir.mkdir(parents=True, exist_ok=True)
    points = 96 if product_type == 15 else 24
    freq = "15min" if product_type == 15 else "1H"

    written = 0
    for day, group in rows.groupby("deliveryDay"):
        group = group.copy()
        group["period"] = group["period"].astype(int)
        daily = group.groupby("period", as_index=False)["price"].mean()
        daily = daily[(daily["period"] >= 1) & (daily["period"] <= points)]
        daily["timestamp"] = pd.to_datetime(str(day)) + pd.to_timedelta(
            (daily["period"] - 1) * (15 if product_type == 15 else 60), unit="m"
        )
        expected_idx = pd.date_range(start=f"{day} 00:00:00", periods=points, freq=freq)
        out = (
            daily[["timestamp", "price"]]
            .set_index("timestamp")
            .reindex(expected_idx)
            .rename_axis("timestamp")
            .reset_index()
        )
        out["price"] = out["price"].interpolate(method="linear").ffill().bfill()
        if product_type == 60:
            out = (
                out.set_index("timestamp")
                .resample("15min")
                .ffill()
                .reindex(pd.date_range(start=f"{day} 00:00:00", periods=96, freq="15min"))
                .rename_axis("timestamp")
                .reset_index()
            )
            out["price"] = out["price"].ffill().bfill()
        out.to_csv(out_dir / f"{market_name}_{day}.csv", index=False)
        written += 1

    print(
        f"OKTE IDM productType={product_type} imported as {market_name}: "
        f"{written} day(s), raw={raw_file}"
    )


def import_dam_overview(path: str, workspace: str):
    df = pd.read_csv(path, sep=";", encoding="utf-8-sig")
    df["Delivery day"] = pd.to_datetime(df["Delivery day"], format="%d.%m.%Y")
    df["Period number"] = pd.to_numeric(df["Period number"], errors="coerce")
    df["Price SK (EUR/MWh)"] = _to_num(df["Price SK (EUR/MWh)"])
    df = df.dropna(subset=["Delivery day", "Period number", "Price SK (EUR/MWh)"])

    out_dir = os.path.join(workspace, "marketdata", "DA")
    _ensure_dir(out_dir)

    for day, g in df.groupby(df["Delivery day"].dt.date):
        g = g.sort_values("Period number").copy()
        if g["Period number"].max() <= 24:
            g["hour"] = (g["Period number"] - 1).astype(int)
            hourly = g.groupby("hour", as_index=False)["Price SK (EUR/MWh)"].mean()
        else:
            g["hour"] = ((g["Period number"] - 1) // 4).astype(int)
            hourly = g.groupby("hour", as_index=False)["Price SK (EUR/MWh)"].mean()
        hourly["timestamp"] = pd.to_datetime(str(day)) + pd.to_timedelta(hourly["hour"], unit="h")
        out = hourly[["timestamp", "Price SK (EUR/MWh)"]].rename(columns={"Price SK (EUR/MWh)": "price"})
        expected_idx = pd.date_range(start=f"{day} 00:00:00", periods=24, freq="1H")
        out = (
            out.set_index("timestamp")
            .reindex(expected_idx)
            .rename_axis("timestamp")
            .reset_index()
        )
        out["price"] = out["price"].interpolate(method="linear").ffill().bfill()
        out.to_csv(os.path.join(out_dir, f"DA_{day}.csv"), index=False)


def import_idm_15min(path: str, workspace: str, column_name: str = "Weighted average price of all trades (EUR/MWh)"):
    df = pd.read_csv(path, sep=";", encoding="utf-8-sig")
    df["Delivery day"] = pd.to_datetime(df["Delivery day"], format="%d.%m.%Y")
    df["Period number"] = pd.to_numeric(df["Period number"], errors="coerce")
    df[column_name] = _to_num(df[column_name])
    df = df.dropna(subset=["Delivery day", "Period number", column_name])

    out_idm15 = os.path.join(workspace, "marketdata", "IDM15")
    out_id1 = os.path.join(workspace, "marketdata", "ID1")
    _ensure_dir(out_idm15)
    _ensure_dir(out_id1)

    for day, g in df.groupby(df["Delivery day"].dt.date):
        g = g.sort_values("Period number").copy()
        g["timestamp"] = pd.to_datetime(str(day)) + pd.to_timedelta((g["Period number"] - 1) * 15, unit="m")
        out = g[["timestamp", column_name]].rename(columns={column_name: "price"})
        expected_idx = pd.date_range(start=f"{day} 00:00:00", periods=96, freq="15min")
        out = (
            out.set_index("timestamp")
            .reindex(expected_idx)
            .rename_axis("timestamp")
            .reset_index()
        )
        out["price"] = out["price"].interpolate(method="linear").ffill().bfill()

        # Current IDM15 input.
        out.to_csv(os.path.join(out_idm15, f"IDM15_{day}.csv"), index=False)
        # Legacy ID1 input for older result folders/scripts.
        out.to_csv(os.path.join(out_id1, f"ID1_{day}.csv"), index=False)


def import_idm_as_imb_proxy(path: str, workspace: str, column_name: str = "Weighted average price of all trades (EUR/MWh)"):
    df = pd.read_csv(path, sep=";", encoding="utf-8-sig")
    df["Delivery day"] = pd.to_datetime(df["Delivery day"], format="%d.%m.%Y")
    df["Period number"] = pd.to_numeric(df["Period number"], errors="coerce")
    df[column_name] = _to_num(df[column_name])
    df = df.dropna(subset=["Delivery day", "Period number", column_name])

    out_imb = os.path.join(workspace, "marketdata", "IMB")
    _ensure_dir(out_imb)

    for day, g in df.groupby(df["Delivery day"].dt.date):
        g = g.sort_values("Period number").copy()
        g["timestamp"] = pd.to_datetime(str(day)) + pd.to_timedelta((g["Period number"] - 1) * 15, unit="m")
        out = g[["timestamp", column_name]].rename(columns={column_name: "price"})
        expected_idx = pd.date_range(start=f"{day} 00:00:00", periods=96, freq="15min")
        out = (
            out.set_index("timestamp")
            .reindex(expected_idx)
            .rename_axis("timestamp")
            .reset_index()
        )
        out["price"] = out["price"].interpolate(method="linear").ffill().bfill()
        out.to_csv(os.path.join(out_imb, f"IMB_{day}.csv"), index=False)

    print("IMB proxy imported from IDM file.")


def repair_imb_from_id1(workspace: str, date_from: str, date_to: str):
    imb_dir = Path(workspace) / "marketdata" / "IMB"
    id1_dir = Path(workspace) / "marketdata" / "ID1"
    repaired = 0

    for day in pd.date_range(start=date_from, end=date_to, freq="D").strftime("%Y-%m-%d"):
        imb_file = imb_dir / "IMB_{}.csv".format(day)
        valid_imb = False
        if imb_file.exists():
            try:
                imb_df = pd.read_csv(imb_file)
                valid_imb = ("timestamp" in imb_df.columns) and (imb_df["timestamp"].dropna().shape[0] == 96)
            except Exception:
                valid_imb = False
        if valid_imb:
            continue

        id1_file = id1_dir / "ID1_{}.csv".format(day)
        if not id1_file.exists():
            continue
        id1_df = pd.read_csv(id1_file)
        if "timestamp" not in id1_df.columns or "price" not in id1_df.columns:
            continue
        id1_df = id1_df[["timestamp", "price"]].copy()
        if id1_df["timestamp"].dropna().shape[0] != 96:
            continue
        imb_dir.mkdir(parents=True, exist_ok=True)
        id1_df.to_csv(imb_file, index=False)
        repaired += 1

    if repaired:
        print(f"Repaired IMB with ID1 proxy for {repaired} day(s).")


def main():
    parser = argparse.ArgumentParser(description="Import OKTE exports to project marketdata format.")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--dam-overview", help="Path to Overwiev_DAM_*.csv")
    parser.add_argument("--idm-15min", help="Path to IDM 15 min.csv")
    parser.add_argument("--date-from", default="2025-03-01", help="Date from for API import")
    parser.add_argument("--date-to", default="2026-03-01", help="Date to for API import")
    parser.add_argument(
        "--fetch-system-imbalance",
        action="store_true",
        help="Import IMB prices from OKTE SystemImbalance API.",
    )
    parser.add_argument(
        "--system-imbalance-evaluation-type",
        default="final",
        help="OKTE evaluationType for SystemImbalance API.",
    )
    parser.add_argument(
        "--system-imbalance-price-field",
        default="isp",
        help="Price field from SystemImbalance periods (e.g. isp, sipr).",
    )
    parser.add_argument(
        "--fetch-demand-supply",
        action="store_true",
        help="Import Demand/Supply Balance API data and store raw csv.",
    )
    parser.add_argument(
        "--fetch-idm-results",
        action="store_true",
        help="Import OKTE IDM results API for productType 15 and/or 60.",
    )
    parser.add_argument(
        "--idm-product-types",
        default="15,60",
        help="Comma-separated OKTE IDM product types to import (15,60).",
    )
    parser.add_argument(
        "--idm-price-field",
        default="priceWeightedAverage",
        help="Price field from OKTE IDM results (default: priceWeightedAverage).",
    )
    parser.add_argument(
        "--use-idm-as-imb-proxy",
        action="store_true",
        help="Fallback only: write IMB from IDM weighted prices.",
    )
    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace)
    if args.dam_overview:
        import_dam_overview(args.dam_overview, workspace)
    if args.idm_15min:
        import_idm_15min(args.idm_15min, workspace)
        if args.use_idm_as_imb_proxy:
            import_idm_as_imb_proxy(args.idm_15min, workspace)
    if args.fetch_system_imbalance:
        import_system_imbalance_api(
            workspace=workspace,
            date_from=args.date_from,
            date_to=args.date_to,
            evaluation_type=args.system_imbalance_evaluation_type,
            price_field=args.system_imbalance_price_field,
        )
        repair_imb_from_id1(
            workspace=workspace,
            date_from=args.date_from,
            date_to=args.date_to,
        )
    if args.fetch_demand_supply:
        import_demand_supply_balance_api(
            workspace=workspace,
            date_from=args.date_from,
            date_to=args.date_to,
        )
    if args.fetch_idm_results:
        product_types = [
            int(item.strip())
            for item in args.idm_product_types.split(",")
            if item.strip()
        ]
        for product_type in product_types:
            import_idm_results_api(
                workspace=workspace,
                date_from=args.date_from,
                date_to=args.date_to,
                product_type=product_type,
                market_name="IDM15" if product_type == 15 else "IDM60",
                price_field=args.idm_price_field,
            )
    print("OKTE import finished.")


if __name__ == "__main__":
    main()
