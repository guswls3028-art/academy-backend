# ==============================================================================
# Plan 래퍼 — deploy.ps1 -Plan 호출. 가독성용.
# AWS/Cloudflare credentials are supplied by the caller through the intended profile or process environment; this script does not load backend/.env.
# Usage: pwsh scripts/v1/plan.ps1 [-PruneLegacy]
# ==============================================================================
$ScriptRoot = $PSScriptRoot
& (Join-Path $ScriptRoot "deploy.ps1") -Plan @args
