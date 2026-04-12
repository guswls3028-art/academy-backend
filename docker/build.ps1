# Docker 이미지 빌드 (Windows PowerShell)
# 사용법:
#   .\docker\build.ps1              # 전체 빌드
#   .\docker\build.ps1 -Target api  # API만 (base 자동 포함)
#   .\docker\build.ps1 -AiGpu       # GPU 이미지만 (TODO: 운영 준비 시)

param(
    [ValidateSet("all", "base", "api", "video", "ai-cpu", "messaging")]
    [string]$Target = "all",
    [switch]$AiGpu
)

$ErrorActionPreference = "Stop"

Push-Location (Split-Path $PSScriptRoot -Parent)
try {

function Build-Base {
    Write-Host "`n[base] academy-base..." -ForegroundColor Yellow
    docker build -f docker/Dockerfile.base -t academy-base:latest .
}

function Build-Api {
    Write-Host "`n[api] academy-api..." -ForegroundColor Yellow
    docker build -f docker/api/Dockerfile -t academy-api:latest .
}

function Build-Video {
    Write-Host "`n[video] academy-video-worker..." -ForegroundColor Yellow
    docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
}

function Build-AiCpu {
    Write-Host "`n[ai-cpu] academy-ai-worker-cpu..." -ForegroundColor Yellow
    docker build -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest .
}

function Build-Messaging {
    Write-Host "`n[messaging] academy-messaging-worker..." -ForegroundColor Yellow
    docker build -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .
}

function Build-AiGpu {
    Write-Host "`n[ai-gpu] academy-ai-worker-gpu..." -ForegroundColor Yellow
    docker build -f docker/ai-worker-gpu/Dockerfile -t academy-ai-worker-gpu:latest .
}

if ($AiGpu) {
    Build-AiGpu
    Write-Host "`nDone." -ForegroundColor Green
    docker images | Select-String "academy-ai-worker-gpu"
    return
}

switch ($Target) {
    "base"      { Build-Base }
    "api"       { Build-Base; Build-Api }
    "video"     { Build-Base; Build-Video }
    "ai-cpu"    { Build-Base; Build-AiCpu }
    "messaging" { Build-Base; Build-Messaging }
    "all"       { Build-Base; Build-Api; Build-Video; Build-AiCpu; Build-Messaging }
}

Write-Host "`nDone." -ForegroundColor Green
docker images | Select-String "academy"

} finally { Pop-Location }
