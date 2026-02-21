# ==============================================================================
# STRICT MODE — VIDEO WORKER ROOT CAUSE DIAGNOSTIC
# Gathers factual AWS runtime state only. No interpretation. stderr not hidden.
# 결과 요약을 JSON 파일로 저장 후 핵심 요약 출력.
# ==============================================================================
# Usage: .\scripts\diagnose_video_worker_full.ps1
#        .\scripts\diagnose_video_worker_full.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$QueueName = "academy-video-jobs",
    [string]$LambdaName = "academy-worker-queue-depth-metric"
)

$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$diagnoseResult = @{
    timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    region    = $Region
    asgName   = $AsgName
    queueName = $QueueName
    lambdaName = $LambdaName
    summary   = @{}
    lambda    = $null
    sqs       = $null
    asg       = $null
    policy    = $null
    activities = $null
}

# ------------------------------------------------------------------------------
# 1) Lambda Runtime Behavior
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 1. Lambda Runtime Behavior =========="
$invokeOut = Join-Path $repoRoot "response_full.json"
Write-Host "[Invoke]"
& aws lambda invoke --function-name $LambdaName --region $Region --cli-binary-format raw-in-base64-out $invokeOut
Write-Host "[Payload]"
if (Test-Path $invokeOut) {
    Get-Content $invokeOut -Raw -Encoding UTF8
} else {
    Write-Host "(no file)"
}
Write-Host "[Payload parsed - metrics]"
try {
    $payload = Get-Content $invokeOut -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
    $diagnoseResult.lambda = @{
        video_queue_depth = $payload.video_queue_depth
        video_queue_depth_total = $payload.video_queue_depth_total
        video_backlog_count = $payload.video_backlog_count
        ai_queue_depth = $payload.ai_queue_depth
        messaging_queue_depth = $payload.messaging_queue_depth
    }
    $vt = if ($null -ne $payload.PSObject.Properties["video_queue_depth_total"]) { $payload.video_queue_depth_total } else { $payload.video_backlog_count }
    Write-Host "video_queue_depth_total: $vt"
    Write-Host "video_queue_depth: $($payload.video_queue_depth)"
    Write-Host "ai_queue_depth: $($payload.ai_queue_depth)"
    Write-Host "messaging_queue_depth: $($payload.messaging_queue_depth)"
} catch {
    Write-Host $_.Exception.Message
}
Write-Host "[Logs tail last 5 min]"
$logGroup = "/aws/lambda/$LambdaName"
$startMs = [DateTimeOffset]::UtcNow.AddMinutes(-5).ToUnixTimeMilliseconds()
& aws logs tail $logGroup --since 5m --region $Region --format short

# ------------------------------------------------------------------------------
# 2) Lambda CloudWatch Logs (last 30 min) — VideoQueueDepthTotal, 403, timeout
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 2. Lambda CloudWatch Logs (last 30 min) =========="
$start30 = (Get-Date).AddMinutes(-30).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$end30 = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$start30Ms = [DateTimeOffset]::Parse($start30).ToUnixTimeMilliseconds()
Write-Host "[Filter: VideoQueueDepthTotal]"
& aws logs filter-log-events --log-group-name $logGroup --region $Region --start-time $start30Ms --filter-pattern "VideoQueueDepthTotal" --output json
Write-Host "[Filter: 403]"
& aws logs filter-log-events --log-group-name $logGroup --region $Region --start-time $start30Ms --filter-pattern "403" --output json
Write-Host "[Filter: timeout]"
& aws logs filter-log-events --log-group-name $logGroup --region $Region --start-time $start30Ms --filter-pattern "timeout" --output json

# ------------------------------------------------------------------------------
# 3) CloudWatch Metric Used By ASG (VideoQueueDepthTotal / BacklogCount)
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 3. CloudWatch Metric (Academy/VideoProcessing) =========="
$mStart = (Get-Date).AddMinutes(-60).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$mEnd = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
Write-Host "[VideoQueueDepthTotal - SQS 기반 스케일링 메트릭]"
$cwVideoTotal = aws cloudwatch get-metric-statistics --region $Region --namespace "Academy/VideoProcessing" --metric-name VideoQueueDepthTotal `
    --dimensions Name=WorkerType,Value=Video Name=AutoScalingGroupName,Value=$AsgName `
    --start-time $mStart --end-time $mEnd --period 60 --statistics Average Maximum Minimum SampleCount --output json
Write-Host $cwVideoTotal
Write-Host "[BacklogCount - 레거시, 제거 권장]"
& aws cloudwatch get-metric-statistics --region $Region --namespace "Academy/VideoProcessing" --metric-name BacklogCount `
    --dimensions Name=WorkerType,Value=Video Name=AutoScalingGroupName,Value=$AsgName `
    --start-time $mStart --end-time $mEnd --period 60 --statistics Average Maximum Minimum SampleCount --output json

# ------------------------------------------------------------------------------
# 4) SQS Runtime State
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 4. SQS Runtime State =========="
$qurl = aws sqs get-queue-url --queue-name $QueueName --region $Region --query "QueueUrl" --output text
Write-Host "QueueUrl: $qurl"
if ($qurl) {
    $sqsAttrsRaw = aws sqs get-queue-attributes --queue-url $qurl --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed --region $Region --output json
    Write-Host $sqsAttrsRaw
    if ($sqsAttrsRaw) {
        try {
            $sqsA = ($sqsAttrsRaw | ConvertFrom-Json).Attributes
            $v = [int]($sqsA.ApproximateNumberOfMessages)
            $nv = [int]($sqsA.ApproximateNumberOfMessagesNotVisible)
            $diagnoseResult.sqs = @{ visible = $v; notVisible = $nv; total = $v + $nv }
        } catch { Write-Host "Parse SQS attrs: $($_.Exception.Message)" }
    }
}

# ------------------------------------------------------------------------------
# 5) ASG Scaling Activities (last 30) — full StatusReason, Details, Cause
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 5. ASG Scaling Activities (last 30) =========="
$activitiesRaw = aws autoscaling describe-scaling-activities --auto-scaling-group-name $AsgName --region $Region --max-items 30 --output json
Write-Host $activitiesRaw
if ($activitiesRaw) {
    try {
        $actObj = $activitiesRaw | ConvertFrom-Json
        $actList = $actObj.Activities
        $cnt = if ($actList) { @($actList).Count } else { 0 }
        $recent = if ($actList) { $actList | Select-Object -First 3 | ForEach-Object { $_.StatusReason } } else { @() }
        $diagnoseResult.activities = @{ count = $cnt; recentStatus = $recent }
    } catch { Write-Host "Parse activities: $($_.Exception.Message)" }
}

# ------------------------------------------------------------------------------
# 6) Running Worker Instances + ASG capacity + Scaling policy
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 6. Running Worker Instances =========="
$instancesOut = aws ec2 describe-instances --region $Region --filters "Name=tag:aws:autoscaling:groupName,Values=$AsgName" "Name=instance-state-name,Values=running" `
    --query "Reservations[*].Instances[*].{InstanceId:InstanceId,InstanceType:InstanceType,AvailabilityZone:Placement.AvailabilityZone,SubnetId:SubnetId,SecurityGroupId:SecurityGroups[0].GroupId,PrivateIpAddress:PrivateIpAddress}" --output json
Write-Host $instancesOut

$asgDesc = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --region $Region --output json | ConvertFrom-Json
if ($asgDesc.AutoScalingGroups -and $asgDesc.AutoScalingGroups.Count -gt 0) {
    $g = $asgDesc.AutoScalingGroups[0]
    $diagnoseResult.asg = @{ desiredCapacity = $g.DesiredCapacity; minSize = $g.MinSize; maxSize = $g.MaxSize; runningCount = $g.Instances.Count }
}
$policyDesc = aws autoscaling describe-policies --auto-scaling-group-name $AsgName --region $Region --output json | ConvertFrom-Json
$videoPolicy = $policyDesc.ScalingPolicies | Where-Object { $_.PolicyName -like "*video*" -or $_.PolicyName -like "*backlog*" } | Select-Object -First 1
if ($videoPolicy) {
    $metricName = $videoPolicy.TargetTrackingConfiguration.CustomizedMetricSpecification.MetricName
    $diagnoseResult.policy = @{ policyName = $videoPolicy.PolicyName; metricName = $metricName; targetValue = $videoPolicy.TargetTrackingConfiguration.TargetValue }
}

# First instance for sections 7–9
$firstSubnetId = $null
$firstSgId = $null
$vpcId = $null
$instancesRaw = aws ec2 describe-instances --region $Region --filters "Name=tag:aws:autoscaling:groupName,Values=$AsgName" "Name=instance-state-name,Values=running" --output json
$instancesObj = $instancesRaw | ConvertFrom-Json
if ($instancesObj.Reservations -and $instancesObj.Reservations.Count -gt 0 -and $instancesObj.Reservations[0].Instances -and $instancesObj.Reservations[0].Instances.Count -gt 0) {
    $firstInstance = $instancesObj.Reservations[0].Instances[0]
    $firstSubnetId = $firstInstance.SubnetId
    $firstSgId = $firstInstance.SecurityGroups[0].GroupId
}

# ------------------------------------------------------------------------------
# 7) Worker Network Path to SQS (first worker)
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 7. Worker Network Path to SQS (first worker) =========="
if (-not $firstSubnetId) {
    Write-Host "No running instance; skip."
} else {
    Write-Host "[Subnet] $firstSubnetId"
    $subnetInfo = aws ec2 describe-subnets --subnet-ids $firstSubnetId --region $Region --output json
    Write-Host $subnetInfo
    $vpcId = aws ec2 describe-subnets --subnet-ids $firstSubnetId --region $Region --query "Subnets[0].VpcId" --output text
    Write-Host "[Route table for subnet]"
    $rtId = aws ec2 describe-route-tables --region $Region --filters "Name=association.subnet-id,Values=$firstSubnetId" --query "RouteTables[0].RouteTableId" --output text
    if ($rtId -and $rtId -ne "None") {
        & aws ec2 describe-route-tables --route-table-ids $rtId --region $Region --output json
        Write-Host "[0.0.0.0/0 route]"
        & aws ec2 describe-route-tables --route-table-ids $rtId --region $Region --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0']" --output json
    }
    Write-Host "[VPC Endpoint com.amazonaws.$Region.sqs]"
    & aws ec2 describe-vpc-endpoints --region $Region --filters "Name=vpc-id,Values=$vpcId" "Name=service-name,Values=com.amazonaws.$Region.sqs" --output json
}

# ------------------------------------------------------------------------------
# 8) Endpoint SG Rules (each SQS endpoint SG)
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 8. Endpoint SG Rules =========="
$epSgIds = @()
if ($vpcId) {
    $vpceJson = aws ec2 describe-vpc-endpoints --region $Region --filters "Name=vpc-id,Values=$vpcId" "Name=service-name,Values=com.amazonaws.$Region.sqs" --output json
    Write-Host "[VPC Endpoints SQS - full]"
    Write-Host $vpceJson
    $vpceObj = $vpceJson | ConvertFrom-Json
    if ($vpceObj.VpcEndpoints) {
        foreach ($ep in $vpceObj.VpcEndpoints) {
            Write-Host "[Endpoint $($ep.VpcEndpointId) EndpointType=$($ep.VpcEndpointType) PrivateDnsEnabled=$($ep.PrivateDnsEnabled)]"
            if ($ep.Groups) { foreach ($g in $ep.Groups) { $epSgIds += $g.GroupId } }
        }
    }
}
foreach ($sgId in $epSgIds) {
    Write-Host "[Endpoint SG $sgId - Inbound]"
    & aws ec2 describe-security-groups --group-ids $sgId --region $Region --query "SecurityGroups[0].IpPermissions" --output json
    Write-Host "[Endpoint SG $sgId - Outbound]"
    & aws ec2 describe-security-groups --group-ids $sgId --region $Region --query "SecurityGroups[0].IpPermissionsEgress" --output json
}

# ------------------------------------------------------------------------------
# 9) Worker SG Outbound (TCP 443 to VPCE SG)
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 9. Worker SG Rules (outbound) =========="
if ($firstSgId) {
    Write-Host "[Worker SG $firstSgId - Outbound]"
    & aws ec2 describe-security-groups --group-ids $firstSgId --region $Region --query "SecurityGroups[0].IpPermissionsEgress" --output json
} else {
    Write-Host "No worker instance; skip."
}

# ------------------------------------------------------------------------------
# 10) Spot Quota Usage (L-34B43A08 + AWS/Usage ResourceCount)
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "========== 10. Spot Quota Usage =========="
Write-Host "[Quota L-34B43A08]"
& aws service-quotas get-service-quota --service-code ec2 --quota-code L-34B43A08 --region $Region --output json
Write-Host "[Usage AWS/Usage ResourceCount Class=Standard/Spot last 2h]"
$uStart = (Get-Date).AddHours(-2).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$uEnd = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$spotUsageRaw = aws cloudwatch get-metric-statistics --region $Region --namespace "AWS/Usage" --metric-name "ResourceCount" `
    --dimensions Name=Service,Value=EC2 Name=Type,Value=Resource Name=Resource,Value=vCPU Name=Class,Value=Standard/Spot `
    --start-time $uStart --end-time $uEnd --period 300 --statistics Maximum Average --output json
Write-Host $spotUsageRaw

# ------------------------------------------------------------------------------
# 11) Save result JSON + Summary
# ------------------------------------------------------------------------------
$diagnoseResult.summary = @{
    sqs_visible    = if ($diagnoseResult.sqs) { $diagnoseResult.sqs.visible } else { $null }
    sqs_notVisible = if ($diagnoseResult.sqs) { $diagnoseResult.sqs.notVisible } else { $null }
    sqs_total      = if ($diagnoseResult.sqs) { $diagnoseResult.sqs.total } else { $null }
    asg_desired    = if ($diagnoseResult.asg) { $diagnoseResult.asg.desiredCapacity } else { $null }
    asg_min_max    = if ($diagnoseResult.asg) { "$($diagnoseResult.asg.minSize)/$($diagnoseResult.asg.maxSize)" } else { $null }
    scaling_metric = if ($diagnoseResult.policy) { $diagnoseResult.policy.metricName } else { $null }
    lambda_total   = if ($diagnoseResult.lambda -and $null -ne $diagnoseResult.lambda.video_queue_depth_total) { $diagnoseResult.lambda.video_queue_depth_total } else { $null }
}

$resultPath = Join-Path $repoRoot ("diagnose_result_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$diagnoseResultJson = $diagnoseResult | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($resultPath, $diagnoseResultJson, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "========== SUMMARY (saved to $resultPath) ==========" -ForegroundColor Green
Write-Host "SQS: visible=$($diagnoseResult.summary.sqs_visible) notVisible=$($diagnoseResult.summary.sqs_notVisible) total=$($diagnoseResult.summary.sqs_total)"
Write-Host "ASG: desired=$($diagnoseResult.summary.asg_desired) min/max=$($diagnoseResult.summary.asg_min_max)"
Write-Host "Scaling metric: $($diagnoseResult.summary.scaling_metric) (expected: VideoQueueDepthTotal for SQS-based scaling)"
Write-Host "Lambda video_queue_depth_total: $($diagnoseResult.summary.lambda_total)"
if ($diagnoseResult.activities -and $diagnoseResult.activities.count -gt 0) {
    Write-Host "Recent activities: $($diagnoseResult.activities.count)"
}

Write-Host ""
Write-Host "========== END DIAGNOSTIC =========="
