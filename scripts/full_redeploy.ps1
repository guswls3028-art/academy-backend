# ==============================================================================
# API + worker redeploy: build(optional) -> ECR push -> API/worker deploy
# Requires: root or deploy access key, C:\key\*.pem (EC2 SSH), -GitRepoUrl when building
#
# Video 인코딩: AWS Batch 전용. EC2/ASG video worker 없음. academy-video-worker 이미지는 빌드 시 ECR 푸시만 (Batch Job Definition용).
#
# --- Default workflow (ASG workers) ---
# Full:  .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG
# Workers only (ECR 이미지는 빌드서버에서 이미 푸시한 뒤): -SkipBuild -WorkersViaASG
# No-cache build: add -NoCache
#
# --- DeployTarget: all | api | video | ai | messaging | workers ---
# video = Batch용 이미지 빌드/푸시만 (EC2/ASG 배포 없음)
# ==============================================================================

param(
    [string]$GitRepoUrl = "",                    # URL to clone on build instance (required unless -SkipBuild)
    [string]$KeyDir = "C:\key",
    [string]$SubnetId = "subnet-07a8427d3306ce910",
    [string]$SecurityGroupId = "sg-02692600fbf8e26f7",
    [string]$Region = "ap-northeast-2",
    [string]$BuildInstanceType = "t4g.medium",
    [string]$RoleName = "academy-ec2-role",
    [switch]$SkipBuild = $false,
    [switch]$WorkersViaASG = $false,             # if true, workers via ASG instance refresh only (no SSH to fixed EC2)
    [switch]$StartStoppedInstances = $true,
    [switch]$NoCache = $false,                   # if true, docker build with --no-cache (e.g. after config change)
    [ValidateSet("all", "api", "video", "ai", "messaging", "workers")]
    [string]$DeployTarget = "all"               # all=API+2 workers (ai,messaging); api|video|ai|messaging=that one; video=build/push only
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
. (Join-Path $ScriptRoot "_config_instance_keys.ps1")
$AsgInfra = Join-Path $RepoRoot "infra\worker_asg"

$AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0) { Write-Host "AWS identity check failed. Check login/permissions." -ForegroundColor Red; exit 1 }
$CallerArn = (aws sts get-caller-identity --query Arn --output text 2>&1)
Write-Host "`n[Deploy account] Account=$AccountId  ARN=$CallerArn" -ForegroundColor Cyan
Write-Host "  Run full/cache/no-cache deploy with this account only. Do not switch key mid-run or SSH/workers will fail." -ForegroundColor Gray
Write-Host "  Preflight: .\scripts\deploy_preflight.ps1" -ForegroundColor Gray
if (-not $SkipBuild) {
    Write-Host "  Build starts in 5s. Press Ctrl+C if wrong key." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
$ECR = "${AccountId}.dkr.ecr.${Region}.amazonaws.com"
$EC2_USER = "ec2-user"

# SSOT: _config_instance_keys.ps1. Video = Batch 전용 (EC2 배포 없음).
$INSTANCE_KEYS = $INSTANCE_KEY_FILES
$INSTANCE_ORDER = @("academy-api", "academy-messaging-worker", "academy-ai-worker-cpu")
$REMOTE_CMDS = @{
    "academy-api" = "sudo docker image prune -af && aws ecr get-login-password --region $Region | sudo docker login --username AWS --password-stdin $ECR && sudo docker pull ${ECR}/academy-api:latest && (sudo docker stop academy-api 2>/dev/null; sudo docker rm academy-api 2>/dev/null; true) && sudo docker run --pull always -d --name academy-api --restart unless-stopped --env-file .env -p 8000:8000 ${ECR}/academy-api:latest && sudo docker update --restart unless-stopped academy-api"
    "academy-messaging-worker" = "aws ecr get-login-password --region $Region | sudo docker login --username AWS --password-stdin $ECR && sudo docker pull ${ECR}/academy-messaging-worker:latest && (sudo docker stop academy-messaging-worker 2>/dev/null; sudo docker rm academy-messaging-worker 2>/dev/null; true) && sudo docker run -d --name academy-messaging-worker --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker ${ECR}/academy-messaging-worker:latest && sudo docker update --restart unless-stopped academy-messaging-worker"
    "academy-ai-worker-cpu" = "aws ecr get-login-password --region $Region | sudo docker login --username AWS --password-stdin $ECR && sudo docker pull ${ECR}/academy-ai-worker-cpu:latest && (sudo docker stop academy-ai-worker-cpu 2>/dev/null; sudo docker rm academy-ai-worker-cpu 2>/dev/null; true) && sudo docker run -d --name academy-ai-worker-cpu --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker ${ECR}/academy-ai-worker-cpu:latest && sudo docker update --restart unless-stopped academy-ai-worker-cpu"
}

function Get-Ec2PublicIps {
    $names = "academy-api,academy-ai-worker-cpu,academy-messaging-worker"
    # PublicIpAddress + Association.PublicIp (Elastic IP fallback), for each instance
    $json = aws ec2 describe-instances --region $Region `
        --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=$names" `
        --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], PublicIpAddress, NetworkInterfaces[0].Association.PublicIp]" `
        --output json 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $json) { return @{} }
    $rows = $json | ConvertFrom-Json
    $result = @{}
    foreach ($row in $rows) {
        $name = ($row[0] -replace "^\s+|\s+$", "")
        $pub = $row[1]
        $assoc = $row[2]
        $ip = if ($pub -and $pub -ne "None") { $pub } elseif ($assoc -and $assoc -ne "None") { $assoc } else { $null }
        if ($name -and $ip) {
            # 동일 이름 여러 인스턴스 시 IP 있는 것을 우선 (ASG 등)
            if (-not $result[$name] -or $result[$name] -eq "None") { $result[$name] = $ip }
        }
    }
    return $result
}

function Start-StoppedAcademyInstances {
    $nameFilter = "academy-api,academy-ai-worker-cpu,academy-messaging-worker"
    $raw = aws ec2 describe-instances --region $Region `
        --filters "Name=tag:Name,Values=$nameFilter" "Name=instance-state-name,Values=stopped" `
        --query "Reservations[].Instances[].InstanceId" --output text 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return }
    $ids = $raw.Trim() -split "\s+" | Where-Object { $_ }
    if ($ids.Count -eq 0) { return }
    Write-Host "[EC2] Starting stopped instances: $($ids -join ',')" -ForegroundColor Cyan
    aws ec2 start-instances --region $Region --instance-ids $ids 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { return }
    aws ec2 wait instance-running --region $Region --instance-ids $ids 2>&1 | Out-Null
    Start-Sleep -Seconds 15
    Write-Host "[EC2] Started." -ForegroundColor Green
}

function Deploy-One {
    param ([string]$Name, [string]$Ip, [string]$KeyFile, [string]$RemoteCmd)
    $keyPath = Join-Path $KeyDir $KeyFile
    if (-not (Test-Path $keyPath)) { Write-Host "[$Name] SKIP - Key not found: $keyPath" -ForegroundColor Yellow; return $false }
    if (-not $Ip) { Write-Host "[$Name] SKIP - No public IP" -ForegroundColor Yellow; return $false }
    Write-Host "[$Name] $Ip ..." -ForegroundColor Cyan
    $cmd = "ssh -o StrictHostKeyChecking=accept-new -i `"$keyPath`" ${EC2_USER}@${Ip} `"$RemoteCmd`""
    Invoke-Expression $cmd
    if ($LASTEXITCODE -eq 0) { Write-Host "[$Name] OK" -ForegroundColor Green; return $true }
    Write-Host "[$Name] FAIL (exit $LASTEXITCODE)" -ForegroundColor Red
    return $false
}

# ---------- 1) Build instance: reuse or create academy-build-arm64 -> build -> leave running ----------
$buildInstanceId = $null
if (-not $SkipBuild) {
    if (-not $GitRepoUrl) {
        Write-Host "-GitRepoUrl is required for build step (or use -SkipBuild for deploy only)." -ForegroundColor Red
        exit 1
    }
    Write-Host "`n=== 1/3 Build instance start & build/ECR push (cache reuse) ===`n" -ForegroundColor Cyan

    # find existing academy-build-arm64 (running or stopped)
    $existing = aws ec2 describe-instances --region $Region `
        --filters "Name=tag:Name,Values=academy-build-arm64" "Name=instance-state-name,Values=running,stopped" `
        --query "Reservations[].Instances[].[InstanceId,State.Name]" --output text 2>&1
    $existingId = $null
    $existingState = $null
    if ($existing -match "i-\S+\s+(running|stopped)") {
        $parts = $existing.Trim() -split "\s+", 2
        $existingId = $parts[0]
        $existingState = $parts[1]
    }

    if ($existingId) {
        $buildInstanceId = $existingId
        Write-Host "Using existing build instance: $buildInstanceId (state: $existingState)" -ForegroundColor Cyan
        if ($existingState -eq "stopped") {
            aws ec2 start-instances --instance-ids $buildInstanceId --region $Region 2>&1 | Out-Null
            Write-Host "Starting instance..." -ForegroundColor Gray
            aws ec2 wait instance-running --instance-ids $buildInstanceId --region $Region
            Start-Sleep -Seconds 20
        }
    } else {
        Write-Host "No existing build instance -> creating new one (On-Demand, will stop after build for cache reuse)" -ForegroundColor Gray
        $AmiId = (aws ec2 describe-images --region $Region --owners amazon `
            --filters "Name=name,Values=al2023-ami-*-kernel-6.1-arm64" "Name=state,Values=available" `
            --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)
        $userData = @"
#!/bin/bash
yum update -y
yum install -y docker git
systemctl start docker
usermod -aG docker ec2-user
mkdir -p /home/ec2-user/build
echo 'Build instance ready'
"@
        $userDataB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($userData))
        $runResult = aws ec2 run-instances --image-id $AmiId --instance-type $BuildInstanceType `
            --count 1 --subnet-id $SubnetId --security-group-ids $SecurityGroupId `
            --iam-instance-profile "Name=$RoleName" --user-data $userDataB64 `
            --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=academy-build-arm64}]" `
            --region $Region --output json 2>&1 | ConvertFrom-Json
        if (-not $runResult.Instances -or $runResult.Instances.Count -eq 0) {
            Write-Host "run-instances failed." -ForegroundColor Red
            exit 1
        }
        $buildInstanceId = $runResult.Instances[0].InstanceId
        Write-Host "Build instance: $buildInstanceId (waiting for running)..." -ForegroundColor Gray
        aws ec2 wait instance-running --instance-ids $buildInstanceId --region $Region
        Start-Sleep -Seconds 30
    }
    Write-Host "Waiting for SSM registration (max 3 min)..." -ForegroundColor Gray
    $ssmReady = $false
    for ($i = 0; $i -lt 18; $i++) {
        Start-Sleep -Seconds 10
        try {
            $info = aws ssm describe-instance-information --region $Region --filters "Key=InstanceIds,Values=$buildInstanceId" --query "InstanceInformationList[0].PingStatus" --output text 2>$null
        } catch {
            $info = $null
        }
        if ($info -eq "Online") { $ssmReady = $true; break }
    }
    if (-not $ssmReady) {
        Write-Host "Instance not Online in SSM. Check academy-ec2-role has SSM permissions. Build instance left as-is: $buildInstanceId" -ForegroundColor Yellow
        exit 1
    }
    Start-Sleep -Seconds 15
    # build: git pull then cache build (use -NoCache when config/deps change)
    $noCacheFlag = if ($NoCache) { "--no-cache" } else { "" }
    $buildScript = @"
set -e
export PATH=/usr/local/bin:/usr/bin:$PATH
# ✅ Git safe.directory 설정 (SSM Run Command에서도 작동하도록)
git config --global --add safe.directory /home/ec2-user/build/academy || true
git config --global --add safe.directory '*' || true
cd /home/ec2-user/build
if [ -d academy ]; then cd academy && git fetch && git reset --hard origin/main && git pull; else git clone '$GitRepoUrl' academy && cd academy; fi
cd /home/ec2-user/build/academy
echo "===== BUILD COMMIT SHA ====="
git rev-parse HEAD
git log -1 --oneline
echo "===== PY_COMPILE CHECK ====="
python -m py_compile apps/support/video/views/video_views.py
echo "===== BUILD START ====="
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR
docker build $noCacheFlag -f docker/Dockerfile.base -t academy-base:latest .
docker build $noCacheFlag -f docker/api/Dockerfile -t academy-api:latest .
docker build $noCacheFlag -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .
docker build $noCacheFlag -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
docker build $noCacheFlag -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest .
docker tag academy-api:latest $ECR/academy-api:latest
docker tag academy-messaging-worker:latest $ECR/academy-messaging-worker:latest
docker tag academy-video-worker:latest $ECR/academy-video-worker:latest
docker tag academy-ai-worker-cpu:latest $ECR/academy-ai-worker-cpu:latest
docker push $ECR/academy-api:latest
docker push $ECR/academy-messaging-worker:latest
docker push $ECR/academy-video-worker:latest
docker push $ECR/academy-ai-worker-cpu:latest
echo Pruning old images and build cache...
docker image prune -f
docker builder prune -f 2>/dev/null || true
echo BUILD_AND_PUSH_OK
"@
    # SSM on Linux runs the script with bash; CRLF causes "set -e" to be parsed as "set -" (invalid option)
    $buildScript = ($buildScript.Trim() -replace "`r`n", "`n" -replace "`r", "`n")
    # ✅ --parameters 직접 사용 (Windows --cli-input-json 문제 회피)
    # 멀티라인 스크립트를 배열로 변환
    $scriptLines = $buildScript -split "`n" | Where-Object { $_.Trim() -ne "" }
    $commandsArray = @()
    foreach ($line in $scriptLines) {
        $trimmed = $line.Trim()
        if ($trimmed) {
            $commandsArray += $trimmed
        }
    }
    # PowerShell 배열을 JSON 배열 문자열로 변환
    $commandsJson = $commandsArray | ConvertTo-Json -Compress
    # ✅ --parameters에 JSON 배열 직접 전달
    $cmdResult = aws ssm send-command --region $Region `
        --instance-ids $buildInstanceId `
        --document-name "AWS-RunShellScript" `
        --parameters "commands=$commandsJson" `
        --timeout-seconds 3600 `
        --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Send-Command failed with exit code: $LASTEXITCODE" -ForegroundColor Red
        Write-Host "Error output: $cmdResult" -ForegroundColor Red
        Write-Host "Build instance kept: $buildInstanceId" -ForegroundColor Yellow
        exit 1
    }
    $cmdId = ($cmdResult | ConvertFrom-Json).Command.CommandId
    if (-not $cmdId) {
        Write-Host "Send-Command failed: Could not extract CommandId" -ForegroundColor Red
        Write-Host "Output: $cmdResult" -ForegroundColor Red
        Write-Host "Build instance kept: $buildInstanceId" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "SSM Run Command started: $cmdId (waiting up to 30 min)..." -ForegroundColor Cyan
    $done = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 30
        $status = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $buildInstanceId --query "Status" --output text 2>&1
        if ($status -eq "Success") { $done = $true; break }
        if ($status -eq "Failed" -or $status -eq "Cancelled") {
            $detail = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $buildInstanceId --output text 2>&1
            Write-Host "Build command failed: $detail" -ForegroundColor Red
            exit 1
        }
        Write-Host "  ... $status ($($i*30)s)" -ForegroundColor Gray
    }
    if (-not $done) {
        Write-Host "Build timeout. Instance kept: $buildInstanceId" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Build and ECR push done. Build instance left running (stop manually if needed): $buildInstanceId" -ForegroundColor Green
    $buildInstanceId = $null
} else {
    Write-Host "`n=== 1/3 Build step skipped (-SkipBuild) ===`n" -ForegroundColor Cyan
}

# ---------- 2) API deploy (when DeployTarget is all or api) ----------
$deployApi = ($DeployTarget -eq "all" -or $DeployTarget -eq "api")
$deployWorkers = ($DeployTarget -eq "all" -or $DeployTarget -eq "workers" -or $DeployTarget -eq "video" -or $DeployTarget -eq "ai" -or $DeployTarget -eq "messaging")
if ($deployApi) {
    Write-Host "`n=== 2/3 API server deploy (EC2 SSH) ===`n" -ForegroundColor Cyan
}
if ($StartStoppedInstances -and -not $WorkersViaASG) {
    Start-StoppedAcademyInstances
}
$ips = Get-Ec2PublicIps
# ASG workers often have no public IP; only require IPs when we actually deploy via SSH (API or workers without -WorkersViaASG)
$needIps = $deployApi -or ($deployWorkers -and -not $WorkersViaASG)
if ($ips.Count -eq 0 -and $needIps) {
    Write-Host "No running academy instances found (need public IPs for API/worker SSH deploy)." -ForegroundColor Red
    exit 1
}
if ($deployApi) {
    $apiIp = $ips["academy-api"]
    if (-not $apiIp) {
        Write-Host "academy-api not found or has no public IP (Get-Ec2PublicIps excludes instances without PublicIpAddress)." -ForegroundColor Red
        Write-Host "  Check: aws ec2 describe-instances --region $Region --filters Name=tag:Name,Values=academy-api Name=instance-state-name,Values=running --query Reservations[].Instances[].[InstanceId,PublicIpAddress]" -ForegroundColor Gray
        exit 1
    }
    # .env 복사 (API 서버 --env-file .env 사용)
    $envPath = Join-Path $RepoRoot ".env"
    $apiKeyPath = Join-Path $KeyDir $INSTANCE_KEYS["academy-api"]
    if ((Test-Path $envPath) -and (Test-Path $apiKeyPath)) {
        Write-Host "[academy-api] Copying .env ..." -ForegroundColor Gray
        scp -o StrictHostKeyChecking=accept-new -i "$apiKeyPath" "$envPath" "${EC2_USER}@${apiIp}:/home/ec2-user/.env"
        if ($LASTEXITCODE -ne 0) { Write-Host "[academy-api] WARN: .env copy failed, deploy may use old .env" -ForegroundColor Yellow }
    }
    $apiOk = Deploy-One -Name "academy-api" -Ip $apiIp -KeyFile $INSTANCE_KEYS["academy-api"] -RemoteCmd $REMOTE_CMDS["academy-api"]
    if (-not $apiOk) { exit 1 }

    # B2: Batch settings runtime verify
    $batchCheckScript = Join-Path $ScriptRoot "check_api_batch_runtime.ps1"
    if (Test-Path $batchCheckScript) {
        Write-Host "[academy-api] Verifying Batch settings in container..." -ForegroundColor Gray
        & $batchCheckScript -ApiIp $apiIp -KeyPath (Join-Path $KeyDir $INSTANCE_KEYS["academy-api"])
        if ($LASTEXITCODE -ne 0) {
            Write-Host "FAIL: Batch settings missing in API runtime. Deployment aborted." -ForegroundColor Red
            exit 1
        }
    }

    # B3: nginx X-Internal-Key 전달 (Lambda backlog-count 인증용)
    $nginxConfPath = Join-Path $RepoRoot "infra\nginx\academy-api.conf"
    if (Test-Path $nginxConfPath) {
        Write-Host "[academy-api] Copying nginx config (X-Internal-Key passthrough) ..." -ForegroundColor Gray
        scp -o StrictHostKeyChecking=accept-new -i "$apiKeyPath" "$nginxConfPath" "${EC2_USER}@${apiIp}:/tmp/academy-api.conf"
        if ($LASTEXITCODE -eq 0) {
            $nginxCmd = "sudo cp /tmp/academy-api.conf /etc/nginx/conf.d/academy-api.conf && sudo nginx -t && sudo systemctl reload nginx 2>/dev/null || sudo service nginx reload 2>/dev/null || true"
            ssh -o StrictHostKeyChecking=accept-new -i "$apiKeyPath" "${EC2_USER}@${apiIp}" $nginxCmd
            if ($LASTEXITCODE -eq 0) { Write-Host "[academy-api] nginx config applied and reloaded" -ForegroundColor Green }
            else { Write-Host "[academy-api] WARN: nginx reload may have failed (host nginx path may differ)" -ForegroundColor Yellow }
        } else { Write-Host "[academy-api] WARN: nginx config copy failed" -ForegroundColor Yellow }
    }
}

# ---------- 3) Worker deploy (when DeployTarget is all|workers|video|ai|messaging) ----------
if ($deployWorkers) {
    Write-Host "`n=== 3/3 Worker deploy ===`n" -ForegroundColor Cyan
    $workerList = @("academy-messaging-worker", "academy-ai-worker-cpu")
    if ($DeployTarget -eq "video") {
        Write-Host "Video: Batch 전용. EC2/ASG 배포 없음 (이미지는 Build 단계에서 ECR 푸시됨)." -ForegroundColor Gray
        $workerList = @()
    }
    if ($DeployTarget -eq "ai")    { $workerList = @("academy-ai-worker-cpu") }
    if ($DeployTarget -eq "messaging") { $workerList = @("academy-messaging-worker") }

    if ($workerList.Count -eq 0 -and $DeployTarget -ne "video") {
        Write-Host "Worker deploy 대상 없음." -ForegroundColor Gray
    } elseif ($workerList.Count -eq 0 -and $DeployTarget -eq "video") {
        # video = build/push only, already handled in Build step
    } elseif ($WorkersViaASG) {
        Write-Host "Worker ASG instance refresh only (skipping fixed EC2 SSH)..." -ForegroundColor Gray
        $asgMap = @{
            "academy-ai-worker-cpu"    = "academy-ai-worker-asg"
            "academy-messaging-worker" = "academy-messaging-worker-asg"
        }
        $asgNames = $workerList | ForEach-Object { $asgMap[$_] } | Where-Object { $_ }
        foreach ($asgName in $asgNames) {
            $asgCheck = aws autoscaling describe-auto-scaling-groups --region $Region --auto-scaling-group-names $asgName --query "AutoScalingGroups[0].AutoScalingGroupName" --output text 2>&1
            if ($LASTEXITCODE -ne 0 -or -not $asgCheck -or $asgCheck -eq "None") {
                Write-Host "  $asgName - ASG not found or error: $asgCheck" -ForegroundColor Yellow
                continue
            }
            
            # ✅ 진행 중인 Instance Refresh 확인
            $inProgress = aws autoscaling describe-instance-refreshes --region $Region --auto-scaling-group-name $asgName --query "InstanceRefreshes[?Status=='InProgress'].[InstanceRefreshId,Status]" --output json 2>&1
            if ($LASTEXITCODE -eq 0 -and $inProgress -and $inProgress -ne "[]" -and $inProgress -ne "null") {
                Write-Host "  $asgName instance refresh already in progress (skipping)" -ForegroundColor Yellow
                continue
            }
            
            # cmd /c prevents PowerShell from treating aws stderr as terminating error
            $refreshOut = cmd /c "aws autoscaling start-instance-refresh --region $Region --auto-scaling-group-name $asgName 2>&1"
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  $asgName instance refresh started" -ForegroundColor Green
            } else {
                Write-Host "  $asgName instance refresh FAILED: $refreshOut" -ForegroundColor Red
            }
        }
    } else {
        $envPath = Join-Path $RepoRoot ".env"
        $ok = 0
        foreach ($name in $workerList) {
            $ip = $ips[$name]
            if (-not $ip) { Write-Host "[$name] SKIP - No public IP" -ForegroundColor Yellow; continue }
            $keyPath = Join-Path $KeyDir $INSTANCE_KEYS[$name]
            if ((Test-Path $envPath) -and (Test-Path $keyPath)) {
                Write-Host "[$name] Copying .env ..." -ForegroundColor Gray
                scp -o StrictHostKeyChecking=accept-new -i "$keyPath" "$envPath" "${EC2_USER}@${ip}:/home/ec2-user/.env"
                if ($LASTEXITCODE -ne 0) { Write-Host "[$name] WARN: .env copy failed" -ForegroundColor Yellow }
            }
            if (Deploy-One -Name $name -Ip $ip -KeyFile $INSTANCE_KEYS[$name] -RemoteCmd $REMOTE_CMDS[$name]) { $ok++ }
        }
        Write-Host "Worker deploy: $ok/$($workerList.Count) succeeded" -ForegroundColor $(if ($ok -eq $workerList.Count) { "Green" } else { "Yellow" })
    }
}

Write-Host "`n=== Redeploy done (Target: $DeployTarget) ===`n" -ForegroundColor Green
