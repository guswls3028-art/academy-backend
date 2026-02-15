# ==============================================================================
# Git 푸시 바로가기 — 더블클릭 또는 .\push.ps1
# (변경사항 add → commit → push → GitHub Actions 빌드 자동 트리거)
# ==============================================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$msg = $args[0]
if (-not $msg) {
    $msg = "update: " + (Get-Date -Format "yyyy-MM-dd HH:mm")
}

Write-Host "`n=== Git Push ===" -ForegroundColor Cyan
Write-Host "Message: $msg`n" -ForegroundColor Gray

git add -A
$status = git status --short
if (-not $status) {
    Write-Host "변경 없음. 푸시 생략." -ForegroundColor Yellow
    exit 0
}

git commit -m $msg
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git push
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nPush 실패. 원격/브랜치 확인." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`nPush 완료. main이면 Actions에서 빌드가 돌아갑니다." -ForegroundColor Green
