param([switch]$NoPause, [switch]$CheckOnly, [switch]$NoInstall)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$logDirectory = Join-Path $projectRoot 'data\logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logPath = Join-Path $logDirectory 'startup.log'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$transcriptStarted = $false

function Test-PythonCommand {
    param([string]$Executable, [string[]]$LeadingArguments = @(), [string]$Code)
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5 converts native stderr into error records.
        # Failed probes are expected and must not abort first-time setup.
        $ErrorActionPreference = 'Continue'
        & $Executable @LeadingArguments -c $Code 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally { $ErrorActionPreference = $previousPreference }
}

function Invoke-NativeChecked {
    param([string]$Executable, [string[]]$CommandArguments)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @CommandArguments
        $commandExitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($commandExitCode -ne 0) { throw "Command failed (exit code $commandExitCode). See startup.log above." }
}

try {
    Start-Transcript -Path $logPath -Append | Out-Null
    $transcriptStarted = $true
    Push-Location $projectRoot
    Write-Host 'RailScope: checking Python and local dependencies...'
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if ((Test-Path -LiteralPath $pythonPath) -and -not (Test-PythonCommand -Executable $pythonPath -Code 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)')) {
        throw 'The local .venv is unusable or was copied from another computer. Rename .venv to .venv-backup and run Start-RailScope.cmd again to create a fresh environment. Your data is not affected.'
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        $pythonCandidate = $null
        foreach ($candidateName in @('py', 'python', 'python3')) {
            $candidateCommand = Get-Command $candidateName -ErrorAction SilentlyContinue
            if (-not $candidateCommand) { continue }
            $candidateArguments = @()
            if ($candidateName -eq 'py') { $candidateArguments = @('-3') }
            if (Test-PythonCommand -Executable $candidateCommand.Source -LeadingArguments $candidateArguments -Code 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)') {
                $pythonCandidate = $candidateCommand.Source
                $pythonArguments = $candidateArguments
                break
            }
        }
        if (-not $pythonCandidate) {
            throw 'Python 3.12+ was not found. Install Python from https://www.python.org/downloads/windows/ and run this script again.'
        }
        if ($NoInstall) { throw 'Local .venv is missing. Run without -NoInstall to set it up.' }
        Write-Host 'Creating a project-local Python environment (no administrator access needed)...'
        Invoke-NativeChecked -Executable $pythonCandidate -CommandArguments ($pythonArguments + @('-m', 'venv', (Join-Path $projectRoot '.venv')))
    }
    if (-not (Test-PythonCommand -Executable $pythonPath -Code "import pip; p=tuple(int(x) for x in pip.__version__.split('.')[:2]); raise SystemExit(0 if p >= (26,2) else 1)")) {
        if ($NoInstall) { throw 'The local pip is older than the required security baseline (26.2).' }
        Write-Host 'Updating the local package installer to the security baseline...'
        Invoke-NativeChecked -Executable $pythonPath -CommandArguments @('-m', 'pip', 'install', '--upgrade', 'pip>=26.2', '--disable-pip-version-check')
    }
    if (-not (Test-PythonCommand -Executable $pythonPath -Code 'import PySide6, osmium; from PySide6.QtWebEngineWidgets import QWebEngineView')) {
        if ($NoInstall) { throw 'Desktop dependencies are missing.' }
        Write-Host 'Installing desktop dependencies. First launch requires internet and may take several minutes...'
        Invoke-NativeChecked -Executable $pythonPath -CommandArguments @('-m', 'pip', 'install', '-r', (Join-Path $scriptRoot 'requirements.txt'), '--disable-pip-version-check', '--retries', '5', '--timeout', '90')
    }
    Invoke-NativeChecked -Executable $pythonPath -CommandArguments @((Join-Path $scriptRoot 'bootstrap.py'))
    if ($CheckOnly) {
        Write-Host 'RailScope startup checks passed.'
    } else {
        Write-Host 'Opening RailScope. Metro data can be downloaded from the Data Sources menu.'
        Invoke-NativeChecked -Executable $pythonPath -CommandArguments @((Join-Path $scriptRoot 'launcher.py'))
    }
} catch {
    Write-Host ('RailScope startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('Log: ' + $logPath)
    if (-not $NoPause) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
} finally {
    Pop-Location -ErrorAction SilentlyContinue
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
