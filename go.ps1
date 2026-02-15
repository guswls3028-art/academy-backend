# ==============================================================================
# 원터치: add → commit → push 한 방
# ==============================================================================

try {
    $ErrorActionPreference = "Stop"
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (-not $scriptDir) { $scriptDir = (Get-Location).Path }
    Set-Location $scriptDir

    $msg = $args[0]
    if (-not $msg) { $msg = "update: " + (Get-Date -Format "yyyy-MM-dd HH:mm") }

    Write-Host "`n=== Go (add -> commit -> push) ===" -ForegroundColor Cyan
    Write-Host "Message: $msg`n" -ForegroundColor Gray

    & git add -A
    if ($LASTEXITCODE -ne 0) { throw "git add 실패" }
    $status = & git status --short 2>&1
    if (-not $status) {
        Write-Host "변경 없음. 푸시 생략." -ForegroundColor Yellow
        exit 0
    }

    & git commit -m $msg
    if ($LASTEXITCODE -ne 0) { throw "git commit 실패 (이미 커밋됐거나 메시지 확인)" }

    & git push
    if ($LASTEXITCODE -ne 0) { throw "git push 실패. 원격/브랜치/인증 확인." }

    Write-Host "`nPush 완료. main이면 Actions에서 빌드 -> ECR 푸시됩니다." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "`n오류: $_" -ForegroundColor Red
    exit 1
}
