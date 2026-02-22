# 모든 워커 ASG 스케일링 정책 확인 (AI, Messaging만. Video = Batch 전용)
# Usage: .\scripts\check_all_worker_scaling_policies.ps1

param(
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"

$asgNames = @("academy-ai-worker-asg", "academy-messaging-worker-asg")

Write-Host "Checking scaling policies for all workers..." -ForegroundColor Cyan
Write-Host ""

foreach ($asgName in $asgNames) {
    $resourceId = "auto-scaling-group/$asgName"
    Write-Host "[$asgName]" -ForegroundColor Yellow
    
    # Application Auto Scaling 정책 확인
    $policies = aws application-autoscaling describe-scaling-policies `
        --service-namespace ec2 `
        --resource-id $resourceId `
        --region $Region `
        --output json | ConvertFrom-Json
    
    if ($policies.ScalingPolicies.Count -eq 0) {
        Write-Host "  ❌ No Application Auto Scaling policies found" -ForegroundColor Red
    } else {
        Write-Host "  ✅ Found $($policies.ScalingPolicies.Count) policy/policies:" -ForegroundColor Green
        foreach ($policy in $policies.ScalingPolicies) {
            $policyType = $policy.PolicyType
            $policyName = $policy.PolicyName
            
            if ($policy.TargetTrackingScalingPolicyConfiguration) {
                $metric = $policy.TargetTrackingScalingPolicyConfiguration.CustomizedMetricSpecification
                if ($metric) {
                    Write-Host "    - $policyName ($policyType): Custom metric $($metric.MetricName) in $($metric.Namespace)" -ForegroundColor Gray
                } else {
                    $predefined = $policy.TargetTrackingScalingPolicyConfiguration.PredefinedMetricSpecification
                    if ($predefined) {
                        Write-Host "    - $policyName ($policyType): Predefined metric $($predefined.PredefinedMetricType)" -ForegroundColor Gray
                        if ($predefined.PredefinedMetricType -like "*CPU*") {
                            Write-Host "      ⚠️  CPU-based scaling detected!" -ForegroundColor Red
                        }
                    }
                }
            } else {
                Write-Host "    - $policyName ($policyType): Step/Simple scaling" -ForegroundColor Gray
            }
        }
    }
    
    # EC2 Auto Scaling 정책 확인
    $ec2Policies = aws autoscaling describe-policies `
        --auto-scaling-group-name $asgName `
        --region $Region `
        --output json | ConvertFrom-Json
    
    if ($ec2Policies.ScalingPolicies.Count -gt 0) {
        Write-Host "  ⚠️  Found $($ec2Policies.ScalingPolicies.Count) EC2 Auto Scaling policy/policies:" -ForegroundColor Yellow
        foreach ($policy in $ec2Policies.ScalingPolicies) {
            Write-Host "    - $($policy.PolicyName): $($policy.PolicyType)" -ForegroundColor Gray
            if ($policy.MetricAlarms) {
                foreach ($alarm in $policy.MetricAlarms) {
                    if ($alarm.MetricName -like "*CPU*") {
                        Write-Host "      ⚠️  CPU-based alarm detected!" -ForegroundColor Red
                    }
                }
            }
        }
    }
    
    Write-Host ""
}

Write-Host "Summary:" -ForegroundColor Cyan
Write-Host "  - Application Auto Scaling: SQS-based QueueDepthTargetTracking should exist" -ForegroundColor Gray
Write-Host "  - EC2 Auto Scaling: Should be empty (no CPU-based policies)" -ForegroundColor Gray
