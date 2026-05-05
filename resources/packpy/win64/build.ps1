# Download all wheels needed for the offline install.
# NekoClaw itself is NOT built into a wheel - it is run from the source tree.
# We only download third-party dev/tooling dependencies here.

$ErrorActionPreference = 'Stop'
$scriptDir = $PSScriptRoot

Write-Host "Locating python.exe..." -ForegroundColor Cyan
$python = Get-ChildItem -Path "$scriptDir\python-build-standalone" -Recurse -Filter "python.exe" |
    Where-Object { $_.DirectoryName -notlike "*\venv\*" } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $python) { throw "python.exe not found under $scriptDir\python-build-standalone" }
Write-Host "Found: $python" -ForegroundColor Green

# -----------------------------------------------------------------------------
# Download wheels for the dev environment into ./wheels
# -----------------------------------------------------------------------------
Write-Host "Downloading dev/tooling packages (requirements.txt)..." -ForegroundColor Cyan
& $python -m pip download -r "$scriptDir\requirements.txt" -d $SCriptDir/wheels
if ($LASTEXITCODE -ne 0) { throw "pip download (requirements.txt) failed (exit $LASTEXITCODE)" }

Write-Host "All packages downloaded to $scriptDir\wheels" -ForegroundColor Green
