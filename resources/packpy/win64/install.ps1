$root = $PSScriptRoot

Write-Host "Locating python.exe..." -ForegroundColor Cyan
$python = Get-ChildItem -Path "$root\python-build-standalone" -Recurse -Filter "python.exe" |
    Where-Object { $_.DirectoryName -notlike "*\venv\*" } |
    Select-Object -First 1 -ExpandProperty FullName
Write-Host "Found: $python" -ForegroundColor Green



Write-Host "Creating virtual environment 'main'..." -ForegroundColor Cyan
& $python -m venv "$root\.venvs\main"
Write-Host "Virtual environment created." -ForegroundColor Green

Write-Host "Activating virtual environment..." -ForegroundColor Cyan
. "$root\.venvs\main\Scripts\Activate.ps1"
Write-Host "Activated: $($env:VIRTUAL_ENV)" -ForegroundColor Green

Write-Host "Installing packages from wheels..." -ForegroundColor Cyan
pip install --no-index --find-links "$root\wheels" "$root/../../"
Write-Host "main env set." -ForegroundColor Green

Write-Host "Creating virtual environment 'dev'..." -ForegroundColor Cyan
& $python -m venv "$root\.venvs\dev"
Write-Host "Virtual environment created." -ForegroundColor Green

Write-Host "Activating virtual environment..." -ForegroundColor Cyan
. "$root\.venvs\dev\Scripts\Activate.ps1"
Write-Host "Activated: $($env:VIRTUAL_ENV)" -ForegroundColor Green

Write-Host "Installing packages from wheels..." -ForegroundColor Cyan
pip install --no-index --find-links "$root\wheels" -r "$root\requirements.txt"
Write-Host "dev env set." -ForegroundColor Green
