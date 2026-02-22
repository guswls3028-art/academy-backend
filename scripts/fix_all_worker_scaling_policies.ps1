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
        Name = "academy-messaging-worker-asg"
        WorkerType = "Messaging"
    }
)

Write-Host "Note: Application Auto Scaling (ec2:autoScalingGroup:DesiredCapacity) is not" -ForegroundColor Yellow
Write-Host "  supported in some accounts/regions." -ForegroundColor Yellow
Write-Host ""
Write-Host "Instead, Lambda (queue_depth_lambda) sets ASG desired capacity directly." -ForegroundColor Green
Write-Host ""
Write-Host "Lambda does:" -ForegroundColor Cyan
Write-Host "  - SQS queue depth (visible + in_flight) monitoring" -ForegroundColor Gray
Write-Host "  - CloudWatch metric publish (Academy/Workers QueueDepth)" -ForegroundColor Gray
Write-Host "  - Direct ASG desired capacity for AI, Video, Messaging workers" -ForegroundColor Gray
Write-Host ""
Write-Host "Checking for existing Application Auto Scaling policies..." -ForegroundColor Cyan
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

Write-Host "Done. Application Auto Scaling policies removed (or none existed)." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Deploy Lambda:" -ForegroundColor Gray
Write-Host "     .\scripts\deploy_worker_asg.ps1 -SubnetIds ""subnet-xxx"" -SecurityGroupId ""sg-xxx"" -IamInstanceProfileName ""academy-ec2-role""" -ForegroundColor White
Write-Host ""
Write-Host "  2. Lambda adjusts ASG desired capacity for all workers:" -ForegroundColor Gray
Write-Host "     - AI: academy-ai-worker-asg" -ForegroundColor White
Write-Host "     - Video: academy-video-worker-asg" -ForegroundColor White
Write-Host "     - Messaging: academy-messaging-worker-asg" -ForegroundColor White
Write-Host ""
Write-Host "  3. Scaling logic:" -ForegroundColor Gray
$msg = "     - (visible + in_flight) / " + $TargetMessagesPerInstance + " = desired capacity"
Write-Host $msg -ForegroundColor White
Write-Host "     - Min/Max: ASG Min/Max capacity" -ForegroundColor White
