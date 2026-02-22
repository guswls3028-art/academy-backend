# ==============================================================================
# Build Docker images + ECR push (로컬 Windows용, Docker Desktop 필요)
# 로컬에 Docker 없으면 → 원격 빌드 서버 사용:
#   .\scripts\build_and_push_ecr_remote.ps1 -ApiOnly
#   .\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly
#   .\scripts\build_and_push_ecr_remote.ps1 -ApiOnly -GitRepoUrl "https://github.com/..."
# 그 다음 배포: .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget api
# Requires: Docker running, AWS CLI configured. Optional: .env with ECR_REGISTRY or AWS_ACCOUNT_ID
#
# -VideoWorker : Video Worker만 빌드/푸시 (base + video-worker)
# ==============================================================================

param(
    [switch]$VideoWorker = $false
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $ScriptRoot
Set-Location $root

$region = $env:AWS_DEFAULT_REGION
if (-not $region) { $region = "ap-northeast-2" }

# ECR registry: ECR_REGISTRY or AWS_ACCOUNT_ID from env, else from AWS CLI
$registry = $env:ECR_REGISTRY
if (-not $registry) {
    $accountId = $env:AWS_ACCOUNT_ID
    if (-not $accountId) {
        $accountId = aws sts get-caller-identity --query Account --output text 2>$null
        if (-not $accountId) {
            Write-Host "ERROR: Set ECR_REGISTRY or AWS_ACCOUNT_ID in .env, or run: aws configure / aws sso login" -ForegroundColor Red
            exit 1
        }
    }
    $registry = "${accountId}.dkr.ecr.${region}.amazonaws.com"
}
if ($registry -match '^\.dkr\.' -or $registry -notmatch '\d{12}') {
    Write-Host "ERROR: Invalid ECR registry (missing account ID): $registry" -ForegroundColor Red
    Write-Host "  Set AWS_ACCOUNT_ID in .env or ensure AWS CLI returns account." -ForegroundColor Yellow
    exit 1
}

# Docker must be running (Desktop or daemon)
$dockerOk = docker info 2>$null; if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker is not running or not in PATH. Start Docker Desktop (Windows) or docker daemon." -ForegroundColor Red
    exit 1
}

Write-Host "ECR Registry: $registry"
Write-Host "Region: $region"
if ($VideoWorker) { Write-Host "VideoWorker only: base + video-worker" -ForegroundColor Cyan }
Write-Host ""

# 1. Base (always needed for workers)
Write-Host "[1/5] academy-base..."
docker buildx build --platform linux/arm64 -f docker/Dockerfile.base -t academy-base:latest --load .

if ($VideoWorker) {
    Write-Host "[2/5] academy-video-worker (skip api/messaging/ai)..."
    docker buildx build --platform linux/arm64 -f docker/video-worker/Dockerfile -t academy-video-worker:latest --load .
    docker tag academy-video-worker:latest "${registry}/academy-video-worker:latest"
    Write-Host "ECR login..."
    aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $registry
    aws ecr create-repository --repository-name academy-video-worker --region $region 2>$null
    Write-Host "ECR push academy-video-worker..."
    docker push "${registry}/academy-video-worker:latest"
    Write-Host "Done (VideoWorker only)."
    exit 0
}

# 2. API
Write-Host "[2/5] academy-api..."
docker buildx build --platform linux/arm64 -f docker/api/Dockerfile -t academy-api:latest --load .
docker tag academy-api:latest "${registry}/academy-api:latest"

# 3. Messaging Worker
Write-Host "[3/5] academy-messaging-worker..."
docker buildx build --platform linux/arm64 -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest --load .
docker tag academy-messaging-worker:latest "${registry}/academy-messaging-worker:latest"

# 4. Video Worker
Write-Host "[4/5] academy-video-worker..."
docker buildx build --platform linux/arm64 -f docker/video-worker/Dockerfile -t academy-video-worker:latest --load .
docker tag academy-video-worker:latest "${registry}/academy-video-worker:latest"

# 5. AI Worker CPU
Write-Host "[5/5] academy-ai-worker-cpu..."
docker buildx build --platform linux/arm64 -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest --load .
docker tag academy-ai-worker-cpu:latest "${registry}/academy-ai-worker-cpu:latest"

# ECR login
Write-Host "ECR login..."
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $registry

# Create repos if missing
$repos = @("academy-api", "academy-messaging-worker", "academy-video-worker", "academy-ai-worker-cpu")
foreach ($repo in $repos) {
    aws ecr create-repository --repository-name $repo --region $region 2>$null
}

# Push
Write-Host "ECR push..."
docker push "${registry}/academy-api:latest"
docker push "${registry}/academy-messaging-worker:latest"
docker push "${registry}/academy-video-worker:latest"
docker push "${registry}/academy-ai-worker-cpu:latest"

Write-Host "Done."
