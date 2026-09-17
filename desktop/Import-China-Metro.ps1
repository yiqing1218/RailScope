param([int]$DownloadProcessId)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root 'data\logs\china-metro-import.log'
New-Item -ItemType Directory -Force (Split-Path -Parent $log) | Out-Null

try {
    Wait-Process -Id $DownloadProcessId -ErrorAction Stop
    Set-Location (Join-Path $root 'backend')
    & D:\Python312\python.exe -m railscope.cli metro import `
        ..\data\raw\osm\china-latest.osm.pbf --output ..\data\processed\osm *>> $log
    if ($LASTEXITCODE -ne 0) { throw "Metro extraction returned exit code $LASTEXITCODE" }
    Add-Content -LiteralPath $log -Value "[$(Get-Date -Format s)] National metro import completed. Restart RailScope to load the national layer."
} catch {
    Add-Content -LiteralPath $log -Value "[$(Get-Date -Format s)] ERROR: $($_.Exception.Message)"
}
