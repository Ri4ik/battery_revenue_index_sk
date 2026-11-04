# Legacy file-based import of OKTE CSV exports into marketdata/ (the database pipeline
# downloads the same data from the OKTE API; see README.md).
#   .\run_slovakia_import.ps1 -DamFile <Overview_DAM.csv> -Idm15File <IDM 15 min.csv>
param(
    [Parameter(Mandatory = $true)][string]$DamFile,
    [Parameter(Mandatory = $true)][string]$Idm15File,
    [string]$DateFrom = "2025-03-01",
    [string]$DateTo = "2026-03-01"
)
$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $workspace ".venv/Scripts/python.exe"

if (-not (Test-Path $python)) {
    throw "Python not found at $python"
}

& $python "tools/import_okte_exports.py" `
  --workspace $workspace `
  --dam-overview $DamFile `
  --idm-15min $Idm15File `
  --date-from $DateFrom `
  --date-to $DateTo `
  --fetch-system-imbalance `
  --fetch-demand-supply

Write-Host "Import completed."
