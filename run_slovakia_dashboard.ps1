$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" "tools\slovakia_revenue_dashboard.py" @args
