# Queue depth Lambda 배포 — full_redeploy.ps1과 별도로 실행
# academy-worker-queue-depth-metric 함수 코드만 업데이트 (conservative_scale_in 등)
#
# 사용: cd C:\academy
#       .\scripts\deploy_queue_depth_lambda.ps1

param(
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

$LambdaName = "academy-worker-queue-depth-metric"
$lambdaPath = Join-Path $RepoRoot "infra\worker_asg\queue_depth_lambda\lambda_function.py"
$zipPath = Join-Path $RepoRoot "worker_queue_depth_lambda.zip"

if (-not (Test-Path $lambdaPath)) {
    Write-Host "FAIL: lambda_function.py not found at $lambdaPath" -ForegroundColor Red
    exit 1
}

Write-Host "[1/2] Packaging Lambda..." -ForegroundColor Cyan
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $lambdaPath -DestinationPath $zipPath -Force

Write-Host "[2/2] Updating Lambda function code: $LambdaName" -ForegroundColor Cyan
aws lambda update-function-code --function-name $LambdaName --zip-file "fileb://$zipPath" --region $Region
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: Lambda update failed" -ForegroundColor Red; exit 1 }

Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

Write-Host "Lambda update initiated. Waiting for LastUpdateStatus=Successful..." -ForegroundColor Gray
$maxWait = 30
$waited = 0
do {
    Start-Sleep -Seconds 2
    $waited += 2
    $status = aws lambda get-function-configuration --function-name $LambdaName --region $Region --query "LastUpdateStatus" --output text
    if ($status -eq "Successful") {
        Write-Host "Done. Lambda $LambdaName updated." -ForegroundColor Green
        exit 0
    }
    if ($waited -ge $maxWait) {
        Write-Host "WARN: Update may still be in progress. Check AWS Lambda console." -ForegroundColor Yellow
        exit 0
    }
} while ($true)
