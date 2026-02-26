# SSOT v3 — 단계 로깅
function Write-Step {
    param([string]$Message, [string]$Color = "Cyan")
    Write-Host "`n[$Message]" -ForegroundColor $Color
}

function Write-Ok { param([string]$Message) Write-Host "  OK: $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "  WARN: $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "  FAIL: $Message" -ForegroundColor Red }
