# ==============================================================================
# Cache-based local build -> ECR push -> deploy (quick code change rollout)
# Requires: Docker local, AWS keys, C:\key\*.pem (EC2 SSH)
#
# Usage: set AWS env then cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget api
# DeployTarget: api | video | ai | messaging | all | workers
# ==============================================================================

param(
    [ValidateSet("all", "api", "video", "ai", "messaging", "workers")]
    [string]$DeployTarget = "api",
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
Push-Location $RepoRoot

try {
    $AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
    if ($LASTEXITCODE -ne 0) { Write-Host "AWS identity check failed. Set access key and retry." -ForegroundColor Red; exit 1 }
    $ECR = "${AccountId}.dkr.ecr.${Region}.amazonaws.com"

    Write-Host "`n=== 1/2 Local build (cache) + ECR push ===`n" -ForegroundColor Cyan

    # Base always (other images depend on it for cache)
    Write-Host "[base] academy-base:latest ..." -ForegroundColor Gray
    docker buildx build --platform linux/arm64 -f docker/Dockerfile.base -t academy-base:latest --load .
    if ($LASTEXITCODE -ne 0) { exit 1 }

    $toPush = @()
    switch ($DeployTarget) {
        "api"        { docker buildx build --platform linux/arm64 -f docker/api/Dockerfile -t academy-api:latest --load .
                       docker tag academy-api:latest "${ECR}/academy-api:latest"
                       $toPush = @("academy-api") }
        "video"      { docker buildx build --platform linux/arm64 -f docker/video-worker/Dockerfile -t academy-video-worker:latest --load .
                       docker tag academy-video-worker:latest "${ECR}/academy-video-worker:latest"
                       $toPush = @("academy-video-worker") }
        "ai"         { docker buildx build --platform linux/arm64 -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest --load .
                       docker tag academy-ai-worker-cpu:latest "${ECR}/academy-ai-worker-cpu:latest"
                       $toPush = @("academy-ai-worker-cpu") }
        "messaging"  { docker buildx build --platform linux/arm64 -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest --load .
                       docker tag academy-messaging-worker:latest "${ECR}/academy-messaging-worker:latest"
                       $toPush = @("academy-messaging-worker") }
        "workers"    { docker buildx build --platform linux/arm64 -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest --load .
                       docker tag academy-messaging-worker:latest "${ECR}/academy-messaging-worker:latest"
                       docker buildx build --platform linux/arm64 -f docker/video-worker/Dockerfile -t academy-video-worker:latest --load .
                       docker tag academy-video-worker:latest "${ECR}/academy-video-worker:latest"
                       docker buildx build --platform linux/arm64 -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest --load .
                       docker tag academy-ai-worker-cpu:latest "${ECR}/academy-ai-worker-cpu:latest"
                       $toPush = @("academy-messaging-worker","academy-video-worker","academy-ai-worker-cpu") }
        "all"        { docker buildx build --platform linux/arm64 -f docker/api/Dockerfile -t academy-api:latest --load .
                       docker tag academy-api:latest "${ECR}/academy-api:latest"
                       docker buildx build --platform linux/arm64 -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest --load .
                       docker tag academy-messaging-worker:latest "${ECR}/academy-messaging-worker:latest"
                       docker buildx build --platform linux/arm64 -f docker/video-worker/Dockerfile -t academy-video-worker:latest --load .
                       docker tag academy-video-worker:latest "${ECR}/academy-video-worker:latest"
                       docker buildx build --platform linux/arm64 -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest --load .
                       docker tag academy-ai-worker-cpu:latest "${ECR}/academy-ai-worker-cpu:latest"
                       $toPush = @("academy-api","academy-messaging-worker","academy-video-worker","academy-ai-worker-cpu") }
    }
    if ($LASTEXITCODE -ne 0) { exit 1 }

    Write-Host "`nECR 로그인 및 푸시..." -ForegroundColor Cyan
    aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR
    foreach ($repo in $toPush) {
        docker push "${ECR}/${repo}:latest"
        if ($LASTEXITCODE -ne 0) { exit 1 }
    }
    Write-Host "푸시 완료.`n" -ForegroundColor Green

    Write-Host "=== 2/2 EC2 배포 ===`n" -ForegroundColor Cyan
    & "$ScriptRoot\full_redeploy.ps1" -SkipBuild -DeployTarget $DeployTarget -KeyDir $KeyDir -Region $Region
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "`n=== Quick Redeploy 완료 ($DeployTarget) ===`n" -ForegroundColor Green
} finally {
    Pop-Location
}
