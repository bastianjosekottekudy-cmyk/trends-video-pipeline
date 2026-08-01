# Requires PowerShell 5.1+
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Virtual environment not found. Run scripts\setup-windows.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "Starting Trends Video Pipeline..."
Write-Host "Dashboard: http://127.0.0.1:8080"
& .\.venv\Scripts\python.exe -m src.main
