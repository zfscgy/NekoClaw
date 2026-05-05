<#
    Offline Python environment installer for NekoClaw (Windows x64).

    Creates one virtual environment under `.venvs\`:
      - dev  : tooling listed in requirements.txt (pptx / docx)

    NekoClaw itself is NOT installed as a wheel. Instead, a `.pth` file is
    written into the dev venv's site-packages so `import nekoclaw` and
    `import nekochat` resolve directly to the extracted source tree.
#>

$ErrorActionPreference = 'Stop'
$scriptDir = $PSScriptRoot
$repoRoot  = (Resolve-Path (Join-Path $scriptDir '..\..\..')).Path

# -----------------------------------------------------------------------------
# Pretty-printing helpers
# -----------------------------------------------------------------------------
$BarChar = '='
$BarWidth = 70

function Write-Banner {
    param([string]$Title)
    $bar = $BarChar * $BarWidth
    Write-Host ""
    Write-Host $bar -ForegroundColor DarkCyan
    Write-Host (" {0}" -f $Title) -ForegroundColor Cyan
    Write-Host $bar -ForegroundColor DarkCyan
}

function Write-Step {
    param([string]$Message)
    Write-Host ("  -> {0}" -f $Message) -ForegroundColor DarkGray
}

function Write-Done {
    param([string]$Message)
    Write-Host ("  [OK] {0}" -f $Message) -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# Stage 0: locate the standalone Python interpreter
# -----------------------------------------------------------------------------
Write-Banner "[0/2] Locating python.exe"
$python = Get-ChildItem -Path "$scriptDir\python-build-standalone" -Recurse -Filter "python.exe" |
    Where-Object { $_.DirectoryName -notlike "*\venv\*" } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $python) {
    throw "python.exe not found under $scriptDir\python-build-standalone"
}
Write-Done "Found: $python"

# -----------------------------------------------------------------------------
# Stage 1: build the 'dev' venv and install tooling requirements
# -----------------------------------------------------------------------------
Write-Banner "[1/2] Building 'dev' venv"

$devVenv = Join-Path $scriptDir ".venvs\dev"
$devPython = Join-Path $devVenv "Scripts\python.exe"

Write-Step "Creating venv at $devVenv"
& $python -m venv $devVenv
if ($LASTEXITCODE -ne 0) { throw "Failed to create dev venv (exit $LASTEXITCODE)" }
Write-Done "venv created"

Write-Step "Installing requirements.txt"
& $devPython -m pip install --no-index --find-links "$scriptDir\wheels" -r "$scriptDir\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install for dev venv failed (exit $LASTEXITCODE)" }

# Link the source tree into the dev venv so `import nekoclaw` / `import nekochat`
# resolve to the extracted repo instead of requiring a built wheel.
Write-Step "Linking source tree via nekoclaw.pth"
$devSitePackages = & $devPython -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
if ($LASTEXITCODE -ne 0 -or -not $devSitePackages) {
    throw "Could not locate site-packages for $devPython"
}
$pthFile = Join-Path $devSitePackages "nekoclaw.pth"
Set-Content -Path $pthFile -Value $repoRoot -Encoding ascii
Write-Done "dev env ready (source linked at $repoRoot)"

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
Write-Banner "[2/2] Venv installed"
Write-Host ("  dev : {0}" -f $devPython) -ForegroundColor Green
Write-Host ""
