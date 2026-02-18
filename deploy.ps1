# ==============================================================================
# EC2 4대 원큐 배포: 로컬 빌드 → ECR 푸시 → 4대 배포
# 실행: 더블클릭 또는 .\deploy.ps1  (빌드+푸시+배포)
#       .\deploy.ps1 -SkipBuild  (이미 빌드됐을 때 푸시+배포만)
# ==============================================================================

param([switch]$SkipBuild, [switch]$StartInstances = $true)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$AWS_REGION = "ap-northeast-2"
$EC2_USER = "ec2-user"
$KEY_DIR = "C:\key"

# 인스턴스 이름 → SSH 키 파일 (실제 사용 중인 키 매핑)
$INSTANCE_KEYS = @{
    "academy-api"                = "backend-api-key.pem"
    "academy-messaging-worker"   = "message-key.pem"
    "academy-ai-worker-cpu"       = "ai-worker-key.pem"
    "academy-video-worker"       = "video-worker-key.pem"
}

# 배포 순서
$INSTANCE_ORDER = @(
    "academy-api",
    "academy-messaging-worker",
    "academy-ai-worker-cpu",
    "academy-video-worker"
)

$ECR = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com"

# 서버별 원격 명령 (문서 DEPLOY_COPYPASTE_WHILE_VIDEO_BUILDS.md 방식: ECR 로그인 → pull → 기존 컨테이너 제거 → run)
$REMOTE_CMDS = @{
    "academy-api" = "aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin $ECR && docker pull $ECR/academy-api:latest && (docker stop academy-api 2>/dev/null; docker rm academy-api 2>/dev/null; true) && docker run -d --name academy-api --restart unless-stopped --env-file .env -p 8000:8000 $ECR/academy-api:latest && docker update --restart unless-stopped academy-api"
    "academy-messaging-worker" = "aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin $ECR && docker pull $ECR/academy-messaging-worker:latest && (docker stop academy-messaging-worker 2>/dev/null; docker rm academy-messaging-worker 2>/dev/null; true) && docker run -d --name academy-messaging-worker --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker $ECR/academy-messaging-worker:latest && docker update --restart unless-stopped academy-messaging-worker"
    "academy-ai-worker-cpu" = "aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin $ECR && docker pull $ECR/academy-ai-worker-cpu:latest && (docker stop academy-ai-worker-cpu 2>/dev/null; docker rm academy-ai-worker-cpu 2>/dev/null; true) && docker run -d --name academy-ai-worker-cpu --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker $ECR/academy-ai-worker-cpu:latest && docker update --restart unless-stopped academy-ai-worker-cpu"
    "academy-video-worker" = "aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin $ECR && docker pull $ECR/academy-video-worker:latest && (docker stop academy-video-worker 2>/dev/null; docker rm academy-video-worker 2>/dev/null; true) && docker run -d --name academy-video-worker --restart unless-stopped --memory 4g --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 -v /mnt/transcode:/tmp $ECR/academy-video-worker:latest && docker update --restart unless-stopped academy-video-worker"
}

# ------------------------------------------------------------------------------
# 중지된 academy 인스턴스 일괄 기동 (워커는 유휴 시 자동 종료됨)
# ------------------------------------------------------------------------------
function Start-StoppedAcademyInstances {
    $nameFilter = "academy-api,academy-ai-worker-cpu,academy-messaging-worker,academy-video-worker"
    $raw = aws ec2 describe-instances --region $AWS_REGION `
        --filters "Name=tag:Name,Values=$nameFilter" "Name=instance-state-name,Values=stopped" `
        --query "Reservations[].Instances[].InstanceId" --output text 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return }
    $ids = $raw.Trim() -split "\s+" | Where-Object { $_ }
    if ($ids.Count -eq 0) { return }
    Write-Host "[EC2] 중지된 인스턴스 기동 중: $($ids.Count)대" -ForegroundColor Cyan
    aws ec2 start-instances --region $AWS_REGION --instance-ids $ids 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "[EC2] start-instances 실패" -ForegroundColor Red; return }
    Write-Host "[EC2] instance-running 대기 중..." -ForegroundColor Gray
    aws ec2 wait instance-running --region $AWS_REGION --instance-ids $ids 2>&1 | Out-Null
    Start-Sleep -Seconds 15
    Write-Host "[EC2] 기동 완료." -ForegroundColor Green
}

# ------------------------------------------------------------------------------
# Public IP 자동 조회 (AWS CLI)
# ------------------------------------------------------------------------------
function Get-Ec2PublicIps {
    $names = $INSTANCE_ORDER -join ","
    $raw = aws ec2 describe-instances --region $AWS_REGION `
        --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-api,academy-ai-worker-cpu,academy-messaging-worker,academy-video-worker" `
        --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], PublicIpAddress]" `
        --output text 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] AWS CLI 실패: $raw" -ForegroundColor Red
        return @{}
    }
    $lines = $raw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $result = @{}
    foreach ($line in $lines) {
        $parts = $line -split "\s+", 2
        if ($parts.Length -ge 2 -and $parts[1] -and $parts[1] -ne "None") {
            $result[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
    return $result
}

# ------------------------------------------------------------------------------
# 단일 서버 배포 (SSH → 명령 실행)
# ------------------------------------------------------------------------------
function Deploy-One {
    param ([string]$Name, [string]$Ip, [string]$KeyFile, [string]$RemoteCmd)
    $keyPath = Join-Path $KEY_DIR $KeyFile
    if (-not (Test-Path $keyPath)) {
        Write-Host "[$Name] SKIP - 키 없음: $keyPath" -ForegroundColor Yellow
        return $false
    }
    if (-not $Ip) {
        Write-Host "[$Name] SKIP - Public IP 없음" -ForegroundColor Yellow
        return $false
    }
    Write-Host "[$Name] $Ip ..." -ForegroundColor Cyan
    $cmd = "ssh -o StrictHostKeyChecking=accept-new -i `"$keyPath`" ${EC2_USER}@${Ip} `"$RemoteCmd`""
    Invoke-Expression $cmd
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$Name] OK" -ForegroundColor Green
        return $true
    } else {
        Write-Host "[$Name] FAIL (exit $LASTEXITCODE)" -ForegroundColor Red
        return $false
    }
}

# ------------------------------------------------------------------------------
# 1단계: 로컬 빌드 + ECR 푸시 (-SkipBuild 이면 생략)
# ------------------------------------------------------------------------------
if (-not $SkipBuild) {
    $ErrorActionPreference = "Stop"
    Write-Host "`n=== 1/2 로컬 빌드 (전체 이미지) ===" -ForegroundColor Cyan
    & "$ScriptDir\docker\build.ps1"
    if ($LASTEXITCODE -ne 0) { Write-Host "빌드 실패." -ForegroundColor Red; exit 1 }

    Write-Host "`n=== ECR 로그인 & 태그 & 푸시 ===" -ForegroundColor Cyan
    aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR
    if ($LASTEXITCODE -ne 0) { Write-Host "ECR 로그인 실패." -ForegroundColor Red; exit 1 }

    docker tag academy-api:latest ${ECR}/academy-api:latest
    docker tag academy-messaging-worker:latest ${ECR}/academy-messaging-worker:latest
    docker tag academy-ai-worker-cpu:latest ${ECR}/academy-ai-worker-cpu:latest
    docker tag academy-video-worker:latest ${ECR}/academy-video-worker:latest

    docker push ${ECR}/academy-api:latest
    docker push ${ECR}/academy-messaging-worker:latest
    docker push ${ECR}/academy-ai-worker-cpu:latest
    docker push ${ECR}/academy-video-worker:latest
    if ($LASTEXITCODE -ne 0) { Write-Host "푸시 실패." -ForegroundColor Red; exit 1 }
    Write-Host "푸시 완료.`n" -ForegroundColor Green
    $ErrorActionPreference = "Continue"
}

# ------------------------------------------------------------------------------
# 2단계: EC2 4대 배포 (옵션: 중지된 인스턴스 일괄 기동 후 배포)
# ------------------------------------------------------------------------------
Write-Host "`n=== 2/2 EC2 4대 배포 (ECR pull → 컨테이너 재기동) ===" -ForegroundColor Cyan
Write-Host "Region: $AWS_REGION | Key dir: $KEY_DIR`n" -ForegroundColor Gray

if ($StartInstances) { Start-StoppedAcademyInstances }

$ips = Get-Ec2PublicIps
if ($ips.Count -eq 0) {
    Write-Host "실행 중인 academy 인스턴스를 찾지 못했습니다. AWS CLI 설정과 리전을 확인하세요." -ForegroundColor Red
    exit 1
}

$ok = 0
$fail = 0
foreach ($name in $INSTANCE_ORDER) {
    $ip = $ips[$name]
    $keyFile = $INSTANCE_KEYS[$name]
    $remoteCmd = $REMOTE_CMDS[$name]
    if (Deploy-One -Name $name -Ip $ip -KeyFile $keyFile -RemoteCmd $remoteCmd) { $ok++ } else { $fail++ }
}

Write-Host "`n--- 결과: 성공 $ok / 실패 $fail ---" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
