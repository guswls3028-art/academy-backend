# Docker 이미지 빌드 (Windows PowerShell)
# 순서: academy-base -> api/workers
#
# AI Worker 분리 빌드:
#   .\docker\build.ps1 -AiWorkerCpu    # academy-ai-worker-cpu 만
#   .\docker\build.ps1 -AiWorkerGpu    # academy-ai-worker-gpu 만
#   .\docker\build.ps1 -AiWorkerBoth   # CPU + GPU 둘 다

param(
    [switch]$AiWorkerCpu,
    [switch]$AiWorkerGpu,
    [switch]$AiWorkerBoth
)

$ErrorActionPreference = "Stop"

$buildAll = -not ($AiWorkerCpu -or $AiWorkerGpu -or $AiWorkerBoth)

function Build-AiWorkerCpu {
    Write-Host "`n[AI-CPU] academy-ai-worker-cpu..." -ForegroundColor Yellow
    docker build -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest .
}

function Build-AiWorkerGpu {
    Write-Host "`n[AI-GPU] academy-ai-worker-gpu..." -ForegroundColor Yellow
    docker build -f docker/ai-worker-gpu/Dockerfile -t academy-ai-worker-gpu:latest .
}

if ($AiWorkerCpu) {
    Write-Host "Building AI Worker CPU only..." -ForegroundColor Cyan
    Build-AiWorkerCpu
    Write-Host "`nDone." -ForegroundColor Green
    docker images | Select-String "academy-ai-worker-cpu"
    exit 0
}

if ($AiWorkerGpu) {
    Write-Host "Building AI Worker GPU only..." -ForegroundColor Cyan
    Build-AiWorkerGpu
    Write-Host "`nDone." -ForegroundColor Green
    docker images | Select-String "academy-ai-worker-gpu"
    exit 0
}

if ($AiWorkerBoth) {
    Write-Host "Building AI Worker CPU + GPU..." -ForegroundColor Cyan
    Build-AiWorkerCpu
    Build-AiWorkerGpu
    Write-Host "`nDone." -ForegroundColor Green
    docker images | Select-String "academy-ai-worker"
    exit 0
}

# ---- 풀 빌드 ----
Write-Host "Building Docker images..." -ForegroundColor Cyan

Write-Host "`n[1/6] academy-base..." -ForegroundColor Yellow
docker build -f docker/Dockerfile.base -t academy-base:latest .

Write-Host "`n[2/6] academy-api..." -ForegroundColor Yellow
docker build -f docker/api/Dockerfile -t academy-api:latest .

Write-Host "`n[3/6] academy-video-worker..." -ForegroundColor Yellow
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .

Write-Host "`n[4/6] academy-ai-worker (legacy single)..." -ForegroundColor Yellow
docker build -f docker/ai-worker/Dockerfile -t academy-ai-worker:latest .

Write-Host "`n[5/6] academy-ai-worker-cpu..." -ForegroundColor Yellow
Build-AiWorkerCpu

Write-Host "`n[6/6] academy-ai-worker-gpu..." -ForegroundColor Yellow
Build-AiWorkerGpu

Write-Host "`n[7/7] academy-messaging-worker..." -ForegroundColor Yellow
docker build -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .

Write-Host "`nAll images built." -ForegroundColor Green
docker images | Select-String "academy"
