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

.EXAMPLE
    # Drop install.ps1 next to NekoClaw-0.1.4.post4-win64.zip and run:
    powershell -ExecutionPolicy Bypass -File .\install.ps1
#>

param(
    [string]$Archive,
    [string]$Destination,
    [switch]$Force,
    [switch]$SkipPythonInstall
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
Write-Host "[1/2] Extracting archive ..." -ForegroundColor Cyan
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
    Write-Host "[2/2] Skipping Python env install (-SkipPythonInstall)." -ForegroundColor Yellow
    Write-Host "Install complete." -ForegroundColor Green
    return
}

$PackPyInstaller = Join-Path $InstallRoot 'resources\packpy\win64\install.ps1'
if (-not (Test-Path $PackPyInstaller)) {
    throw "packpy installer not found at $PackPyInstaller"
}

Write-Host "[2/2] Installing Python environment ..." -ForegroundColor Cyan
# Invoke in a fresh scope so its Activate.ps1 side effects don't leak here.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PackPyInstaller
if ($LASTEXITCODE -ne 0) {
    throw "packpy install.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host ("  Location    : {0}" -f $InstallRoot) -ForegroundColor Green
Write-Host ("  Activate env: {0}" -f (Join-Path $InstallRoot 'resources\packpy\win64\.venvs\main\Scripts\Activate.ps1')) -ForegroundColor Green
