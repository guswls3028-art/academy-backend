# 모든 워커 ASG 스케일링 정책 재생성 (SQS 기반만, CPU 기반 제거)
# 주의: Application Auto Scaling(ec2:autoScalingGroup:DesiredCapacity)은 일부 계정/리전에서 지원되지 않음
# 대신 Lambda 함수(queue_depth_lambda)에서 직접 ASG desired capacity를 조정함
# Usage: .\scripts\fix_all_worker_scaling_policies.ps1

param(
    [string]$Region = "ap-northeast-2",
    [int]$TargetMessagesPerInstance = 20,
    [int]$MaxCapacity = 20
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

$asgConfigs = @(
    @{
        Name = "academy-ai-worker-asg"
        WorkerType = "AI"
    },
    @{
        Name = "academy-video-worker-asg"
        WorkerType = "Video"
    },
    @{
        Name = "academy-messaging-worker-asg"
        WorkerType = "Messaging"
    }
)

Write-Host "⚠️  주의: Application Auto Scaling(ec2:autoScalingGroup:DesiredCapacity)은" -ForegroundColor Yellow
Write-Host "   일부 계정/리전에서 지원되지 않습니다." -ForegroundColor Yellow
Write-Host ""
Write-Host "✅ 대신 Lambda 함수(queue_depth_lambda)에서 직접 ASG desired capacity를 조정합니다." -ForegroundColor Green
Write-Host ""
Write-Host "Lambda 함수가 다음을 수행합니다:" -ForegroundColor Cyan
Write-Host "  - SQS 큐 깊이(visible + in_flight) 모니터링" -ForegroundColor Gray
Write-Host "  - CloudWatch 메트릭 퍼블리시 (Academy/Workers QueueDepth)" -ForegroundColor Gray
Write-Host "  - 모든 워커(AI, Video, Messaging) ASG desired capacity 직접 조정" -ForegroundColor Gray
Write-Host ""
Write-Host "기존 Application Auto Scaling 정책 확인 중..." -ForegroundColor Cyan
Write-Host ""

foreach ($config in $asgConfigs) {
    $asgName = $config.Name
    $workerType = $config.WorkerType
    $resourceId = "auto-scaling-group/$asgName"
    
    Write-Host "[$asgName]" -ForegroundColor Yellow
    
    # 기존 Application Auto Scaling 정책 확인 및 제거 (있다면)
    Write-Host "  Checking for existing Application Auto Scaling policies..." -ForegroundColor Gray
    $existingPolicies = aws application-autoscaling describe-scaling-policies `
        --service-namespace ec2 `
        --resource-id $resourceId `
        --region $Region `
        --output json 2>$null | ConvertFrom-Json
    
    if ($existingPolicies.ScalingPolicies) {
        foreach ($policy in $existingPolicies.ScalingPolicies) {
            $policyName = $policy.PolicyName
            Write-Host "    Removing policy: $policyName" -ForegroundColor Yellow
            $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            aws application-autoscaling delete-scaling-policy `
                --service-namespace ec2 `
                --resource-id $resourceId `
                --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" `
                --policy-name $policyName `
                --region $Region 2>$null
            $ErrorActionPreference = $ea
        }
        Write-Host "    ✅ All Application Auto Scaling policies removed" -ForegroundColor Green
    } else {
        Write-Host "    ℹ️  No Application Auto Scaling policies found" -ForegroundColor Gray
    }
    
    Write-Host ""
}

Write-Host "Done. All workers now have SQS-based QueueDepthTargetTracking policies." -ForegroundColor Green
Write-Host ""
Write-Host "Verification:" -ForegroundColor Cyan
Write-Host "  Run: .\scripts\check_all_worker_scaling_policies.ps1" -ForegroundColor Gray
