# ==============================================================================
# Plan 래퍼 — deploy.ps1 -Plan 호출. 가독성용.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키를 환경변수로 넣어 배포·검증·인증을 진행한다.
# Usage: pwsh scripts/v1/plan.ps1 [-PruneLegacy]
# ==============================================================================
$ScriptRoot = $PSScriptRoot
& (Join-Path $ScriptRoot "deploy.ps1") -Plan @args
