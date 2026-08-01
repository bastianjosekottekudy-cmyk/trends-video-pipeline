# Requires PowerShell 5.1+
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "=== Trends Video Pipeline - Windows Setup ===" -ForegroundColor Cyan

# Check Python
$python = $null
foreach ($candidate in @("python", "python3", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    Write-Host "Python not found. Install with: winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}
Write-Host "Using Python: $python"

# Check FFmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "FFmpeg not found. Install with: winget install Gyan.FFmpeg" -ForegroundColor Yellow
} else {
    Write-Host "FFmpeg: OK"
}

# Create venv
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    & $python -m venv .venv
}

Write-Host "Installing dependencies..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt

# Copy .env if missing
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example - fill in YouTube credentials." -ForegroundColor Yellow
}

# Create secrets dir
New-Item -ItemType Directory -Force -Path secrets | Out-Null
New-Item -ItemType Directory -Force -Path output | Out-Null

Write-Host ""
Write-Host "Setup complete!" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Start: .\scripts\run.ps1"
Write-Host "     Or background restart: .\scripts\restart-app.ps1 -Background"
Write-Host "  2. Auto-start at logon: .\scripts\restart-app.ps1 -RegisterStartup"
Write-Host "  3. Open: http://127.0.0.1:8080"
Write-Host "  4. Generate a video (videos are stored locally under output/)"

