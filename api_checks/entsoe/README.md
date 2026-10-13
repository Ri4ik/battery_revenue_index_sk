# ENTSO-E API check

Small local helper for checking access to the ENTSO-E Transparency Platform API:

- account/API access page: https://transparency.entsoe.eu/myAccount/webApiAccess
- API endpoint: https://web-api.tp.entsoe.eu/api

Keep the API token in a local `.env` file or in the `ENTSOE_API_KEY` environment variable. Do not commit real tokens.

## Local setup

Create `api_checks/entsoe/.env`:

```text
ENTSOE_API_KEY=your-real-token
```

Run a quick day-ahead price request for Slovakia:

```powershell
.\.venv\Scripts\python.exe api_checks\entsoe\check_entsoe_api.py
```

The default request uses:

- `documentType=A44` day-ahead prices
- Slovakia bidding zone EIC `10YSK-SEPS-----K`
- yesterday/today UTC date range

You can override parameters:

```powershell
.\.venv\Scripts\python.exe api_checks\entsoe\check_entsoe_api.py `
  --domain 10YSK-SEPS-----K `
  --document-type A44 `
  --period-start 202605110000 `
  --period-end 202605120000 `
  --output api_checks\entsoe\sample_response.xml
```

