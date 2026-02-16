# ==============================================================================
# API + 워커 전부 재배포 (한 방): 빌드 인스턴스 생성 → 빌드/ECR 푸시 → 인스턴스 종료 → API/워커 배포
# 전제: 루트 또는 배포 권한 있는 액세스 키, C:\key\*.pem (API/워커 EC2 SSH용)
#       빌드 인스턴스용 IAM 역할(academy-ec2-role)에 SSM + ECR push 권한 필요
#
# 한 방 실행 (Git 레포 URL 필수):
#   cd C:\academy
#   .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git"
#
# 빌드 생략하고 배포만 (이미 ECR에 최신 이미지 있을 때):
#   .\scripts\full_redeploy.ps1 -SkipBuild
#
# 워커는 ASG로만 배포 (고정 EC2 3대 SSH 배포 생략, ASG 인스턴스 리프레시만):
#   .\scripts\full_redeploy.ps1 -GitRepoUrl "..." -WorkersViaASG
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
if ($LASTEXITCODE -ne 0) { Write-Host "AWS identity 확인 실패. 로그인/권한 확인." -ForegroundColor Red; exit 1 }
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
    Write-Host "[EC2] 중지된 인스턴스 기동: $($ids -join ',')" -ForegroundColor Cyan
    aws ec2 start-instances --region $Region --instance-ids $ids 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { return }
    aws ec2 wait instance-running --region $Region --instance-ids $ids 2>&1 | Out-Null
    Start-Sleep -Seconds 15
    Write-Host "[EC2] 기동 완료." -ForegroundColor Green
}

function Deploy-One {
    param ([string]$Name, [string]$Ip, [string]$KeyFile, [string]$RemoteCmd)
    $keyPath = Join-Path $KeyDir $KeyFile
    if (-not (Test-Path $keyPath)) { Write-Host "[$Name] SKIP - 키 없음: $keyPath" -ForegroundColor Yellow; return $false }
    if (-not $Ip) { Write-Host "[$Name] SKIP - Public IP 없음" -ForegroundColor Yellow; return $false }
    Write-Host "[$Name] $Ip ..." -ForegroundColor Cyan
    $cmd = "ssh -o StrictHostKeyChecking=accept-new -i `"$keyPath`" ${EC2_USER}@${Ip} `"$RemoteCmd`""
    Invoke-Expression $cmd
    if ($LASTEXITCODE -eq 0) { Write-Host "[$Name] OK" -ForegroundColor Green; return $true }
    Write-Host "[$Name] FAIL (exit $LASTEXITCODE)" -ForegroundColor Red
    return $false
}

# ---------- 1) 빌드 인스턴스 기동 + 빌드/푸시 + 종료 ----------
$buildInstanceId = $null
if (-not $SkipBuild) {
    if (-not $GitRepoUrl) {
        Write-Host "빌드 단계에서는 -GitRepoUrl 이 필요합니다. (또는 -SkipBuild 로 배포만)" -ForegroundColor Red
        exit 1
    }
    Write-Host "`n=== 1/3 빌드 인스턴스 기동 & 빌드/ECR 푸시 ===`n" -ForegroundColor Cyan
    $AmiId = (aws ec2 describe-images --region $Region --owners amazon `
        --filters "Name=name,Values=al2023-ami-*-kernel-6.1-arm64" "Name=state,Values=available" `
        --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)
    $userData = @"
#!/bin/bash
yum update -y
yum install -y docker git
systemctl start docker
usermod -aG docker ec2-user
echo 'Build instance ready'
"@
    $userDataB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($userData))
    $spotFile = Join-Path $RepoRoot "spot_options.json"
    '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time","MaxPrice":"0.05"}}' | Set-Content $spotFile -Encoding ASCII -NoNewline
    $spotUri = "file://$($spotFile -replace '\\','/' -replace ' ', '%20')"
    $runResult = aws ec2 run-instances --image-id $AmiId --instance-type $BuildInstanceType `
        --count 1 --subnet-id $SubnetId --security-group-ids $SecurityGroupId `
        --iam-instance-profile "Name=$RoleName" --user-data $userDataB64 `
        --instance-market-options $spotUri `
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=academy-build-arm64}]" `
        --region $Region --output json 2>&1 | ConvertFrom-Json
    Remove-Item $spotFile -Force -ErrorAction SilentlyContinue
    if (-not $runResult.Instances -or $runResult.Instances.Count -eq 0) {
        Write-Host "run-instances 실패." -ForegroundColor Red
        exit 1
    }
    $buildInstanceId = $runResult.Instances[0].InstanceId
    Write-Host "빌드 인스턴스: $buildInstanceId (running 대기 중)..." -ForegroundColor Gray
    aws ec2 wait instance-running --instance-ids $buildInstanceId --region $Region
    Write-Host "SSM 등록 대기 (최대 3분)..." -ForegroundColor Gray
    $ssmReady = $false
    for ($i = 0; $i -lt 18; $i++) {
        Start-Sleep -Seconds 10
        $info = aws ssm describe-instance-information --region $Region --filters "Key=InstanceIds,Values=$buildInstanceId" --query "InstanceInformationList[0].PingStatus" --output text 2>&1
        if ($info -eq "Online") { $ssmReady = $true; break }
    }
    if (-not $ssmReady) {
        Write-Host "SSM 에서 인스턴스가 Online 이 아닙니다. academy-ec2-role 에 SSM 권한이 있는지 확인하세요. 빌드 인스턴스는 종료하지 않고 유지합니다: $buildInstanceId" -ForegroundColor Yellow
        Write-Host "수동으로 SSM 연결 후 빌드하거나, 아래로 수동 빌드 후 이 스크립트를 -SkipBuild 로 다시 실행하세요." -ForegroundColor Yellow
        exit 1
    }
    # Docker/유저데이터 적용 여유
    Start-Sleep -Seconds 60
    $buildScript = @"
set -e
cd /tmp
rm -rf academy
git clone '$GitRepoUrl' academy
cd academy
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
    Write-Host "SSM Run Command 시작: $cmdId (완료까지 대기, 최대 30분)..." -ForegroundColor Cyan
    $done = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 30
        $status = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $buildInstanceId --query "Status" --output text 2>&1
        if ($status -eq "Success") { $done = $true; break }
        if ($status -eq "Failed" -or $status -eq "Cancelled") {
            $detail = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $buildInstanceId --output text 2>&1
            Write-Host "빌드 명령 실패: $detail" -ForegroundColor Red
            exit 1
        }
        Write-Host "  ... $status ($($i*30)s)" -ForegroundColor Gray
    }
    if (-not $done) {
        Write-Host "빌드 타임아웃. 인스턴스는 유지: $buildInstanceId" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "빌드 및 ECR 푸시 완료. 빌드 인스턴스 종료 중..." -ForegroundColor Green
    aws ec2 terminate-instances --instance-ids $buildInstanceId --region $Region 2>&1 | Out-Null
    $buildInstanceId = $null
} else {
    Write-Host "`n=== 1/3 빌드 단계 생략 (-SkipBuild) ===`n" -ForegroundColor Cyan
}

# ---------- 2) API + 워커 배포 (고정 EC2 SSH) ----------
Write-Host "`n=== 2/3 API 서버 배포 (EC2 SSH) ===`n" -ForegroundColor Cyan
if ($StartStoppedInstances) { Start-StoppedAcademyInstances }
$ips = Get-Ec2PublicIps
if ($ips.Count -eq 0) {
    Write-Host "실행 중인 academy 인스턴스가 없습니다." -ForegroundColor Red
    exit 1
}
$apiIp = $ips["academy-api"]
if (-not $apiIp) {
    Write-Host "academy-api 인스턴스를 찾을 수 없습니다." -ForegroundColor Red
    exit 1
}
$apiOk = Deploy-One -Name "academy-api" -Ip $apiIp -KeyFile $INSTANCE_KEYS["academy-api"] -RemoteCmd $REMOTE_CMDS["academy-api"]
if (-not $apiOk) { exit 1 }

# ---------- 3) 워커: 고정 EC2 3대 SSH 또는 ASG 리프레시 ----------
Write-Host "`n=== 3/3 워커 배포 ===`n" -ForegroundColor Cyan
if ($WorkersViaASG) {
    Write-Host "워커 ASG 인스턴스 리프레시만 수행 (고정 EC2 SSH 생략)..." -ForegroundColor Gray
    $asgNames = @("academy-ai-worker-asg", "academy-messaging-worker-asg", "academy-video-worker-asg")
    foreach ($asgName in $asgNames) {
        $asg = aws autoscaling describe-auto-scaling-groups --region $Region --auto-scaling-group-names $asgName --query "AutoScalingGroups[0].AutoScalingGroupName" --output text 2>&1
        if ($asg -and $asg -ne "None") {
            aws autoscaling start-instance-refresh --region $Region --auto-scaling-group-name $asgName 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Host "  $asgName instance refresh 시작" -ForegroundColor Green }
        }
    }
} else {
    $ok = 0
    foreach ($name in @("academy-messaging-worker", "academy-ai-worker-cpu", "academy-video-worker")) {
        $ip = $ips[$name]
        if (Deploy-One -Name $name -Ip $ip -KeyFile $INSTANCE_KEYS[$name] -RemoteCmd $REMOTE_CMDS[$name]) { $ok++ }
    }
    Write-Host "워커 배포: $ok/3 성공" -ForegroundColor $(if ($ok -eq 3) { "Green" } else { "Yellow" })
}

Write-Host "`n=== Full Redeploy 완료 ===`n" -ForegroundColor Green
