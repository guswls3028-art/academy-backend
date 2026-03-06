# ==============================================================================
# API ASG Instance Refresh 수동 실행
# ==============================================================================
# 새 인스턴스가 기동되면 UserData에서 최신 SSM env를 읽어 /opt/api.env 생성 후 컨테이너 실행.
# 사용: pwsh scripts/v1/start-api-instance-refresh.ps1 -AwsProfile default
# ==============================================================================
param([string]$AwsProfile = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile) { $env:AWS_PROFILE = $AwsProfile; if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" } }

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"

$minHealthy = if ($script:ApiInstanceRefreshMinHealthyPercentage -gt 0) { $script:ApiInstanceRefreshMinHealthyPercentage } else { 100 }
$warmup = if ($script:ApiInstanceRefreshInstanceWarmup -gt 0) { $script:ApiInstanceRefreshInstanceWarmup } else { 300 }
$prefs = "{`"MinHealthyPercentage`":$minHealthy,`"InstanceWarmup`":$warmup}"

Write-Host "API ASG Instance Refresh 시작: $($script:ApiASGName)" -ForegroundColor Cyan
Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:ApiASGName, "--preferences", $prefs, "--region", $script:Region) -ErrorMessage "start-instance-refresh"
$healthUrl = if ($script:ApiBaseUrl) { "$($script:ApiBaseUrl.TrimEnd('/'))/healthz" } else { "https://api.hakwonplus.com/healthz" }
Write-Host "완료까지 5~10분 소요. healthz 확인: $healthUrl" -ForegroundColor Green
