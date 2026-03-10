# API ALB target healthy 될 때까지 폴링. 성공 시 exit 0. 사용: pwsh -File wait-api-target-healthy.ps1 [-MaxWaitSeconds 1200]
param([int]$MaxWaitSeconds = 1200)
$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = "default"
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
$tgArn = "arn:aws:elasticloadbalancing:ap-northeast-2:809466760795:targetgroup/academy-v1-api-tg/2c34b94ea3c33101"
$elapsed = 0
while ($elapsed -lt $MaxWaitSeconds) {
    $r = aws elbv2 describe-target-health --target-group-arn $tgArn --region ap-northeast-2 --profile default --query "TargetHealthDescriptions[*].TargetHealth.State" --output text 2>&1
    $hasHealthy = $r -match "healthy"
    Write-Host "Target health: $r (${elapsed}s)"
    if ($hasHealthy) { Write-Host "DONE: at least one healthy"; exit 0 }
    Start-Sleep -Seconds 30
    $elapsed += 30
}
Write-Host "TIMEOUT"; exit 2
