# ==============================================================================
# Video 워커 / ASG / SQS / Lambda / 네트워크 원인 점검 (한 번에)
# 사용: .\scripts\diagnose_video_worker.ps1
#      .\scripts\diagnose_video_worker.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$QueueName = "academy-video-jobs",
    [string]$LambdaName = "academy-worker-queue-depth-metric"
)

$ErrorActionPreference = "Continue"
$out = @()

function Write-Section { param([string]$Title) $script:out += ""; $script:out += "========== $Title ==========" }
function Write-Line  { param([string]$Text) $script:out += $Text }
function Write-Result { param([string]$Key, [string]$Value, [string]$Status = "") 
    $s = if ($Status) { " [$Status]" } else { "" }
    $script:out += "  $Key : $Value$s"
}

# ------------------------------------------------------------------------------
# 1) Lambda (BacklogCount 소스)
# ------------------------------------------------------------------------------
Write-Section "1. Lambda (queue-depth-metric)"
$lambdaResp = $null
try {
    $invokeOut = Join-Path $PSScriptRoot ".." "response.json"
    aws lambda invoke --function-name $LambdaName --region $Region $invokeOut 2>$null | Out-Null
    $lambdaResp = Get-Content $invokeOut -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
} catch {}
if ($lambdaResp) {
    $vc = $lambdaResp.video_backlog_count
    if ($null -eq $vc) { Write-Line "  video_backlog_count: null  [WARN] API 403 or skip -> BacklogCount not published" }
    else { Write-Line "  video_backlog_count: $vc  [OK]" }
    Write-Line "  ai_queue_depth: $($lambdaResp.ai_queue_depth) | video_queue_depth: $($lambdaResp.video_queue_depth) | messaging: $($lambdaResp.messaging_queue_depth)"
} else {
    Write-Line "  Lambda invoke failed or no response  [FAIL]"
}

# ------------------------------------------------------------------------------
# 2) CloudWatch BacklogCount (최근 15분)
# ------------------------------------------------------------------------------
Write-Section "2. CloudWatch BacklogCount (recent 15 min)"
$start = (Get-Date).AddMinutes(-15).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$end   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$cw = aws cloudwatch get-metric-statistics --region $Region --namespace "Academy/VideoProcessing" --metric-name BacklogCount `
    --dimensions Name=WorkerType,Value=Video Name=AutoScalingGroupName,Value=$AsgName `
    --start-time $start --end-time $end --period 60 --statistics Average Maximum --output json 2>$null | ConvertFrom-Json
if ($cw -and $cw.Datapoints -and $cw.Datapoints.Count -gt 0) {
    $max = ($cw.Datapoints | Measure-Object -Property Maximum -Maximum).Maximum
    $avg = ($cw.Datapoints | ForEach-Object { $_.Average } | Measure-Object -Average).Average
    Write-Line "  Datapoints: $($cw.Datapoints.Count) | Max: $max | Avg: $([math]::Round($avg,2))"
} else {
    Write-Line "  No datapoints  [WARN] Lambda not publishing or metric name/dimensions mismatch"
}

# ------------------------------------------------------------------------------
# 3) ASG
# ------------------------------------------------------------------------------
Write-Section "3. ASG ($AsgName)"
$asg = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --region $Region --query "AutoScalingGroups[0]" --output json 2>$null | ConvertFrom-Json
if ($asg) {
    Write-Line "  DesiredCapacity: $($asg.DesiredCapacity) | MinSize: $($asg.MinSize) | MaxSize: $($asg.MaxSize)"
    $instCount = if ($asg.Instances) { $asg.Instances.Count } else { 0 }
    Write-Line "  Running instances: $instCount"
    $hasMixed = $asg.MixedInstancesPolicy -and $asg.MixedInstancesPolicy.LaunchTemplate
    Write-Line "  MixedInstancesPolicy: $(if ($hasMixed) { 'Yes (c6g/t4g Spot)' } else { 'No' })"
    $vpcZone = $asg.VpcZoneIdentifier
    if ($vpcZone) { Write-Line "  VpcZoneIdentifier: $vpcZone" }
    $policy = aws autoscaling describe-policies --auto-scaling-group-name $AsgName --policy-names "video-backlogcount-tt" --region $Region --query "ScalingPolicies[0].TargetTrackingConfiguration.TargetValue" --output text 2>$null
    if ($policy) { Write-Line "  TargetTracking TargetValue: $policy (backlog per worker)" }
} else {
    Write-Line "  ASG not found  [FAIL]"
}

# ------------------------------------------------------------------------------
# 4) SQS
# ------------------------------------------------------------------------------
Write-Section "4. SQS ($QueueName)"
$qurl = aws sqs get-queue-url --queue-name $QueueName --region $Region --query "QueueUrl" --output text 2>$null
if ($qurl) {
    $attr = aws sqs get-queue-attributes --queue-url $qurl --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region $Region --output json 2>$null | ConvertFrom-Json
    $vis = $attr.Attributes.ApproximateNumberOfMessages
    $inv = $attr.Attributes.ApproximateNumberOfMessagesNotVisible
    Write-Line "  Visible (waiting): $vis | NotVisible (in flight): $inv"
    if ([int]$vis -gt 0 -and [int]$inv -eq 0) { Write-Line "  [WARN] Messages in queue but none in flight -> workers may not be receiving (e.g. SQS connect timeout)" }
} else {
    Write-Line "  Queue not found  [FAIL]"
}

# ------------------------------------------------------------------------------
# 5) Spot Quota (Standard Spot vCPU)
# ------------------------------------------------------------------------------
Write-Section "5. Spot Quota (Standard Spot vCPU)"
$quota = aws service-quotas get-service-quota --service-code ec2 --quota-code L-34B43A08 --region $Region --output json 2>$null | ConvertFrom-Json
if ($quota.Quota) {
    Write-Line "  L-34B43A08: $($quota.Quota.Value) vCPU (Standard Spot)"
}

# ------------------------------------------------------------------------------
# 6) ASG Scaling Activities (최근 실패 위주)
# ------------------------------------------------------------------------------
Write-Section "6. ASG Scaling Activities (recent, Failed highlighted)"
$acts = aws autoscaling describe-scaling-activities --auto-scaling-group-name $AsgName --region $Region --max-items 15 --output json 2>$null | ConvertFrom-Json
if ($acts.Activities) {
    foreach ($a in $acts.Activities) {
        $status = $a.StatusCode
        $time = $a.StartTime
        $desc = if ($a.Description) { $a.Description } else { "" }
        if ($desc.Length -gt 80) { $desc = $desc.Substring(0, 77) + "..." }
        $mark = if ($status -eq "Failed") { " [FAIL]" } else { "" }
        Write-Line "  $time | $status$mark | $desc"
    }
} else {
    Write-Line "  No activities"
}

# ------------------------------------------------------------------------------
# 7) Running Video Worker Instances
# ------------------------------------------------------------------------------
Write-Section "7. Running Video Worker Instances"
$instances = aws ec2 describe-instances --region $Region --filters "Name=tag:aws:autoscaling:groupName,Values=$AsgName" "Name=instance-state-name,Values=running" `
    --query "Reservations[*].Instances[*].{Id:InstanceId,Type:InstanceType,Az:Placement.AvailabilityZone,Subnet:SubnetId,Sg:SecurityGroups[0].GroupId}" --output json 2>$null | ConvertFrom-Json
if ($instances -and $instances.Count -gt 0) {
    $flat = @(); foreach ($r in $instances) { foreach ($i in $r) { $flat += $i } }
    foreach ($i in $flat) {
        Write-Line "  $($i.Id) | $($i.Type) | $($i.Az) | subnet $($i.Subnet) | sg $($i.Sg)"
    }
    $firstSubnet = $flat[0].Subnet
    $firstSg = $flat[0].Sg
} else {
    Write-Line "  No running instances"
    $firstSubnet = $null
    $firstSg = $null
}

# ------------------------------------------------------------------------------
# 8) Network (첫 번째 워커 서브넷 기준: 라우트, SQS 엔드포인트, SG 아웃바운드)
# ------------------------------------------------------------------------------
Write-Section "8. Network (worker subnet: route to SQS, SG outbound)"
if ($firstSubnet) {
    $vpcId = aws ec2 describe-subnets --subnet-ids $firstSubnet --region $Region --query "Subnets[0].VpcId" --output text 2>$null
    $rtAssoc = aws ec2 describe-route-tables --region $Region --filters "Name=association.subnet-id,Values=$firstSubnet" --query "RouteTables[0].RouteTableId" --output text 2>$null
    if ($rtAssoc -and $rtAssoc -ne "None") {
        $routes = aws ec2 describe-route-tables --route-table-ids $rtAssoc --region $Region --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0']" --output json 2>$null | ConvertFrom-Json
        $nat = $routes | Where-Object { $_.GatewayId -or $_.NatGatewayId } | Select-Object -First 1
        if ($nat.NatGatewayId) { Write-Line "  Route 0.0.0.0/0 -> NatGatewayId: $($nat.NatGatewayId)  [OK]" }
        elseif ($nat.GatewayId -and $nat.GatewayId -like "igw-*") { Write-Line "  Route 0.0.0.0/0 -> Internet Gateway (public subnet)" }
        else { Write-Line "  No 0.0.0.0/0 route  [FAIL] -> SQS connect timeout likely (no NAT/IGW)" }
    }
    $endpoints = aws ec2 describe-vpc-endpoints --region $Region --filters "Name=vpc-id,Values=$vpcId" "Name=service-name,Values=com.amazonaws.$Region.sqs" --query "VpcEndpoints[*].VpcEndpointId" --output text 2>$null
    if ($endpoints) { Write-Line "  SQS VPC Endpoint(s): $endpoints" }
    else { Write-Line "  No SQS VPC Endpoint -> traffic goes via NAT to public SQS" }
    if ($firstSg) {
        $sgOut = aws ec2 describe-security-groups --group-ids $firstSg --region $Region --query "SecurityGroups[0].IpPermissionsEgress[?ToPort==\`"443\`" || ToPort==null]" --output json 2>$null | ConvertFrom-Json
        if ($sgOut) { Write-Line "  SG $firstSg outbound: has rule (443 or all)" }
        else { Write-Line "  SG $firstSg: no 443 outbound  [WARN]" }
    }
} else {
    Write-Line "  No instance -> skip network check"
}

# ------------------------------------------------------------------------------
# 9) Lambda 최근 로그 (403 등)
# ------------------------------------------------------------------------------
Write-Section "9. Lambda recent errors (VIDEO_BACKLOG)"
$logGroup = "/aws/lambda/$LambdaName"
$since = (Get-Date).AddMinutes(-30).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$events = aws logs filter-log-events --log-group-name $logGroup --region $Region --start-time ([DateTimeOffset]::Parse($since).ToUnixTimeMilliseconds()) --filter-pattern "ERROR" --max-items 5 --output json 2>$null | ConvertFrom-Json
if ($events.events) {
    foreach ($e in $events.events) {
        $msg = $e.message
        if ($msg.Length -gt 120) { $msg = $msg.Substring(0, 117) + "..." }
        Write-Line "  $msg"
    }
} else {
    Write-Line "  No recent ERROR logs"
}

# ------------------------------------------------------------------------------
# 출력
# ------------------------------------------------------------------------------
$out += ""
$out += "========== 요약 (원인 후보) =========="
$out += "  - video_backlog_count null -> Lambda 403: INTERNAL_API_ALLOW_IPS, LAMBDA_INTERNAL_API_KEY 확인"
$out += "  - SQS visible>0, in_flight=0 -> 워커가 메시지 안 받음: 8번 네트워크(NAT/라우트), 워커 docker 로그 Connect timeout"
$out += "  - Scaling Failed MaxSpotInstanceCountExceeded -> Spot 쿼터(5번) 또는 계정 한도"
$out += "  - Desired 비정상(예: 20) -> TargetValue 확인(3번), 과거 0.25 등"
$out += ""

$out | ForEach-Object { Write-Host $_ }
