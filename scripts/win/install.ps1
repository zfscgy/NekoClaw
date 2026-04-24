<#
.SYNOPSIS
    Install NekoClaw on Windows from a distribution archive.

.DESCRIPTION
    Expands the NekoClaw-*.zip archive produced by `scripts/win/build.ps1`
    into a target directory, then invokes `resources/packpy/win64/install.ps1`
    inside the extracted tree to create the offline Python environment.

.PARAMETER Archive
    Path to the NekoClaw-*-win64.zip archive. If not provided, the script
    looks for a single `NekoClaw-*.zip` file next to itself.

.PARAMETER Destination
    Target directory to extract into. Defaults to the folder containing
    this script. The archive already contains a top-level `NekoClaw/`
    directory, so the final install path is `<Destination>\NekoClaw`.

.PARAMETER Force
    Remove an existing `<Destination>\NekoClaw` folder before extracting.

.PARAMETER SkipPythonInstall
    Extract only; do not run `resources/packpy/win64/install.ps1`.

.PARAMETER SkipConfigure
    Do not prompt the user for NekoClaw configuration (OpenAI key, base URL,
    model, locale) after the Python environment is ready.

.EXAMPLE
    # Drop install.ps1 next to NekoClaw-0.1.4.post4-win64.zip and run:
    powershell -ExecutionPolicy Bypass -File .\install.ps1
#>

param(
    [string]$Archive,
    [string]$Destination,
    [switch]$Force,
    [switch]$SkipPythonInstall,
    [switch]$SkipConfigure
)

$ErrorActionPreference = 'Stop'

$ScriptDir = $PSScriptRoot
if (-not $Destination) {
    $Destination = $ScriptDir
}

# -----------------------------------------------------------------------------
# Locate the archive
# -----------------------------------------------------------------------------
if (-not $Archive) {
    $candidates = @(Get-ChildItem -Path $ScriptDir -Filter 'NekoClaw-*.zip' -File -ErrorAction SilentlyContinue)
    if ($candidates.Count -eq 0) {
        throw "No NekoClaw-*.zip found next to install.ps1. Pass -Archive <path> explicitly."
    }
    if ($candidates.Count -gt 1) {
        $picked = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        Write-Warning "Multiple NekoClaw-*.zip archives found; using the newest: $($picked.Name)"
        $Archive = $picked.FullName
    } else {
        $Archive = $candidates[0].FullName
    }
}

if (-not (Test-Path $Archive)) {
    throw "Archive not found: $Archive"
}
$Archive = (Resolve-Path $Archive).Path

Write-Host "Archive     : $Archive" -ForegroundColor DarkGray
Write-Host "Destination : $Destination" -ForegroundColor DarkGray

# -----------------------------------------------------------------------------
# Prepare destination
# -----------------------------------------------------------------------------
if (-not (Test-Path $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}
$Destination = (Resolve-Path $Destination).Path
$InstallRoot = Join-Path $Destination 'NekoClaw'

if (Test-Path $InstallRoot) {
    if ($Force) {
        Write-Host "Removing existing $InstallRoot ..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $InstallRoot
    } else {
        throw "Target already exists: $InstallRoot. Use -Force to overwrite."
    }
}

# -----------------------------------------------------------------------------
# 1) Extract the archive
# -----------------------------------------------------------------------------
Write-Host "[1/3] Extracting archive ..." -ForegroundColor Cyan
$tarExe = Get-Command tar.exe -ErrorAction SilentlyContinue
if ($tarExe) {
    # tar handles large zips much faster than Expand-Archive.
    Push-Location $Destination
    try {
        & tar.exe -xf $Archive
        if ($LASTEXITCODE -ne 0) {
            throw "tar.exe failed to extract $Archive (exit $LASTEXITCODE)"
        }
    } finally {
        Pop-Location
    }
} else {
    Expand-Archive -Path $Archive -DestinationPath $Destination -Force
}

if (-not (Test-Path $InstallRoot)) {
    throw "Extraction did not produce expected folder: $InstallRoot"
}
Write-Host "Extracted to: $InstallRoot" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 2) Run the offline Python environment installer
# -----------------------------------------------------------------------------
if ($SkipPythonInstall) {
    Write-Host "[2/3] Skipping Python env install (-SkipPythonInstall)." -ForegroundColor Yellow
    Write-Host "[3/3] Skipping config prompt (Python env not installed)." -ForegroundColor Yellow
    Write-Host "Install complete." -ForegroundColor Green
    return
}

$PackPyInstaller = Join-Path $InstallRoot 'resources\packpy\win64\install.ps1'
if (-not (Test-Path $PackPyInstaller)) {
    throw "packpy installer not found at $PackPyInstaller"
}

Write-Host "[2/3] Installing Python environment ..." -ForegroundColor Cyan
# Invoke in a fresh scope so its Activate.ps1 side effects don't leak here.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PackPyInstaller
if ($LASTEXITCODE -ne 0) {
    throw "packpy install.ps1 failed with exit code $LASTEXITCODE"
}

# -----------------------------------------------------------------------------
# 3) Prompt the user for essential NekoClaw configuration.
#
#    Writes OpenAI base URL / API key / default model / template locale to
#    `~/.nekoclaw/config.json` (and the `providers.json` sidecar). Existing
#    values appear as defaults so pressing Enter keeps them. Runs with the
#    `main` venv's Python because that's where nekoclaw + rich + pydantic
#    are installed.
# -----------------------------------------------------------------------------
$MainPython = Join-Path $InstallRoot 'resources\packpy\win64\.venvs\main\Scripts\python.exe'

if ($SkipConfigure) {
    Write-Host "[3/3] Skipping config prompt (-SkipConfigure)." -ForegroundColor Yellow
} elseif (-not (Test-Path $MainPython)) {
    Write-Warning "[3/3] Main venv python not found at $MainPython; skipping config prompt."
} else {
    Write-Host "[3/3] Configuring NekoClaw (writes to ~/.nekoclaw/config.json) ..." -ForegroundColor Cyan
    & $MainPython -c "from nekoclaw.config.loader import prompt_configs; prompt_configs()"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Config prompt exited with code $LASTEXITCODE. You can re-run it later with:"
        Write-Warning "  `"$MainPython`" -c `"from nekoclaw.config.loader import prompt_configs; prompt_configs()`""
    }
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host ("  Location    : {0}" -f $InstallRoot) -ForegroundColor Green
Write-Host ("  Activate env: {0}" -f (Join-Path $InstallRoot 'resources\packpy\win64\.venvs\main\Scripts\Activate.ps1')) -ForegroundColor Green
Write-Host ("  Config file : {0}" -f (Join-Path $HOME '.nekoclaw\config.json')) -ForegroundColor Green
Write-Host "  Re-run config prompt later (inside activated main env):" -ForegroundColor DarkGray
Write-Host "    python -c `"from nekoclaw.config.loader import prompt_configs; prompt_configs()`"" -ForegroundColor DarkGray
