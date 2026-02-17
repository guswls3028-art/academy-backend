# ==============================================================================
# 배포 전 검증: AWS 계정·키·인스턴스·ASG 확인 (full_redeploy 실행 전 권장)
# 사용: .\scripts\deploy_preflight.ps1
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$failed = $false

Write-Host "`n=== Deploy preflight ===`n" -ForegroundColor Cyan

# 1) AWS Identity
$identity = aws sts get-caller-identity --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] AWS identity: $identity" -ForegroundColor Red
    Write-Host "  -> Set env: `$env:AWS_ACCESS_KEY_ID, `$env:AWS_SECRET_ACCESS_KEY, `$env:AWS_DEFAULT_REGION" -ForegroundColor Yellow
    exit 1
}
$idJson = $identity | ConvertFrom-Json
$account = $idJson.Account
$arn = $idJson.Arn
Write-Host "[OK] Account: $account" -ForegroundColor Green
Write-Host "     ARN: $arn" -ForegroundColor Gray

# 2) SSH 키 (API + 워커 3종)
$requiredKeys = @(
    @{ Name = "academy-api"; Key = "backend-api-key.pem" },
    @{ Name = "academy-messaging-worker"; Key = "message-key.pem" },
    @{ Name = "academy-ai-worker-cpu"; Key = "ai-worker-key.pem" },
    @{ Name = "academy-video-worker"; Key = "video-worker-key.pem" }
)
foreach ($r in $requiredKeys) {
    $path = Join-Path $KeyDir $r.Key
    if (Test-Path $path) {
        Write-Host "[OK] Key $($r.Name): $path" -ForegroundColor Green
    } else {
        Write-Host "[MISS] Key $($r.Name): $path" -ForegroundColor Red
        $failed = $true
    }
}

# 3) 실행 중인 academy 인스턴스
$names = "academy-api,academy-ai-worker-cpu,academy-messaging-worker,academy-video-worker"
$raw = aws ec2 describe-instances --region $Region `
    --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=$names" `
    --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], InstanceId, PublicIpAddress]" `
    --output text 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] EC2 describe-instances: wrong account or no permission?" -ForegroundColor Red
    $failed = $true
} elseif (-not $raw -or $raw.Trim() -eq "") {
    Write-Host "[WARN] No running academy instances in this account/region" -ForegroundColor Yellow
} else {
    Write-Host "[OK] Running instances (this account):" -ForegroundColor Green
    foreach ($line in ($raw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        $p = $line -split "\s+", 3
        if ($p.Length -ge 3) { Write-Host "     $($p[0]) $($p[1]) $($p[2])" -ForegroundColor Gray }
    }
}

# 4) ASG (WorkersViaASG 사용 시)
$asgNames = @("academy-video-worker-asg", "academy-ai-worker-asg", "academy-messaging-worker-asg")
$asgOut = aws autoscaling describe-auto-scaling-groups --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] ASG describe failed (skip if not using -WorkersViaASG)" -ForegroundColor Yellow
} else {
    $asgJson = $asgOut | ConvertFrom-Json
    $found = $asgJson.AutoScalingGroups | Where-Object { $asgNames -contains $_.AutoScalingGroupName }
    if ($found.Count -ge 1) {
        Write-Host "[OK] ASG found: $($found.Count)/$($asgNames.Count)" -ForegroundColor Green
        $found | ForEach-Object { Write-Host "     $($_.AutoScalingGroupName) Desired=$($_.DesiredCapacity)" -ForegroundColor Gray }
    } else {
        Write-Host "[WARN] No academy ASGs in this account (use -WorkersViaASG only after ASG deploy)" -ForegroundColor Yellow
    }
}

Write-Host ""
if ($failed) {
    Write-Host "Preflight FAILED. Fix above before full_redeploy." -ForegroundColor Red
    exit 1
}
Write-Host "Preflight OK. Use same AWS env for: .\scripts\full_redeploy.ps1 -GitRepoUrl ... -WorkersViaASG" -ForegroundColor Green
Write-Host ""
