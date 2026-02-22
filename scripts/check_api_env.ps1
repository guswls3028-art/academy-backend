# ==============================================================================
# API Env Settings Verify
# 1) Docker + academy-api 있음 → 컨테이너 내부에서 실행
# 2) Docker 미가동/컨테이너 없음 → 로컬 .env 기반으로 실행
# 3) -ApiIp, -KeyPath 지정 → 원격 EC2 SSH로 실행
# Usage (local): .\scripts\check_api_env.ps1
# Usage (remote): .\scripts\check_api_env.ps1 -ApiIp 1.2.3.4 -KeyPath C:\key\backend-api-key.pem
# ==============================================================================

param(
    [string]$ApiIp = "",
    [string]$KeyPath = "",
    [switch]$ShowSecrets
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "== API Env Settings Verify ==" -ForegroundColor Cyan

$extraArgs = if ($ShowSecrets) { " --verbose" } else { "" }

function Run-LocalCheck {
    $root = Split-Path -Parent $PSScriptRoot
    Push-Location $root
    try {
        python manage.py check_api_env_settings $extraArgs
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

if ($ApiIp) {
    if (-not $KeyPath) {
        Write-Host "FAIL: -KeyPath required for remote check (e.g. -KeyPath C:\path\to\key.pem)" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path -LiteralPath $KeyPath)) {
        Write-Host "FAIL: KeyPath file not found: $KeyPath" -ForegroundColor Red
        exit 1
    }
    $EC2_USER = "ec2-user"
    $remoteCmd = "docker exec academy-api python manage.py check_api_env_settings$extraArgs"
    $sshCmd = "ssh -o StrictHostKeyChecking=accept-new -i `"$KeyPath`" ${EC2_USER}@${ApiIp} `"$remoteCmd`""
    Invoke-Expression $sshCmd
} else {
    # Docker 사용 가능 여부 확인 (에러 무시)
    $eap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $cid = $null
    try {
        $out = docker ps -q --filter name=academy-api 2>&1
        if ($out -and $out -notmatch "failed to connect|cannot find|error|No such") {
            $cid = ($out | Out-String).Trim()
        }
    } catch { }
    $ErrorActionPreference = $eap

    if ($cid) {
        docker exec $cid python manage.py check_api_env_settings $extraArgs
    } else {
        Write-Host "Docker unavailable or academy-api not running. Checking .env locally..." -ForegroundColor Yellow
        Run-LocalCheck
    }
}

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "FAIL: Required env vars missing" -ForegroundColor Red
    Write-Host "  Local: add VIDEO_BATCH_JOB_QUEUE, VIDEO_BATCH_JOB_DEFINITION to .env" -ForegroundColor Yellow
    Write-Host "  Deploy: .\scripts\verify_ssm_api_env.ps1 then .\scripts\upload_env_to_ssm.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "PASS: API env settings OK" -ForegroundColor Green
exit 0
