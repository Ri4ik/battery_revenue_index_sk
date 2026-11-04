param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$Inbox
)
$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
if (-not $Inbox) {
    $Inbox = Join-Path $taskRepo 'incoming'
    $taskEnvFile = Join-Path $taskRepo '.env'
    if (Test-Path -LiteralPath $taskEnvFile) {
        $taskConfigured = Get-Content -LiteralPath $taskEnvFile | Where-Object { $_ -match '^BRI_INBOX_HOST=' } | Select-Object -Last 1
        if ($taskConfigured) {
            $Inbox = ($taskConfigured -split '=', 2)[1].Trim().Trim('"').Trim("'")
            if (-not [IO.Path]::IsPathRooted($Inbox)) { $Inbox = Join-Path $taskRepo $Inbox }
        }
    }
}
$taskSource = Get-Item -LiteralPath $Path
if ($taskSource.PSIsContainer -or $taskSource.Extension -ine '.xlsx' -or ($taskSource.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Specify one regular SEPS .xlsx file.'
}
New-Item -ItemType Directory -Path $Inbox -Force | Out-Null
$taskInboxPath = (Resolve-Path -LiteralPath $Inbox).Path
$taskFinal = Join-Path $taskInboxPath $taskSource.Name
if (Test-Path -LiteralPath $taskFinal) { throw "A file with this name already exists in incoming: $taskFinal" }
$taskPartial = Join-Path $taskInboxPath ([Guid]::NewGuid().ToString('N') + '.xlsx.part')
try {
    Copy-Item -LiteralPath $taskSource.FullName -Destination $taskPartial
    $taskSourceHash = (Get-FileHash -LiteralPath $taskSource.FullName -Algorithm SHA256).Hash
    $taskCopyHash = (Get-FileHash -LiteralPath $taskPartial -Algorithm SHA256).Hash
    if ($taskSourceHash -ne $taskCopyHash) { throw 'The source changed during copying; retry when the download is complete.' }
    # Rename on the same filesystem publishes the complete upload atomically.
    Move-Item -LiteralPath $taskPartial -Destination $taskFinal
    Write-Output "Uploaded: $taskFinal"
    Write-Output "SHA-256: $taskCopyHash"
    Write-Output 'Wait at least 10 seconds after upload, then run:'
    Write-Output 'docker compose -p bri-db-pipeline exec worker python -m bri.pipeline --source local'
} finally {
    if (Test-Path -LiteralPath $taskPartial) { Remove-Item -LiteralPath $taskPartial }
}
