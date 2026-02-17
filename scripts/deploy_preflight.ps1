# ==============================================================================
# Deploy preflight: AWS account, keys, instances, ASG, ECR, SSM, (optional) SSH
# Usage: .\scripts\deploy_preflight.ps1   or  .\scripts\deploy_preflight.ps1 -TestSsh
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2",
    [switch]$TestSsh = $false   # if true, actually try SSH to academy-api (slower)
)

$ErrorActionPreference = "Stop"
$failed = $false

Write-Host "`n=== Deploy preflight (strict) ===`n" -ForegroundColor Cyan

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

# 2) SSH keys (API + 3 workers)
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

# 3) ECR access (avoid push failure during build)
$ecrTest = aws ecr describe-repositories --region $Region --repository-names academy-api 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] ECR describe academy-api: no permission or wrong account?" -ForegroundColor Red
    Write-Host "  $ecrTest" -ForegroundColor Gray
    $failed = $true
} else {
    Write-Host "[OK] ECR access (academy-api repo)" -ForegroundColor Green
}

# 4) SSM /academy/workers/env (워커·API 배포 시 필요)
$ssmTest = aws ssm get-parameter --name /academy/workers/env --region $Region --query "Parameter.Name" --output text 2>&1
if ($LASTEXITCODE -ne 0 -or -not $ssmTest) {
    Write-Host "[FAIL] SSM /academy/workers/env missing or no permission" -ForegroundColor Red
    $failed = $true
} else {
    Write-Host "[OK] SSM /academy/workers/env" -ForegroundColor Green
}

# 5) 빌드 인스턴스 (풀배포 시 사용 — 없으면 새로 만드는데 그때 권한/AMI 이슈 나올 수 있음)
$buildRaw = aws ec2 describe-instances --region $Region `
    --filters "Name=tag:Name,Values=academy-build-arm64" "Name=instance-state-name,Values=running,stopped" `
    --query "Reservations[].Instances[].[InstanceId,State.Name]" --output text 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] EC2 describe (build instance): $buildRaw" -ForegroundColor Yellow
} elseif (-not $buildRaw -or $buildRaw.Trim() -eq "") {
    Write-Host "[WARN] No academy-build-arm64 (running/stopped). Full deploy will CREATE new instance (needs EC2 run-instances + SSM)." -ForegroundColor Yellow
} else {
    $buildParts = $buildRaw.Trim() -split "\s+", 2
    Write-Host "[OK] Build instance: $($buildParts[0]) ($($buildParts[1]))" -ForegroundColor Green
}

# 6) 실행 중인 academy 인스턴스 (API/워커 SSH용)
$names = "academy-api,academy-ai-worker-cpu,academy-messaging-worker,academy-video-worker"
$raw = aws ec2 describe-instances --region $Region `
    --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=$names" `
    --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], InstanceId, PublicIpAddress]" `
    --output text 2>&1
$ips = @{}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] EC2 describe-instances: wrong account or no permission?" -ForegroundColor Red
    $failed = $true
} elseif (-not $raw -or $raw.Trim() -eq "") {
    Write-Host "[WARN] No running academy instances. full_redeploy will start stopped ones (StartStoppedInstances)." -ForegroundColor Yellow
} else {
    Write-Host "[OK] Running instances:" -ForegroundColor Green
    foreach ($line in ($raw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        $p = $line -split "\s+", 3
        if ($p.Length -ge 3) { $ips[$p[0]] = $p[2]; Write-Host "     $($p[0]) $($p[1]) $($p[2])" -ForegroundColor Gray }
    }
}

# 7) ASG (WorkersViaASG 사용 시)
$asgNames = @("academy-video-worker-asg", "academy-ai-worker-asg", "academy-messaging-worker-asg")
$asgOut = aws autoscaling describe-auto-scaling-groups --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] ASG describe failed (skip if not using -WorkersViaASG)" -ForegroundColor Yellow
} else {
    $asgJson = $asgOut | ConvertFrom-Json
    $found = $asgJson.AutoScalingGroups | Where-Object { $asgNames -contains $_.AutoScalingGroupName }
    if ($found.Count -ge 1) {
        Write-Host "[OK] ASG: $($found.Count)/$($asgNames.Count)" -ForegroundColor Green
        $found | ForEach-Object { Write-Host "     $($_.AutoScalingGroupName) Desired=$($_.DesiredCapacity)" -ForegroundColor Gray }
    } else {
        Write-Host "[WARN] No academy ASGs (use -WorkersViaASG only after ASG deploy)" -ForegroundColor Yellow
    }
}

# 8) (선택) SSH 실제 접속 테스트 — 키/보안그룹/네트워크 검증
if ($TestSsh -and $ips["academy-api"]) {
    $apiKeyPath = Join-Path $KeyDir "backend-api-key.pem"
    if (Test-Path $apiKeyPath) {
        Write-Host "`n[SSH] Testing academy-api ($($ips['academy-api']))..." -ForegroundColor Cyan
        $sshTest = ssh -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new -i "`"$apiKeyPath`"`" ec2-user@$($ips["academy-api"]) "exit" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] SSH to academy-api works" -ForegroundColor Green
        } else {
            Write-Host "[FAIL] SSH to academy-api failed (key/SG/network?). Output: $sshTest" -ForegroundColor Red
            $failed = $true
        }
    }
}

Write-Host ""
if ($failed) {
    Write-Host "Preflight FAILED. Fix above before full_redeploy." -ForegroundColor Red
    Write-Host "  Optional: run with -TestSsh to verify SSH: .\scripts\deploy_preflight.ps1 -TestSsh" -ForegroundColor Gray
    exit 1
}
Write-Host "Preflight OK. Use SAME terminal/env: .\scripts\full_redeploy.ps1 -GitRepoUrl ... -WorkersViaASG" -ForegroundColor Green
Write-Host "  (Do not switch to admin97 or other key before deploy.)" -ForegroundColor Gray
Write-Host ""
