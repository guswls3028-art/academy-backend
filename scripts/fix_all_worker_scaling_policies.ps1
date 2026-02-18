# 모든 워커 ASG 스케일링 정책 재생성 (SQS 기반만, CPU 기반 제거)
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

Write-Host "Creating SQS-based scaling policies for all workers..." -ForegroundColor Cyan
Write-Host ""

foreach ($config in $asgConfigs) {
    $asgName = $config.Name
    $workerType = $config.WorkerType
    $resourceId = "auto-scaling-group/$asgName"
    
    Write-Host "[$asgName]" -ForegroundColor Yellow
    
    # 1. Application Auto Scaling 타겟 등록
    Write-Host "  Registering scalable target..." -ForegroundColor Gray
    $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    aws application-autoscaling register-scalable-target --service-namespace ec2 --resource-id $resourceId `
        --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --min-capacity 1 --max-capacity $MaxCapacity --region $Region 2>$null
    $ErrorActionPreference = $ea
    
    # 2. 기존 CPU 기반 정책 제거 (있다면)
    Write-Host "  Checking for CPU-based policies..." -ForegroundColor Gray
    $existingPolicies = aws application-autoscaling describe-scaling-policies `
        --service-namespace ec2 `
        --resource-id $resourceId `
        --region $Region `
        --output json 2>$null | ConvertFrom-Json
    
    if ($existingPolicies.ScalingPolicies) {
        foreach ($policy in $existingPolicies.ScalingPolicies) {
            $policyName = $policy.PolicyName
            $hasCpuMetric = $false
            
            if ($policy.TargetTrackingScalingPolicyConfiguration) {
                $predefined = $policy.TargetTrackingScalingPolicyConfiguration.PredefinedMetricSpecification
                if ($predefined -and $predefined.PredefinedMetricType -like "*CPU*") {
                    $hasCpuMetric = $true
                }
            }
            
            if ($hasCpuMetric -or $policyName -like "*CPU*") {
                Write-Host "    Removing CPU-based policy: $policyName" -ForegroundColor Yellow
                $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
                aws application-autoscaling delete-scaling-policy `
                    --service-namespace ec2 `
                    --resource-id $resourceId `
                    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" `
                    --policy-name $policyName `
                    --region $Region 2>$null
                $ErrorActionPreference = $ea
            }
        }
    }
    
    # 3. SQS 기반 Target Tracking 정책 생성/업데이트
    Write-Host "  Creating QueueDepthTargetTracking policy..." -ForegroundColor Gray
    
    # AWS CLI 문서에 따르면 파일 내용은 TargetTrackingScalingPolicyConfiguration 객체의 내용이어야 함
    # 최상위 레벨에 TargetTrackingScalingPolicyConfiguration 래퍼가 없어야 함
    $targetValue = $TargetMessagesPerInstance
    $policyContent = @"
{
  "TargetValue": $targetValue,
  "CustomizedMetricSpecification": {
    "MetricName": "QueueDepth",
    "Namespace": "Academy/Workers",
    "Dimensions": [{"Name": "WorkerType", "Value": "$workerType"}],
    "Statistic": "Average"
  },
  "ScaleInCooldown": 600,
  "ScaleOutCooldown": 60
}
"@
    
    $policyFile = Join-Path $RepoRoot "asg_policy_${workerType}_temp.json"
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($policyFile, $policyContent, $utf8NoBom)
    
    # JSON 유효성 검사
    try {
        $jsonTest = Get-Content $policyFile -Raw | ConvertFrom-Json
        if (-not $jsonTest.TargetTrackingScalingPolicyConfiguration) {
            Write-Host "    ⚠️  JSON structure validation failed" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "    ⚠️  Invalid JSON: $_" -ForegroundColor Yellow
        Write-Host "    File content:" -ForegroundColor Gray
        Get-Content $policyFile | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
    }
    
    $policyPath = "file://$($policyFile -replace '\\','/' -replace ' ', '%20')"
    
    $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $result = aws application-autoscaling put-scaling-policy --service-namespace ec2 --resource-id $resourceId `
        --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --policy-name "QueueDepthTargetTracking" `
        --policy-type "TargetTrackingScaling" --target-tracking-scaling-policy-configuration $policyPath --region $Region 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    ✅ Policy created/updated successfully" -ForegroundColor Green
    } else {
        Write-Host "    ❌ Policy creation failed" -ForegroundColor Red
        Write-Host "    Error output:" -ForegroundColor Yellow
        $result | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
    }
    $ErrorActionPreference = $ea
    if (Test-Path $policyFile) {
        Remove-Item $policyFile -Force -ErrorAction SilentlyContinue
    }
    
    Write-Host ""
}

Write-Host "Done. All workers now have SQS-based QueueDepthTargetTracking policies." -ForegroundColor Green
Write-Host ""
Write-Host "Verification:" -ForegroundColor Cyan
Write-Host "  Run: .\scripts\check_all_worker_scaling_policies.ps1" -ForegroundColor Gray
