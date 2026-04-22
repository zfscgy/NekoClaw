<#
.SYNOPSIS
    Build the NekoClaw Windows distribution package.

.DESCRIPTION
    Collects the Python source tree, the offline Python environment
    (resources/packpy), the bundled Chrome runtime (resources/chrome) and
    the nekochat frontend dist into a single staging folder, and then
    compresses the whole tree into `build/win/NekoClaw-<version>-win64.zip`.

.PARAMETER KeepStaging
    Keep the intermediate staging folder under `build/win/staging/` after
    the archive has been produced. Useful for debugging.

.PARAMETER SkipArchive
    Only prepare the staging folder; do not create the final zip archive.
#>

param(
    [switch]$KeepStaging,
    [switch]$SkipArchive
)

$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
$ScriptDir   = $PSScriptRoot
$RepoRoot    = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$BuildDir    = Join-Path $RepoRoot 'build\win'
$StagingRoot = Join-Path $BuildDir 'staging'
$PkgName     = 'NekoClaw'
$StagingPkg  = Join-Path $StagingRoot $PkgName

Write-Host "Repo root   : $RepoRoot" -ForegroundColor DarkGray
Write-Host "Build dir   : $BuildDir" -ForegroundColor DarkGray
Write-Host "Staging dir : $StagingPkg" -ForegroundColor DarkGray

# -----------------------------------------------------------------------------
# Read version from pyproject.toml
# -----------------------------------------------------------------------------
$PyProject = Join-Path $RepoRoot 'pyproject.toml'
if (-not (Test-Path $PyProject)) {
    throw "pyproject.toml not found at $PyProject"
}
$VersionMatch = Select-String -Path $PyProject -Pattern '^\s*version\s*=\s*"([^"]+)"' |
    Select-Object -First 1
if (-not $VersionMatch) {
    throw "Could not parse version from $PyProject"
}
$Version = $VersionMatch.Matches[0].Groups[1].Value
Write-Host "Version     : $Version" -ForegroundColor DarkGray

$ArchivePath = Join-Path $BuildDir "$PkgName-$Version-win64.zip"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
function Invoke-Robocopy {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )

    $roboArgs = @($Source, $Destination, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
    if ($ExcludeDirs.Count -gt 0) {
        $roboArgs += '/XD'
        $roboArgs += $ExcludeDirs
    }
    if ($ExcludeFiles.Count -gt 0) {
        $roboArgs += '/XF'
        $roboArgs += $ExcludeFiles
    }

    & robocopy @roboArgs | Out-Null
    # Robocopy exit codes 0-7 mean success; >=8 is a failure.
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed ($Source -> $Destination) with exit code $LASTEXITCODE"
    }
    $global:LASTEXITCODE = 0
}

function Copy-File {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $destDir = Split-Path -Parent $Destination
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

# -----------------------------------------------------------------------------
# Prepare staging
# -----------------------------------------------------------------------------
if (Test-Path $StagingPkg) {
    Write-Host "Cleaning previous staging..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force $StagingPkg
}
New-Item -ItemType Directory -Path $StagingPkg -Force | Out-Null
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

# -----------------------------------------------------------------------------
# 1) Copy resources/packpy (offline Python env + wheels)
# -----------------------------------------------------------------------------
$PackPySrc = Join-Path $RepoRoot 'resources\packpy'
$PackPyDst = Join-Path $StagingPkg 'resources\packpy'
if (-not (Test-Path $PackPySrc)) {
    throw "resources/packpy not found at $PackPySrc"
}
$WheelsDir = Join-Path $PackPySrc 'win64\wheels'
if (-not (Test-Path $WheelsDir) -or -not (Get-ChildItem -Path $WheelsDir -Filter '*.whl' -ErrorAction SilentlyContinue)) {
    Write-Warning "No wheels found under resources/packpy/win64/wheels. Run resources/packpy/win64/build.ps1 first."
}
$PbsDir = Join-Path $PackPySrc 'win64\python-build-standalone'
if (-not (Test-Path $PbsDir) -or -not (Get-ChildItem -Path $PbsDir -ErrorAction SilentlyContinue)) {
    Write-Warning "python-build-standalone directory is empty at resources/packpy/win64. Download the standalone runtime before packaging."
}
Write-Host "[1/5] Copying resources/packpy ..." -ForegroundColor Cyan
Invoke-Robocopy -Source $PackPySrc -Destination $PackPyDst `
    -ExcludeDirs @('.venvs', '__pycache__')

# -----------------------------------------------------------------------------
# 2) Copy resources/chrome (bundled Chrome runtime)
# -----------------------------------------------------------------------------
$ChromeSrc = Join-Path $RepoRoot 'resources\chrome'
$ChromeDst = Join-Path $StagingPkg 'resources\chrome'
if (-not (Test-Path $ChromeSrc)) {
    throw "resources/chrome not found at $ChromeSrc"
}
Write-Host "[2/5] Copying resources/chrome ..." -ForegroundColor Cyan
Invoke-Robocopy -Source $ChromeSrc -Destination $ChromeDst `
    -ExcludeFiles @('debug.log')

# -----------------------------------------------------------------------------
# 3) Copy nekochat_frontend/dist
# -----------------------------------------------------------------------------
$FrontendDistSrc = Join-Path $RepoRoot 'nekochat\nekochat_frontend\dist'
$FrontendDistDst = Join-Path $StagingPkg 'nekochat\nekochat_frontend\dist'
if (-not (Test-Path $FrontendDistSrc)) {
    throw "nekochat_frontend/dist not found at $FrontendDistSrc. Run ``npm run build`` inside nekochat/nekochat_frontend first."
}
Write-Host "[3/5] Copying nekochat_frontend/dist ..." -ForegroundColor Cyan
Invoke-Robocopy -Source $FrontendDistSrc -Destination $FrontendDistDst

# -----------------------------------------------------------------------------
# 4) Copy Python source code
# -----------------------------------------------------------------------------
Write-Host "[4/5] Copying Python source ..." -ForegroundColor Cyan

# nekoclaw package
Invoke-Robocopy -Source (Join-Path $RepoRoot 'nekoclaw') `
    -Destination (Join-Path $StagingPkg 'nekoclaw') `
    -ExcludeDirs @('__pycache__', '.pytest_cache')

# lightsear support package (used at runtime if present)
$LightsearSrc = Join-Path $RepoRoot 'lightsear'
if (Test-Path $LightsearSrc) {
    Invoke-Robocopy -Source $LightsearSrc `
        -Destination (Join-Path $StagingPkg 'lightsear') `
        -ExcludeDirs @('__pycache__', '.pytest_cache')
}

# Top-level metadata files required by pip install from source
foreach ($name in @('pyproject.toml', 'README.md', 'LICENSE')) {
    $src = Join-Path $RepoRoot $name
    if (Test-Path $src) {
        Copy-File -Source $src -Destination (Join-Path $StagingPkg $name)
    }
}

# -----------------------------------------------------------------------------
# 5) Create archive
# -----------------------------------------------------------------------------
if ($SkipArchive) {
    Write-Host "[5/5] Skipping archive creation (-SkipArchive)." -ForegroundColor Yellow
    Write-Host "Staging ready at: $StagingPkg" -ForegroundColor Green
    return
}

Write-Host "[5/5] Creating archive ..." -ForegroundColor Cyan
if (Test-Path $ArchivePath) {
    Remove-Item -Force $ArchivePath
}

# Prefer tar.exe (Windows 10+/Server 2019+) for speed on large trees; fall back
# to Compress-Archive when unavailable.
$tarExe = Get-Command tar.exe -ErrorAction SilentlyContinue
if ($tarExe) {
    Push-Location $StagingRoot
    try {
        & tar.exe -a -cf $ArchivePath $PkgName
        if ($LASTEXITCODE -ne 0) {
            throw "tar.exe failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
} else {
    Compress-Archive -Path (Join-Path $StagingRoot "$PkgName\*") `
        -DestinationPath $ArchivePath -Force
}

# Also publish install.ps1 next to the archive so users can fetch both together.
$InstallScriptSrc = Join-Path $ScriptDir 'install.ps1'
if (Test-Path $InstallScriptSrc) {
    Copy-File -Source $InstallScriptSrc -Destination (Join-Path $BuildDir 'install.ps1')
}

# -----------------------------------------------------------------------------
# Clean up
# -----------------------------------------------------------------------------
if (-not $KeepStaging) {
    Write-Host "Cleaning staging ..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force $StagingRoot
}

$ArchiveSizeMB = [math]::Round((Get-Item $ArchivePath).Length / 1MB, 1)
Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host ("  Archive : {0} ({1} MB)" -f $ArchivePath, $ArchiveSizeMB) -ForegroundColor Green
Write-Host ("  Installer : {0}" -f (Join-Path $BuildDir 'install.ps1')) -ForegroundColor Green
