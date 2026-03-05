# ==============================================================================
# Plan 래퍼 — deploy.ps1 -Plan 호출. 가독성용.
# Usage: pwsh scripts/v4/plan.ps1 [-PruneLegacy]
# ==============================================================================
$ScriptRoot = $PSScriptRoot
& (Join-Path $ScriptRoot "deploy.ps1") -Plan @args
