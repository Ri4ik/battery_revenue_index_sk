"""
Column identifiers for reserve-market CSV files.

Upstream ISEA exports used Regelleistung-style headers with a GERMANY_ prefix.
Slovak pipelines store the same numeric meaning under neutral names; legacy
headers remain supported when reading older files or DB snapshots.
"""

# FCR capacity settlement (€/MW per 4h block)
FCR_SETTLEMENT = "FCR_SETTLEMENTCAPACITY_PRICE_EUR_PER_MW"
FCR_SETTLEMENT_LEGACY = "GERMANY_SETTLEMENTCAPACITY_PRICE_[EUR/MW]"

# aFRR capacity (€/MW/h)
AFRR_CAPACITY = "AFRR_AVERAGE_CAPACITY_PRICE_EUR_PER_MW_PER_H"
AFRR_CAPACITY_LEGACY = "GERMANY_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]"

# aFRR energy activations (MW) — only used when energy market data is present
AFRR_SETPOINT = "AFRR_SETPOINT_MW"
AFRR_SETPOINT_LEGACY = "GERMANY_aFRR_SETPOINT_[MW]"


def series_from_columns(df, *candidates):
    for name in candidates:
        if name in df.columns:
            return df[name]
    raise KeyError(
        "None of the expected columns found: "
        + ", ".join(candidates)
        + f". Available: {list(df.columns)}"
    )
