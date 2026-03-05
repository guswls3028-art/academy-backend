# 로깅 헬퍼. AWS·Cloudflare 인증: Cursor 룰에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
function Write-Step { param([string]$Message) Write-Host "`n[$Message]" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "  OK: $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "  WARN: $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "  FAIL: $Message" -ForegroundColor Red }
