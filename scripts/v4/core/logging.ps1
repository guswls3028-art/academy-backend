function Write-Step { param([string]$Message) Write-Host "`n[$Message]" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "  OK: $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "  WARN: $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "  FAIL: $Message" -ForegroundColor Red }
