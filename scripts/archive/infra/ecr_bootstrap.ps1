# ==============================================================================
# ECR bootstrap: create repository if not exists, set scanOnPush=true, output full ECR URI.
# Fail if region mismatch.
# Usage: .\scripts\infra\ecr_bootstrap.ps1 -Region ap-northeast-2 [-RepositoryName academy-video-worker]
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$RepositoryName = "academy-video-worker"
)

$ErrorActionPreference = "Stop"

$AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0) { Write-Host "AWS identity check failed" -ForegroundColor Red; exit 1 }

$DefaultRegion = (aws configure get region 2>&1)
if ($DefaultRegion -and $DefaultRegion -ne $Region) {
    Write-Host "WARN: default AWS region ($DefaultRegion) differs from -Region $Region. Using -Region $Region for ECR." -ForegroundColor Yellow
}

$repo = $null
try {
    $repo = aws ecr describe-repositories --repository-names $RepositoryName --region $Region --output json 2>&1 | ConvertFrom-Json
} catch {}
if (-not $repo -or -not $repo.repositories) {
    Write-Host "Creating ECR repository: $RepositoryName" -ForegroundColor Cyan
    aws ecr create-repository --repository-name $RepositoryName --region $Region --image-scanning-configuration scanOnPush=true | Out-Null
} else {
    Write-Host "ECR repository exists: $RepositoryName" -ForegroundColor Gray
    aws ecr put-image-scanning-configuration --repository-name $RepositoryName --region $Region --image-scanning-configuration scanOnPush=true 2>$null | Out-Null
}

$desc = aws ecr describe-repositories --repository-names $RepositoryName --region $Region --output json | ConvertFrom-Json
$uri = $desc.repositories[0].repositoryUri
$uriRegion = ($uri -split '\.')[2]
if ($uriRegion -ne $Region) {
    Write-Host "FAIL: ECR repository region ($uriRegion) does not match -Region $Region." -ForegroundColor Red
    exit 1
}

$fullUri = $uri + ":latest"
Write-Host "ECR_URI=$fullUri" -ForegroundColor Green
Write-Output $fullUri
