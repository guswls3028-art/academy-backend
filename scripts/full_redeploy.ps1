# ==============================================================================
# API + 워커 재배포: 빌드(선택) → ECR 푸시 → API/워커 배포
# 전제: 루트 또는 배포 권한 액세스 키, C:\key\*.pem (EC2 SSH용), 빌드 시 -GitRepoUrl
#
# DeployTarget: all(기본) | api | video | ai | messaging | workers
#
# --- Git 푸시 후 한 방 재배포 (6종) ---
# 1) API만:     cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget api
# 2) Video만:   cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget video
# 3) AI만:      cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget ai
# 4) Messaging만: cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget messaging
# 5) 전부(API+3워커): cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git"
# 6) 워커만(3종): cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget workers
#
# 빌드 생략(ECR 이미지 그대로 배포만): 위 명령에 -SkipBuild 추가
# 워커 ASG 리프레시만: -WorkersViaASG
# ==============================================================================

param(
    [string]$GitRepoUrl = "",                    # 빌드 인스턴스에서 clone 할 URL (SkipBuild 아니면 권장 지정)
    [string]$KeyDir = "C:\key",
    [string]$SubnetId = "subnet-07a8427d3306ce910",
    [string]$SecurityGroupId = "sg-02692600fbf8e26f7",
    [string]$Region = "ap-northeast-2",
    [string]$BuildInstanceType = "t4g.medium",
    [string]$RoleName = "academy-ec2-role",
    [switch]$SkipBuild = $false,
    [switch]$WorkersViaASG = $false,             # true면 워커는 ASG 인스턴스 리프레시만, 고정 EC2 3대 SSH 안 함
    [switch]$StartStoppedInstances = $true,
    [ValidateSet("all", "api", "video", "ai", "messaging", "workers")]
    [string]$DeployTarget = "all"               # all=API+3워커, api/video/ai/messaging=해당 1종만, workers=워커 3종만
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$AsgInfra = Join-Path $RepoRoot "infra\worker_asg"

$AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0) { Write-Host "AWS identity check failed. Check login/permissions." -ForegroundColor Red; exit 1 }
$ECR = "${AccountId}.dkr.ecr.${Region}.amazonaws.com"
$EC2_USER = "ec2-user"

# deploy.ps1 과 동일
$INSTANCE_KEYS = @{
    "academy-api"                = "backend-api-key.pem"
    "academy-messaging-worker"   = "message-key.pem"
    "academy-ai-worker-cpu"      = "ai-worker-key.pem"
    "academy-video-worker"       = "video-worker-key.pem"
}
$INSTANCE_ORDER = @("academy-api", "academy-messaging-worker", "academy-ai-worker-cpu", "academy-video-worker")
$REMOTE_CMDS = @{
    "academy-api" = "aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR && docker pull ${ECR}/academy-api:latest && (docker stop academy-api 2>/dev/null; docker rm academy-api 2>/dev/null; true) && docker run -d --name academy-api --restart unless-stopped --env-file .env -p 8000:8000 ${ECR}/academy-api:latest && docker update --restart unless-stopped academy-api"
    "academy-messaging-worker" = "aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR && docker pull ${ECR}/academy-messaging-worker:latest && (docker stop academy-messaging-worker 2>/dev/null; docker rm academy-messaging-worker 2>/dev/null; true) && docker run -d --name academy-messaging-worker --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker ${ECR}/academy-messaging-worker:latest && docker update --restart unless-stopped academy-messaging-worker"
    "academy-ai-worker-cpu" = "aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR && docker pull ${ECR}/academy-ai-worker-cpu:latest && (docker stop academy-ai-worker-cpu 2>/dev/null; docker rm academy-ai-worker-cpu 2>/dev/null; true) && docker run -d --name academy-ai-worker-cpu --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 ${ECR}/academy-ai-worker-cpu:latest && docker update --restart unless-stopped academy-ai-worker-cpu"
    "academy-video-worker" = "aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR && docker pull ${ECR}/academy-video-worker:latest && (docker stop academy-video-worker 2>/dev/null; docker rm academy-video-worker 2>/dev/null; true) && docker run -d --name academy-video-worker --restart unless-stopped --memory 4g --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 -v /mnt/transcode:/tmp ${ECR}/academy-video-worker:latest && docker update --restart unless-stopped academy-video-worker"
}

function Get-Ec2PublicIps {
    $names = "academy-api,academy-ai-worker-cpu,academy-messaging-worker,academy-video-worker"
    $raw = aws ec2 describe-instances --region $Region `
        --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=$names" `
        --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], PublicIpAddress]" `
        --output text 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return @{} }
    $result = @{}
    foreach ($line in ($raw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        $p = $line -split "\s+", 2
        if ($p.Length -ge 2 -and $p[1] -and $p[1] -ne "None") { $result[$p[0].Trim()] = $p[1].Trim() }
    }
    return $result
}

function Start-StoppedAcademyInstances {
    $nameFilter = "academy-api,academy-ai-worker-cpu,academy-messaging-worker,academy-video-worker"
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

# ---------- 1) 빌드 인스턴스: 기존 academy-build-arm64 재사용 또는 새로 생성 → 빌드 → 중지(캐시 유지) ----------
$buildInstanceId = $null
if (-not $SkipBuild) {
    if (-not $GitRepoUrl) {
        Write-Host "-GitRepoUrl is required for build step (or use -SkipBuild for deploy only)." -ForegroundColor Red
        exit 1
    }
    Write-Host "`n=== 1/3 Build instance start & build/ECR push (cache reuse) ===`n" -ForegroundColor Cyan

    # 기존 academy-build-arm64 인스턴스 찾기 (running 또는 stopped)
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
        $info = aws ssm describe-instance-information --region $Region --filters "Key=InstanceIds,Values=$buildInstanceId" --query "InstanceInformationList[0].PingStatus" --output text 2>&1
        if ($info -eq "Online") { $ssmReady = $true; break }
    }
    if (-not $ssmReady) {
        Write-Host "Instance not Online in SSM. Check academy-ec2-role has SSM permissions. Build instance left as-is: $buildInstanceId" -ForegroundColor Yellow
        exit 1
    }
    Start-Sleep -Seconds 15
    # 캐시 재사용: /home/ec2-user/build/academy 에서 git pull 후 빌드 (재사용 시 빠름)
    $buildScript = @"
set -e
export PATH=/usr/local/bin:/usr/bin:$PATH
cd /home/ec2-user/build
if [ -d academy ]; then cd academy && git pull; else git clone '$GitRepoUrl' academy && cd academy; fi
cd /home/ec2-user/build/academy
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR
docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/api/Dockerfile -t academy-api:latest .
docker build -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
docker build -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest .
docker tag academy-api:latest $ECR/academy-api:latest
docker tag academy-messaging-worker:latest $ECR/academy-messaging-worker:latest
docker tag academy-video-worker:latest $ECR/academy-video-worker:latest
docker tag academy-ai-worker-cpu:latest $ECR/academy-ai-worker-cpu:latest
docker push $ECR/academy-api:latest
docker push $ECR/academy-messaging-worker:latest
docker push $ECR/academy-video-worker:latest
docker push $ECR/academy-ai-worker-cpu:latest
echo BUILD_AND_PUSH_OK
"@
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    $paramsJson = @{ commands = @($buildScript.Trim()) } | ConvertTo-Json -Depth 10 -Compress
    $paramsFile = Join-Path $RepoRoot "ssm_build_params.json"
    [System.IO.File]::WriteAllText($paramsFile, $paramsJson, $utf8NoBom)
    $paramsUri = "file://$($paramsFile -replace '\\','/' -replace ' ', '%20')"
    $cmdId = aws ssm send-command --region $Region --instance-ids $buildInstanceId `
        --document-name "AWS-RunShellScript" `
        --parameters $paramsUri `
        --timeout-seconds 3600 `
        --output text --query "Command.CommandId" 2>&1
    Remove-Item $paramsFile -Force -ErrorAction SilentlyContinue
    if (-not $cmdId -or $cmdId -match "error|Error") {
        Write-Host "Send-Command 실패: $cmdId" -ForegroundColor Red
        Write-Host "빌드 인스턴스 유지: $buildInstanceId" -ForegroundColor Yellow
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
    Write-Host "Build and ECR push done. Stopping build instance (cache reuse next time)..." -ForegroundColor Green
    aws ec2 stop-instances --instance-ids $buildInstanceId --region $Region 2>&1 | Out-Null
    $buildInstanceId = $null
} else {
    Write-Host "`n=== 1/3 Build step skipped (-SkipBuild) ===`n" -ForegroundColor Cyan
}

# ---------- 2) API 배포 (DeployTarget이 all 또는 api 일 때만) ----------
$deployApi = ($DeployTarget -eq "all" -or $DeployTarget -eq "api")
if ($deployApi) {
    Write-Host "`n=== 2/3 API server deploy (EC2 SSH) ===`n" -ForegroundColor Cyan
}
if ($StartStoppedInstances) { Start-StoppedAcademyInstances }
$ips = Get-Ec2PublicIps
if ($ips.Count -eq 0) {
    Write-Host "No running academy instances found." -ForegroundColor Red
    exit 1
}
if ($deployApi) {
    $apiIp = $ips["academy-api"]
    if (-not $apiIp) {
        Write-Host "academy-api instance not found." -ForegroundColor Red
        exit 1
    }
    $apiOk = Deploy-One -Name "academy-api" -Ip $apiIp -KeyFile $INSTANCE_KEYS["academy-api"] -RemoteCmd $REMOTE_CMDS["academy-api"]
    if (-not $apiOk) { exit 1 }
}

# ---------- 3) 워커 배포 (DeployTarget이 all / workers / video / ai / messaging 일 때) ----------
$deployWorkers = ($DeployTarget -eq "all" -or $DeployTarget -eq "workers" -or $DeployTarget -eq "video" -or $DeployTarget -eq "ai" -or $DeployTarget -eq "messaging")
if ($deployWorkers) {
    Write-Host "`n=== 3/3 Worker deploy ===`n" -ForegroundColor Cyan
    $workerList = @("academy-messaging-worker", "academy-ai-worker-cpu", "academy-video-worker")
    if ($DeployTarget -eq "video") { $workerList = @("academy-video-worker") }
    if ($DeployTarget -eq "ai")    { $workerList = @("academy-ai-worker-cpu") }
    if ($DeployTarget -eq "messaging") { $workerList = @("academy-messaging-worker") }

    if ($WorkersViaASG) {
        Write-Host "Worker ASG instance refresh only (skipping fixed EC2 SSH)..." -ForegroundColor Gray
        $asgMap = @{
            "academy-video-worker"     = "academy-video-worker-asg"
            "academy-ai-worker-cpu"    = "academy-ai-worker-asg"
            "academy-messaging-worker" = "academy-messaging-worker-asg"
        }
        $asgNames = $workerList | ForEach-Object { $asgMap[$_] } | Where-Object { $_ }
        foreach ($asgName in $asgNames) {
            $asg = aws autoscaling describe-auto-scaling-groups --region $Region --auto-scaling-group-names $asgName --query "AutoScalingGroups[0].AutoScalingGroupName" --output text 2>&1
            if ($asg -and $asg -ne "None") {
                aws autoscaling start-instance-refresh --region $Region --auto-scaling-group-name $asgName 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { Write-Host "  $asgName instance refresh started" -ForegroundColor Green }
            }
        }
    } else {
        $ok = 0
        foreach ($name in $workerList) {
            $ip = $ips[$name]
            if (Deploy-One -Name $name -Ip $ip -KeyFile $INSTANCE_KEYS[$name] -RemoteCmd $REMOTE_CMDS[$name]) { $ok++ }
        }
        Write-Host "Worker deploy: $ok/$($workerList.Count) succeeded" -ForegroundColor $(if ($ok -eq $workerList.Count) { "Green" } else { "Yellow" })
    }
}

Write-Host "`n=== Redeploy done (Target: $DeployTarget) ===`n" -ForegroundColor Green
