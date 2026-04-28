$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $workspace ".venv/Scripts/python.exe"

if (-not (Test-Path $python)) {
    throw "Python not found at $python"
}

$damFile = "C:/Users/Даниил Бережной/Downloads/Overwiev_DAM_2025-03-01_2026-03-01.csv"
$idm15File = "C:/Users/Даниил Бережной/Downloads/IDM_overview_2025-03-01_2026-03-01/15 min.csv"

& $python "tools/import_okte_exports.py" `
  --workspace $workspace `
  --dam-overview $damFile `
  --idm-15min $idm15File `
  --date-from "2025-03-01" `
  --date-to "2026-03-01" `
  --fetch-system-imbalance `
  --fetch-demand-supply

Write-Host "Import completed."
