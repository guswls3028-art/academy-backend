# v1 SSOT vs 실제 인프라 일치 확인. Drift + Evidence 수집 후 보고서만 저장.
# 사용: pwsh -File scripts/v1/check-v1-infra.ps1 (run-with-env로 감싸서 실행 권장)
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
Push-Location $RepoRoot | Out-Null

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\diff.ps1")
. (Join-Path $ScriptRoot "core\evidence.ps1")
. (Join-Path $ScriptRoot "core\reports.ps1")

Load-SSOT -Env prod | Out-Null
$script:PlanMode = $true

Write-Host "`n=== v1 vs 실제 인프라 확인 ===" -ForegroundColor Cyan
$driftRows = Get-StructuralDrift
Show-DriftTable -Rows $driftRows
Save-DriftReport -Rows $driftRows

$ev = Get-EvidenceSnapshot
$md = Convert-EvidenceToMarkdown -Ev $ev
Save-EvidenceReport -MarkdownContent $md
Write-Host "`nEvidence/Audit 저장 완료." -ForegroundColor Green

$hasDrift = $driftRows | Where-Object { $_.Action -ne "NoOp" }
if ($hasDrift -and $hasDrift.Count -gt 0) {
    Write-Host "`n[요약] 일치하지 않음: $($hasDrift.Count)건 (Action != NoOp)" -ForegroundColor Yellow
    $hasDrift | ForEach-Object { Write-Host "  - $($_.ResourceType) $($_.Name): $($_.Actual) -> $($_.Action)" -ForegroundColor Gray }
    Pop-Location
    exit 1
}
Write-Host "`n[요약] v1 설정과 실제 인프라 일치 (Drift 없음)." -ForegroundColor Green
Pop-Location
exit 0
