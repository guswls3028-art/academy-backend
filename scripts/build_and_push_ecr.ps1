# ==============================================================================
# Build Docker images + ECR push
# Requires: .env loaded (ECR_REGISTRY, AWS_ACCOUNT_ID). In terminal: load .env then run this script
# ==============================================================================

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$registry = $env:ECR_REGISTRY
if (-not $registry) { $registry = "$($env:AWS_ACCOUNT_ID).dkr.ecr.ap-northeast-2.amazonaws.com" }
$region = $env:AWS_DEFAULT_REGION
if (-not $region) { $region = "ap-northeast-2" }

Write-Host "ECR Registry: $registry"
Write-Host "Region: $region"
Write-Host ""

# 1. Base
Write-Host "[1/5] academy-base..."
docker buildx build --platform linux/arm64 -f docker/Dockerfile.base -t academy-base:latest --load .

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
