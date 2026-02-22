# ==============================================================================
# delete_r2 Lambda Setup (SQS academy-video-jobs 트리거)
# Video Batch 전환 후, delete_r2 메시지는 Lambda가 API 호출로 처리.
# Usage: .\scripts\infra\delete_r2_lambda_setup.ps1 -Region ap-northeast-2 -ApiBaseUrl "http://internal-api:8000"
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [Parameter(Mandatory=$true)][string]$ApiBaseUrl,
    [string]$LambdaName = "academy-video-delete-r2",
    [string]$QueueName = "academy-video-jobs",
    [string]$VpcSubnetId = "",
    [string]$VpcSecurityGroupId = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$LambdaDir = Join-Path $RepoRoot "infra\worker_asg\delete_r2_lambda"

$AccountId = (aws sts get-caller-identity --query Account --output text)
$RoleName = "academy-lambda"

Write-Host "== delete_r2 Lambda Setup ==" -ForegroundColor Cyan
Write-Host "Lambda=$LambdaName Queue=$QueueName ApiBase=$ApiBaseUrl" -ForegroundColor Gray

# Zip Lambda
$ZipPath = Join-Path $RepoRoot "delete_r2_lambda.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $LambdaDir "lambda_function.py") -DestinationPath $ZipPath

# Env
$envVars = "VIDEO_DELETE_R2_API_URL=$ApiBaseUrl"
if ($env:LAMBDA_INTERNAL_API_KEY) { $envVars += ",LAMBDA_INTERNAL_API_KEY=$env:LAMBDA_INTERNAL_API_KEY" }

# Create/Update
$exists = aws lambda get-function --function-name $LambdaName --region $Region 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Updating Lambda $LambdaName..." -ForegroundColor Yellow
    aws lambda update-function-code --function-name $LambdaName --zip-file "fileb://$ZipPath" --region $Region | Out-Null
    aws lambda update-function-configuration --function-name $LambdaName --environment "Variables={$envVars}" --timeout 120 --memory-size 256 --region $Region | Out-Null
} else {
    Write-Host "Creating Lambda $LambdaName..." -ForegroundColor Yellow
    $roleArn = "arn:aws:iam::${AccountId}:role/$RoleName"
    $vpcConfig = ""
    if ($VpcSubnetId -and $VpcSecurityGroupId) {
        $vpcConfig = " --vpc-config SubnetIds=$VpcSubnetId,SecurityGroupIds=$VpcSecurityGroupId"
    }
    Invoke-Expression "aws lambda create-function --function-name $LambdaName --runtime python3.11 --handler lambda_function.lambda_handler --role $roleArn --zip-file fileb://$ZipPath --timeout 120 --memory-size 256 --environment Variables={$envVars} $vpcConfig --region $Region"
}

# SQS Event Source Mapping
$queueUrl = "https://sqs.${Region}.amazonaws.com/${AccountId}/${QueueName}"
$queueArn = "arn:aws:sqs:${Region}:${AccountId}:${QueueName}"
$mappings = aws lambda list-event-source-mappings --function-name $LambdaName --region $Region --output json | ConvertFrom-Json
$existing = $mappings.EventSourceMappings | Where-Object { $_.EventSourceArn -eq $queueArn }
if (-not $existing) {
    Write-Host "Adding SQS trigger..." -ForegroundColor Yellow
    aws lambda create-event-source-mapping --function-name $LambdaName --event-source-arn $queueArn --batch-size 1 --region $Region
} else {
    Write-Host "SQS trigger exists" -ForegroundColor Gray
}

Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
Write-Host "`nDONE. delete_r2 Lambda ready." -ForegroundColor Green
