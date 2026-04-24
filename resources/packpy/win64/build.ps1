# Download all wheels needed for the offline install.
# NekoClaw itself is NOT built into a wheel - it is run from the source tree.
# We only download third-party runtime dependencies here.

$ErrorActionPreference = 'Stop'
$scriptDir = $PSScriptRoot
$repoRoot  = Resolve-Path (Join-Path $scriptDir '..\..\..')

Write-Host "Locating python.exe..." -ForegroundColor Cyan
$python = Get-ChildItem -Path "$scriptDir\python-build-standalone" -Recurse -Filter "python.exe" |
    Where-Object { $_.DirectoryName -notlike "*\venv\*" } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $python) { throw "python.exe not found under $scriptDir\python-build-standalone" }
Write-Host "Found: $python" -ForegroundColor Green

# -----------------------------------------------------------------------------
# Extract [project].dependencies from pyproject.toml into requirements-main.txt
# so install.ps1 can install them offline without touching the build backend.
# -----------------------------------------------------------------------------
$pyproject    = Join-Path $repoRoot 'pyproject.toml'
$reqMainFile  = Join-Path $scriptDir 'requirements-main.txt'

Write-Host "Extracting runtime dependencies from $pyproject ..." -ForegroundColor Cyan
$extractScript = @"
import sys, tomllib
with open(r'''$pyproject''', 'rb') as f:
    data = tomllib.load(f)
deps = data.get('project', {}).get('dependencies', [])
sys.stdout.write('\n'.join(deps) + '\n')
"@
$deps = & $python -c $extractScript
if ($LASTEXITCODE -ne 0) { throw "Failed to read dependencies from pyproject.toml" }
$deps | Set-Content -Encoding utf8 $reqMainFile
Write-Host "Wrote $reqMainFile" -ForegroundColor Green

# -----------------------------------------------------------------------------
# Download wheels for both dependency sets into ./wheels
# -----------------------------------------------------------------------------
Write-Host "Downloading NekoClaw runtime dependencies..." -ForegroundColor Cyan
& $python -m pip download -r $reqMainFile -d ./wheels
if ($LASTEXITCODE -ne 0) { throw "pip download (main deps) failed (exit $LASTEXITCODE)" }

Write-Host "Downloading extra dev/tooling packages (requirements.txt)..." -ForegroundColor Cyan
& $python -m pip download -r "$scriptDir\requirements.txt" -d ./wheels
if ($LASTEXITCODE -ne 0) { throw "pip download (requirements.txt) failed (exit $LASTEXITCODE)" }

Write-Host "All packages downloaded to $scriptDir\wheels" -ForegroundColor Green
