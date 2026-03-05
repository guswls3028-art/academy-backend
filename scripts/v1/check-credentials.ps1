# AWS 루트 권한 및 Cloudflare 권한 확인 전용.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키를 환경변수로 넣어 배포·검증·인증을 진행한다. 본 스크립트는 .env에서 AWS_*, CLOUDFLARE_* 로드 후 get-caller-identity / wrangler whoami 실행.
# 배포 플로우에서는 사용하지 않음. 에이전트가 권한 체크할 때만 실행.
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$envPath = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envPath)) {
    Write-Host "FAIL: .env not found at $envPath" -ForegroundColor Red
    exit 1
}
foreach ($line in (Get-Content -Path $envPath -Encoding UTF8 -ErrorAction SilentlyContinue)) {
    $t = $line.Trim()
    if ($t -match '^\s*#' -or $t -eq "") { continue }
    if ($t -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $k = $matches[1].Trim()
        $v = $matches[2].Trim() -replace '^"|"$', ''
        if ($k -match '^AWS_|^CLOUDFLARE_') {
            [Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }
}
$region = $env:AWS_DEFAULT_REGION; if (-not $region) { $region = $env:AWS_REGION }; if (-not $region) { $region = "ap-northeast-2" }

Write-Host "`n=== AWS 권한 체크 (get-caller-identity) ===" -ForegroundColor Cyan
$awsOut = aws sts get-caller-identity --region $region 2>&1
$awsOk = $LASTEXITCODE -eq 0
if ($awsOk) {
    Write-Host "PASS" -ForegroundColor Green
    $awsOut
} else {
    Write-Host "FAIL" -ForegroundColor Red
    Write-Host $awsOut
}

Write-Host "`n=== Cloudflare 권한 체크 (wrangler whoami) ===" -ForegroundColor Cyan
$cfOut = npx wrangler whoami 2>&1
$cfOk = $LASTEXITCODE -eq 0
if ($cfOk) {
    Write-Host "PASS" -ForegroundColor Green
    $cfOut
} else {
    Write-Host "FAIL" -ForegroundColor Red
    Write-Host $cfOut
}

if (-not $awsOk -or -not $cfOk) { exit 1 }
Write-Host "`nCredentials check OK." -ForegroundColor Green
