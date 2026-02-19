# Verify Video Worker 1:1 scaling (Lambda deploy + 3 messages + invoke + ASG check)
# Usage: .\scripts\verify_video_worker_scaling.ps1
# Output: PASS or FAIL

param(
    [string]$Region = "ap-northeast-2",
    [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
)

$ErrorActionPreference = "Stop"
$LambdaName = "academy-worker-queue-depth-metric"
$QueueName = "academy-video-jobs"
$AsgName = "academy-video-worker-asg"

# 1) Deploy Lambda
$zipPath = Join-Path $RepoRoot "worker_queue_depth_lambda.zip"
$lambdaPath = Join-Path $RepoRoot "infra\worker_asg\queue_depth_lambda\lambda_function.py"
if (-not (Test-Path $lambdaPath)) { Write-Host "FAIL (lambda_function.py not found)"; exit 1 }
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $lambdaPath -DestinationPath $zipPath -Force
aws lambda update-function-code --function-name $LambdaName --zip-file "fileb://$zipPath" --region $Region | Out-Null
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

# 2) Wait for Lambda update Successful
$maxWait = 30
$waited = 0
do {
    Start-Sleep -Seconds 2
    $waited += 2
    $status = aws lambda get-function-configuration --function-name $LambdaName --region $Region --query "LastUpdateStatus" --output text
    if ($status -eq "Successful") { break }
    if ($waited -ge $maxWait) { Write-Host "FAIL (Lambda update timeout)"; exit 1 }
} while ($true)

# 3) Queue URL and send 3 messages
$queueUrl = aws sqs get-queue-url --queue-name $QueueName --region $Region --query QueueUrl --output text
1..3 | ForEach-Object {
    aws sqs send-message --queue-url $queueUrl --message-body '{"test":true}' --region $Region | Out-Null
}

# 4) Invoke Lambda
$invokeOut = Join-Path $RepoRoot "verify_video_scaling_out.json"
aws lambda invoke --function-name $LambdaName --payload '{}' --region $Region $invokeOut | Out-Null
Remove-Item $invokeOut -Force -ErrorAction SilentlyContinue

# 5) Short wait then check ASG
Start-Sleep -Seconds 3
$desired = [int](aws autoscaling describe-auto-scaling-groups --region $Region --auto-scaling-group-names $AsgName --query "AutoScalingGroups[0].DesiredCapacity" --output text)

# PASS: with 3+ messages (visible+in_flight), 1:1 formula => desired >= 3
if ($desired -ge 3) {
    Write-Host "PASS (Desired=$desired)"
    exit 0
} else {
    Write-Host "FAIL (Desired=$desired, expected >= 3)"
    exit 1
}
