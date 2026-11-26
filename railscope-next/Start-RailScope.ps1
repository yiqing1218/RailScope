$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $PSScriptRoot 'apps/desktop/src-tauri/tauri.conf.json'
$guidePath = Join-Path $PSScriptRoot 'README.md'

try {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $detected = foreach ($build in 'release', 'debug') {
        $path = Join-Path $PSScriptRoot "target/$build/railscope-next.exe"
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }

        $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($path).ProductVersion
        if ($version -eq $config.version) {
            if (-not $env:RAILSCOPE_NEXT_PACKS) {
                $env:RAILSCOPE_NEXT_PACKS = Join-Path $repo 'data/processed/railscope-next/packs'
            }
            Start-Process -FilePath $path -WorkingDirectory $repo
            exit 0
        }
        "$build ($version)"
    }

    [Console]::Error.WriteLine("RailScope Next $($config.version) is required. No matching executable was found.")
    if ($detected) { [Console]::Error.WriteLine("Local executables: $($detected -join ', ').") }
    [Console]::Error.WriteLine("Build instructions: $guidePath")
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
}
exit 1
