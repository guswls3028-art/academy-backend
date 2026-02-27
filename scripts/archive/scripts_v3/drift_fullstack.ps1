# ==============================================================================
# FullStack Drift — compare actual AWS to params.yaml (env/prod.ps1), output 3-class table.
# Output: console + docs/00-SSOT/FULLSTACK-DRIFT-TABLE.md
# Usage: .\scripts_v3\drift_fullstack.ps1 [-Region ap-northeast-2]
# ==============================================================================
[CmdletBinding()]
param([string]$Region = "ap-northeast-2")
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..")).Path
. (Join-Path $ScriptRoot "core\aws-wrapper.ps1")
. (Join-Path $ScriptRoot "env\prod.ps1")
$script:Region = $Region
$R = $Region

$drifts = [System.Collections.ArrayList]::new()

function Add-Drift {
    param([string]$Resource, [string]$Item, [string]$Expected, [string]$Actual, [string]$Classification)
    [void]$drifts.Add([PSCustomObject]@{ Resource = $Resource; Item = $Item; Expected = $Expected; Actual = $Actual; Classification = $Classification })
}

# Batch CE names
$ces = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
$ceNames = if ($ces -and $ces.computeEnvironments) { ($ces.computeEnvironments | ForEach-Object { $_.computeEnvironmentName }) -join "," } else { "" }
$wantCE = "$($script:VideoCEName),$($script:OpsCEName)"
if ($ceNames -ne $wantCE) {
    $extra = $ceNames -replace [regex]::Escape($script:VideoCEName), "" -replace [regex]::Escape($script:OpsCEName), "" -replace ",+", "," -trim ","
    if ($extra) { Add-Drift "Batch" "CE name" $wantCE $ceNames "Manual check (non-SSOT CE exists)" }
    if (-not $ceNames.Contains($script:VideoCEName)) { Add-Drift "Batch" "Video CE" $script:VideoCEName "missing" "Recreate required" }
    if (-not $ceNames.Contains($script:OpsCEName)) { Add-Drift "Batch" "Ops CE" $script:OpsCEName "missing" "Recreate required" }
}

# Batch Queue
$queues = Invoke-AwsJson @("batch", "describe-job-queues", "--region", $R, "--output", "json")
$qNames = if ($queues -and $queues.jobQueues) { ($queues.jobQueues | ForEach-Object { $_.jobQueueName }) -join "," } else { "" }
if (-not $qNames.Contains($script:VideoQueueName)) { Add-Drift "Batch" "Video Queue" $script:VideoQueueName "missing" "Recreate required" }
if (-not $qNames.Contains($script:OpsQueueName)) { Add-Drift "Batch" "Ops Queue" $script:OpsQueueName "missing" "Recreate required" }

# EventBridge rule names
$rules = Invoke-AwsJson @("events", "list-rules", "--region", $R, "--output", "json")
$ruleNames = if ($rules -and $rules.Rules) { ($rules.Rules | ForEach-Object { $_.Name }) -join "," } else { "" }
if (-not $ruleNames.Contains($script:EventBridgeReconcileRule)) { Add-Drift "EventBridge" "reconcile rule" $script:EventBridgeReconcileRule "missing" "Updatable (put-rule)" }
if (-not $ruleNames.Contains($script:EventBridgeScanStuckRule)) { Add-Drift "EventBridge" "scan_stuck rule" $script:EventBridgeScanStuckRule "missing" "Updatable (put-rule)" }

# ASG names
$asg = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
$asgNames = if ($asg -and $asg.AutoScalingGroups) { ($asg.AutoScalingGroups | ForEach-Object { $_.AutoScalingGroupName }) -join "," } else { "" }
if (-not $asgNames.Contains($script:MessagingASGName)) { Add-Drift "ASG" "Messaging ASG" $script:MessagingASGName "missing" "Recreate required" }
if (-not $asgNames.Contains($script:AiASGName)) { Add-Drift "ASG" "AI ASG" $script:AiASGName "missing" "Recreate required" }

# API EIP
$eip = Invoke-AwsJson @("ec2", "describe-addresses", "--allocation-ids", $script:ApiAllocationId, "--region", $R, "--output", "json")
$eipInstance = if ($eip -and $eip.Addresses -and $eip.Addresses.Count -gt 0) { $eip.Addresses[0].InstanceId } else { $null }
if (-not $eipInstance) { Add-Drift "API" "EIP attachment" $script:ApiPublicIp "no instance" "Manual check" }

# Build instance
$buildInst = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=tag:Name,Values=$($script:BuildTagValue)", "Name=instance-state-name,Values=running,pending,stopped", "--region", $R, "--output", "json")
$buildFound = $buildInst -and $buildInst.Reservations -and $buildInst.Reservations.Count -gt 0
if (-not $buildFound) { Add-Drift "Build" "instance" $script:BuildTagValue "missing" "Manual check" }

# RDS
$rds = Invoke-AwsJson @("rds", "describe-db-instances", "--region", $R, "--output", "json")
$db = $rds.DBInstances | Where-Object { $_.DBInstanceIdentifier -eq $script:RdsDbIdentifier } | Select-Object -First 1
if (-not $db) { Add-Drift "RDS" "DB Identifier" $script:RdsDbIdentifier "missing" "Recreate required" }
elseif ($db.DBInstanceStatus -ne "available") { Add-Drift "RDS" "Status" "available" $db.DBInstanceStatus "Manual check" }

# Redis
$redis = Invoke-AwsJson @("elasticache", "describe-replication-groups", "--replication-group-id", $script:RedisReplicationGroupId, "--region", $R, "--output", "json")
$rg = $redis.ReplicationGroups | Where-Object { $_.ReplicationGroupId -eq $script:RedisReplicationGroupId } | Select-Object -First 1
if (-not $rg) { Add-Drift "Redis" "ReplicationGroupId" $script:RedisReplicationGroupId "missing" "Recreate required" }
elseif ($rg.Status -ne "available") { Add-Drift "Redis" "Status" "available" $rg.Status "Manual check" }

# SSM
foreach ($nm in @($script:SsmWorkersEnv, $script:SsmApiEnv)) {
    try {
        $p = Invoke-AwsJson @("ssm", "get-parameter", "--name", $nm, "--region", $R, "--output", "json")
        if (-not $p -or -not $p.Parameter) { Add-Drift "SSM" "Parameter" $nm "missing" "Updatable (put-parameter)" }
    } catch { Add-Drift "SSM" "Parameter" $nm "missing/access failed" "Updatable (put-parameter)" }
}

# ECR repos
foreach ($repo in @($script:VideoWorkerRepo, $script:EcrApiRepo, $script:EcrMessagingRepo, $script:EcrAiRepo)) {
    try {
        $di = Invoke-AwsJson @("ecr", "describe-repositories", "--repository-names", $repo, "--region", $R, "--output", "json")
        if (-not $di -or -not $di.repositories) { Add-Drift "ECR" "Repository" $repo "missing" "Updatable (create-repository)" }
    } catch { Add-Drift "ECR" "Repository" $repo "missing" "Updatable (create-repository)" }
}

# IAM roles (required only)
$requiredRoles = @("academy-batch-service-role", "academy-batch-ecs-instance-role", "academy-batch-ecs-task-execution-role", "academy-video-batch-job-role", "academy-eventbridge-batch-video-role")
$roles = Invoke-AwsJson @("iam", "list-roles", "--output", "json")
$roleNames = $roles.Roles | ForEach-Object { $_.RoleName }
foreach ($rn in $requiredRoles) {
    if ($roleNames -notcontains $rn) { Add-Drift "IAM" "Role" $rn "missing" "Recreate required" }
}

# Network VPC
$vpc = Invoke-AwsJson @("ec2", "describe-vpcs", "--vpc-ids", $script:VpcId, "--region", $R, "--output", "json")
if (-not $vpc -or -not $vpc.Vpcs -or $vpc.Vpcs.Count -eq 0) { Add-Drift "Network" "VPC" $script:VpcId "missing" "Manual check" }

# Output table
$outDir = Join-Path $RepoRoot "docs\00-SSOT"
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# FullStack Drift Table")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**Generated:** $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | **Region:** $R")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Resource | Item | SSOT Expected | Actual | Classification |")
[void]$sb.AppendLine("|----------|------|---------------|--------|----------------|")

foreach ($d in $drifts) {
    $line = "| $($d.Resource) | $($d.Item) | $($d.Expected) | $($d.Actual) | $($d.Classification) |"
    [void]$sb.AppendLine($line)
    Write-Host $line -ForegroundColor $(if ($d.Classification -match "Manual") { "Yellow" } elseif ($d.Classification -match "Recreate") { "Red" } else { "Cyan" })
}

if ($drifts.Count -eq 0) {
    [void]$sb.AppendLine("| - | no drift | - | - | - |")
    Write-Host "No drift (matches SSOT)" -ForegroundColor Green
}

[void]$sb.AppendLine("")
[void]$sb.AppendLine("**Classification:** Updatable = can refresh via API/script | Recreate required = Delete then Create or Ensure | Manual check = verify in console/docs")

$outPath = Join-Path $outDir "FULLSTACK-DRIFT-TABLE.md"
[System.IO.File]::WriteAllText($outPath, $sb.ToString(), [System.Text.UTF8Encoding]::new($false))
Write-Host "`nDrift table written: $outPath" -ForegroundColor Gray
