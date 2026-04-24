<#
    Offline Python environment installer for NekoClaw (Windows x64).

    Creates two virtual environments under `.venvs\`:
      - main : runtime deps for the NekoClaw package (installed from local wheels)
      - dev  : extra tooling listed in requirements.txt (pptx / docx)

    NekoClaw itself is NOT installed as a wheel. Instead, a `.pth` file is
    written into the main venv's site-packages so `import nekoclaw` and
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
Write-Banner "[0/3] Locating python.exe"
$python = Get-ChildItem -Path "$scriptDir\python-build-standalone" -Recurse -Filter "python.exe" |
    Where-Object { $_.DirectoryName -notlike "*\venv\*" } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $python) {
    throw "python.exe not found under $scriptDir\python-build-standalone"
}
Write-Done "Found: $python"

# -----------------------------------------------------------------------------
# Stage 1: build the 'main' venv and install runtime deps from local wheels
# -----------------------------------------------------------------------------
Write-Banner "[1/3] Building 'main' venv (project runtime)"

$mainVenv = Join-Path $scriptDir ".venvs\main"
$mainPython = Join-Path $mainVenv "Scripts\python.exe"
$reqMainFile = Join-Path $scriptDir "requirements-main.txt"

if (-not (Test-Path $reqMainFile)) {
    throw "Missing $reqMainFile. Re-run build.ps1 to regenerate it."
}

Write-Step "Creating venv at $mainVenv"
& $python -m venv $mainVenv
if ($LASTEXITCODE -ne 0) { throw "Failed to create main venv (exit $LASTEXITCODE)" }
Write-Done "venv created"

Write-Step "Installing runtime dependencies (wheels-only)"
& $mainPython -m pip install --no-index --find-links "$scriptDir\wheels" -r $reqMainFile
if ($LASTEXITCODE -ne 0) { throw "pip install for main venv failed (exit $LASTEXITCODE)" }
Write-Done "runtime deps installed"

# Link the source tree into the main venv so `import nekoclaw` / `import nekochat`
# resolve to the extracted repo instead of requiring a built wheel.
Write-Step "Linking source tree via nekoclaw.pth"
$mainSitePackages = & $mainPython -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
if ($LASTEXITCODE -ne 0 -or -not $mainSitePackages) {
    throw "Could not locate site-packages for $mainPython"
}
$pthFile = Join-Path $mainSitePackages "nekoclaw.pth"
Set-Content -Path $pthFile -Value $repoRoot -Encoding ascii
Write-Done "main env ready (source linked at $repoRoot)"

# -----------------------------------------------------------------------------
# Stage 2: build the 'dev' venv and install the extra tooling requirements
# -----------------------------------------------------------------------------
Write-Banner "[2/3] Building 'dev' venv (extra tooling)"

$devVenv = Join-Path $scriptDir ".venvs\dev"
$devPython = Join-Path $devVenv "Scripts\python.exe"

Write-Step "Creating venv at $devVenv"
& $python -m venv $devVenv
if ($LASTEXITCODE -ne 0) { throw "Failed to create dev venv (exit $LASTEXITCODE)" }
Write-Done "venv created"

Write-Step "Installing requirements.txt"
& $devPython -m pip install --no-index --find-links "$scriptDir\wheels" -r "$scriptDir\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install for dev venv failed (exit $LASTEXITCODE)" }
Write-Done "dev env ready"

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
Write-Banner "[3/3] All venvs installed"
Write-Host ("  main : {0}" -f $mainPython) -ForegroundColor Green
Write-Host ("  dev  : {0}" -f $devPython)  -ForegroundColor Green
Write-Host ""
