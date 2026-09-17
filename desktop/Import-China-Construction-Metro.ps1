param(
    [string]$ProjectRoot = "D:\RailScope"
)

$ErrorActionPreference = 'Stop'
$output = Join-Path $ProjectRoot 'data\processed\osm'
$raw = Join-Path $ProjectRoot 'data\raw\osm\china-latest.osm.pbf'
$log = Join-Path $output 'china_metro_construction_import.log'
New-Item -ItemType Directory -Force -Path $output | Out-Null
"[$(Get-Date -Format s)] Starting explicit OSM construction-metro extraction." | Set-Content -Path $log -Encoding utf8
Push-Location (Join-Path $ProjectRoot 'backend')
try {
    & D:\Python312\python.exe -m railscope.cli metro construction $raw --output $output *>> $log
    "[$(Get-Date -Format s)] Completed successfully." | Add-Content -Path $log -Encoding utf8
} catch {
    "[$(Get-Date -Format s)] FAILED: $($_.Exception.Message)" | Add-Content -Path $log -Encoding utf8
    exit 1
} finally {
    Pop-Location
}
