# Battery Revenue Index

## Installation

To get started with the `calculation_config.py` script, you need to install the required Python packages. You can do this using pip and the provided `requirements.txt` file.

1.  Navigate to the directory containing `requirements.txt` in your terminal.
2.  Run the following command:

    ```bash
    pip install -r requirements.txt
    ```

This will install all the necessary dependencies, including pandas, numpy, and other libraries.

## Configuration

Before running the script, you may want to adjust the configuration parameters to suit your specific needs. These parameters are defined within the `calculation_config.py` file.

1.  Open `calculation_config.py` in a text editor or IDE.
2.  Modify the following variables as needed:

    *   `start_day`: The start date for the analysis (e.g., `'2025-03-01'`).
    *   `end_day`: The end date for the analysis (e.g., `'2025-03-01'`).
        **Note:** With the pre-configured data, only the 01.03.2025 can be calculated.
    *   `market_list`: A list of markets to include in the analysis (e.g., `['FCR', 'aFRR', 'DA', 'IDA1', 'ID1', 'IDC']`).
    *   `battery_config`: A dictionary containing battery parameters such as energy capacity (`energy`), power rating (`power`), cycle life (`cycle_limit`), and efficiency (`efficiency`).
    *   `market_config`: A dictionary containing market-specific configurations, such as delivery time (`t_delivery`), power share (`power_share`), and capture rate (`capture_rate`).
    *   `analysismode`: Choose between `'Single-Market'` or `'Cross-Market'` to define the analysis mode.
    *   `parallel`: Set to `True` to enable parallel processing (if supported) or `False` to run sequentially.

    The script is desgned to always calculate the results for the 4 different standard battery configurations 1h/1c; 1h/2c; 2h/1c and 2h/2c.
    If you wish to calculate other battery configurations, simply change the `battery_config` dictionary.

## Execution

Once you have configured the script, you can run it from your terminal.

1.  Navigate to the directory containing `calculation_config.py`.
2.  Run the following command:

    ```bash
    python calculation_config.py
    ```

The script will perform the analysis based on your configuration and save the results to a folder in the `results` directory. For each analysis mode and energy/cycle configuration, there will be a new folder created in the result directory.
Each single- or cross-market result will be stored in a separate .json file containing daily profits, battery-& market-configurations and time-series of SOC and revenue streams

## Slovakia Adaptation (Simplified)

This repository now includes a simplified Slovak benchmark setup based on public data:

- `calculation_config_slovakia.py` - runs `DA`, `IDA1`, `ID1`, `IMB`, `FCR`, `aFRR` in single-market mode.
- `tools/slovakia_data_adapter.py` - converts raw OKTE/SEPS exports into the internal file formats in `marketdata/`.

### Required Slovak input data (per day)

- **Day-ahead (OKTE)**: hourly cleared price
- **Intraday continuous or index (OKTE)**: quarter-hourly prices (for `ID1`)
- **Intraday auction (OKTE)**: quarter-hourly prices (for `IDA1`)
- **Imbalance prices / Co trend proxy (OKTE)**: quarter-hourly prices (for `IMB`)
- **FCR capacity prices (SEPS)**: 6 block prices (4-hour blocks)
- **aFRR capacity prices (SEPS)**: 12 rows (POS/NEG for 6 blocks)

### Public source pages

- OKTE Day-ahead detailed overview: <http://www.okte.sk/en/short-term-market/published-information-of-dam/day-ahead-detailed-overview/>
- OKTE Intraday detailed overview: <http://www.okte.sk/en/short-term-market/published-information-of-idm/intraday-detailed-overview/>
- OKTE Imbalance published information: <http://www.okte.sk/en/imbalance-settlement/published-information/demand-supply-balance/>
- SEPS system services and auctions: <https://www.sepsas.sk/en/services/system-services/>

### Convert your raw files

Example (column names can be overridden):

```bash
python tools/slovakia_data_adapter.py \
  --workspace . \
  --day 2025-03-01 \
  --da-file raw/okte_da_2025-03-01.csv \
  --id1-file raw/okte_idc_2025-03-01.csv \
  --ida1-file raw/okte_ida1_2025-03-01.csv \
  --imb-file raw/okte_imb_2025-03-01.csv \
  --fcr-file raw/seps_fcr_2025-03-01.csv \
  --afrr-cap-file raw/seps_afrr_capacity_2025-03-01.csv
```

If your source uses different header names, pass them explicitly (for example `--da-price-col "ClearingPrice"`).

### Run simplified Slovak index

```bash
python calculation_config_slovakia.py
```

### One-command Slovak import (Windows / PowerShell)

```powershell
./run_slovakia_import.ps1
```

This imports:
- DA + IDM(15m) exports from your local files,
- IMB from OKTE `SystemImbalance` API,
- Demand/Supply Balance from OKTE API.

If you ever need the old IMB proxy fallback from IDM, use:

```bash
./.venv/Scripts/python.exe tools/import_okte_exports.py --workspace . --idm-15min "<path-to-15min.csv>" --use-idm-as-imb-proxy
```

## Licensing

This project is licensed under the GNU General Public License. See the `LICENSE` file for details.

## Documentation

For more detailed information on the project, its features, and usage, please visit the [Documentation Page](https://battery-revenue-index-96345f.pages.rwth-aachen.de/).