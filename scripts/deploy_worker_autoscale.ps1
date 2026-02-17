# ==============================================================================
# Worker autoscale Lambda + EventBridge deploy
# Requires: AWS CLI configured, Lambda execution role (iam_policy_500.json)
# Usage: .\scripts\deploy_worker_autoscale.ps1 [-RoleArn <arn>] [-FunctionName <name>]
# ==============================================================================

param(
    [string]$RoleArn = "",
    [string]$FunctionName = "academy-worker-autoscale",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$LambdaDir = Join-Path $RepoRoot "infra\worker_autoscale_lambda"
$ZipPath = Join-Path $RepoRoot "worker_autoscale_lambda.zip"

if (-not (Test-Path (Join-Path $LambdaDir "lambda_function.py"))) {
    Write-Host "[ERROR] infra/worker_autoscale_lambda/lambda_function.py not found." -ForegroundColor Red
    exit 1
}

# 1) Zip Lambda (lambda_function.py only)
Write-Host "[1/4] Creating zip..." -ForegroundColor Cyan
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $LambdaDir "lambda_function.py") -DestinationPath $ZipPath -Force

# 2) Create or update Lambda
$exists = $false
try {
    aws lambda get-function --function-name $FunctionName --region $Region 2>$null | Out-Null
    $exists = $true
} catch {}

if ($exists) {
    Write-Host "[2/4] Updating Lambda code: $FunctionName" -ForegroundColor Cyan
    aws lambda update-function-code `
        --function-name $FunctionName `
        --zip-file "fileb://$ZipPath" `
        --region $Region
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] update-function-code failed" -ForegroundColor Red; exit 1 }
    # Match config (Reserved Concurrency = 1, Timeout 60)
    $null = aws lambda put-function-concurrency --function-name $FunctionName --reserved-concurrent-executions 1 --region $Region 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] ReservedConcurrency=1 not set." -ForegroundColor Yellow }
    aws lambda update-function-configuration --function-name $FunctionName --timeout 60 --memory-size 128 --region $Region 2>$null
} else {
    if (-not $RoleArn) {
        Write-Host "[ERROR] Lambda does not exist. Create a role and pass -RoleArn. See infra/worker_autoscale_lambda/iam_policy_500.json" -ForegroundColor Red
        Write-Host "Example: .\scripts\deploy_worker_autoscale.ps1 -RoleArn arn:aws:iam::809466760795:role/academy-worker-autoscale-role" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "[2/4] Creating Lambda: $FunctionName" -ForegroundColor Cyan
    aws lambda create-function `
        --function-name $FunctionName `
        --runtime python3.11 `
        --role $RoleArn `
        --handler lambda_function.lambda_handler `
        --zip-file "fileb://$ZipPath" `
        --timeout 60 `
        --memory-size 128 `
        --region $Region
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] create-function failed" -ForegroundColor Red; exit 1 }
    $null = aws lambda put-function-concurrency --function-name $FunctionName --reserved-concurrent-executions 1 --region $Region 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] ReservedConcurrency=1 not set (account may need 10+ unreserved). Lambda works without it." -ForegroundColor Yellow }
}

# 3) EventBridge Rule (rate 1 minute)
$RuleName = "academy-worker-autoscale-rate"
Write-Host "[3/4] EventBridge rule: $RuleName (rate 1 minute)" -ForegroundColor Cyan
aws events put-rule `
    --name $RuleName `
    --schedule-expression "rate(1 minute)" `
    --state ENABLED `
    --region $Region
if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] put-rule failed" -ForegroundColor Red; exit 1 }

$LambdaArn = (aws lambda get-function --function-name $FunctionName --region $Region --query "Configuration.FunctionArn" --output text)
$AccountId = (aws sts get-caller-identity --query Account --output text)

aws events put-targets `
    --rule $RuleName `
    --targets "Id=1,Arn=$LambdaArn" `
    --region $Region
if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] put-targets failed" -ForegroundColor Red; exit 1 }

# 4) Lambda permission (EventBridge can invoke)
Write-Host "[4/4] Adding Lambda permission for EventBridge" -ForegroundColor Cyan
aws lambda add-permission `
    --function-name $FunctionName `
    --statement-id "EventBridgeInvoke" `
    --action "lambda:InvokeFunction" `
    --principal "events.amazonaws.com" `
    --source-arn "arn:aws:events:${Region}:${AccountId}:rule/${RuleName}" `
    --region $Region 2>$null
# Ignore if already exists

Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
Write-Host "Done. Lambda: $FunctionName | Rule: $RuleName (rate 1 minute) | ReservedConcurrency=1" -ForegroundColor Green
Write-Host "Ensure Lambda role has iam_policy_500.json (SQS, EC2, SSM, Logs)." -ForegroundColor Yellow
