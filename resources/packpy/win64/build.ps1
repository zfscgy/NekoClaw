# This script should run in its folder

$root = $PSScriptRoot

Write-Host "Locating python.exe..." -ForegroundColor Cyan
$python = Get-ChildItem -Path "$root\python-build-standalone" -Recurse -Filter "python.exe" |
    Where-Object { $_.DirectoryName -notlike "*\venv\*" } |
    Select-Object -First 1 -ExpandProperty FullName
Write-Host "Found: $python" -ForegroundColor Green

Write-Host "Downloading packages..." -ForegroundColor Cyan

Write-Host "Downloading nanobot runtime packages..." -ForegroundColor Cyan
& $python -m pip download ../../. -d ./wheels

Write-Host "Downloading nekoclaw exec env packages..." -ForegroundColor Cyan
& $python -m pip download -r requirements.txt -d ./wheels
Write-Host "All packages downloaded." -ForegroundColor Green