# ==============================================================================
# Create Ops-only Batch CE and Queue (default_arm64, max 2 vCPU). Same VPC as video CE.
# Security Group: Uses the SAME security group as academy-video-batch-ce (no new SG).
# Ops jobs (reconcile, scan_stuck, netprobe) submit to academy-video-ops-queue only.
# Usage: .\scripts\infra\batch_ops_setup.ps1 -Region ap-northeast-2
#        .\scripts\infra\batch_ops_setup.ps1 -Region ap-northeast-2 -Verbose
# If VpcId/SubnetIds/SecurityGroupId omitted, discovers from existing academy-video-batch-ce.
# ==============================================================================

[CmdletBinding()]
param(
    [string]$Region = "ap-northeast-2",
    [string]$VpcId = "",
    [string[]]$SubnetIds = @(),
    [string]$SecurityGroupId = "",
    [string]$ComputeEnvName = "academy-video-ops-ce",
    [string]$JobQueueName = "academy-video-ops-queue",
    [string]$VideoCeNameForDiscovery = "academy-video-batch-ce-final"
)
# SSOT v3 Legacy kill-switch: 직접 실행 금지. scripts_v3/deploy.ps1 만 사용.
if (-not $env:ALLOW_LEGACY_IMPORT) { throw "DEPRECATED: Use scripts_v3/deploy.ps1" }
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"

function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

function Invoke-Aws {
    param([string[]]$ArgsArray, [string]$ErrorMessage = "AWS CLI failed")
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    if ($exit -ne 0) {
        $text = ($out | Out-String).Trim()
        throw "${ErrorMessage}. ExitCode=$exit. Output: $text"
    }
    return $out
}

function Get-ComputeEnvironmentArn {
    param([string]$Name)
    $ce = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $Name, "--region", $Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments) { return $null }
    $obj = $ce.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $Name } | Select-Object -First 1
    if (-not $obj) { return $null }
    return $obj.computeEnvironmentArn
}

function Get-JobQueueArn {
    param([string]$Name)
    $jq = ExecJson @("batch", "describe-job-queues", "--job-queues", $Name, "--region", $Region, "--output", "json")
    if (-not $jq -or -not $jq.jobQueues) { return $null }
    $q = $jq.jobQueues | Where-Object { $_.jobQueueName -eq $Name } | Select-Object -First 1
    if (-not $q) { return $null }
    return $q.jobQueueArn
}

Write-Host "== Ops Batch Setup (academy-video-ops-ce / academy-video-ops-queue) ==" -ForegroundColor Cyan

# Resolve VpcId, SubnetIds, SecurityGroupId from existing video CE if not provided
# Tries VideoCeNameForDiscovery first (e.g. academy-video-batch-ce-final), then academy-video-batch-ce.
# Then filters to "working" subnets only (NAT/IGW outbound or full VPCE set).
if (-not $VpcId -or $SubnetIds.Count -eq 0 -or -not $SecurityGroupId) {
    $videoCeObj = $null
    foreach ($ceName in @($VideoCeNameForDiscovery, "academy-video-batch-ce")) {
        if ([string]::IsNullOrWhiteSpace($ceName)) { continue }
        $videoCe = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $ceName, "--region", $Region, "--output", "json")
        if (-not $videoCe -or -not $videoCe.computeEnvironments) { continue }
        $videoCeObj = $videoCe.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ceName } | Select-Object -First 1
        if (-not $videoCeObj) { continue }
        if ($videoCeObj.status -eq "VALID") { break }
        $wait = 0
        while ($wait -lt 90 -and $videoCeObj.status -ne "VALID") {
            Start-Sleep -Seconds 5
            $wait += 5
            $videoCe = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $ceName, "--region", $Region, "--output", "json")
            if ($videoCe -and $videoCe.computeEnvironments) {
                $videoCeObj = $videoCe.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ceName } | Select-Object -First 1
            }
            if (-not $videoCeObj -or $videoCeObj.status -eq "INVALID") { break }
        }
        if ($videoCeObj -and $videoCeObj.status -eq "VALID") { break }
    }
    if (-not $videoCeObj -or $videoCeObj.status -ne "VALID") {
        Write-Host "FAIL: Video CE ($VideoCeNameForDiscovery / academy-video-batch-ce) not found or not VALID. Create video Batch first or pass -VpcId -SubnetIds -SecurityGroupId." -ForegroundColor Red
        exit 1
    }
    $cr = $videoCeObj.computeResources
    if (-not $SecurityGroupId -and $cr.securityGroupIds -and $cr.securityGroupIds.Count -gt 0) { $SecurityGroupId = $cr.securityGroupIds[0] }
    if ($SubnetIds.Count -eq 0 -and $cr.subnets) { $SubnetIds = @($cr.subnets) }
    if (-not $VpcId -and $SubnetIds.Count -gt 0) {
        $subResp = ExecJson @("ec2", "describe-subnets", "--subnet-ids", $SubnetIds[0], "--region", $Region, "--output", "json")
        if ($subResp.Subnets) { $VpcId = $subResp.Subnets[0].VpcId }
    }
}

# Subnet selection: only use subnets with working outbound (NAT/IGW or full VPCE)
function Test-SubnetHasOutbound {
    param([string]$SubId, [string]$Reg)
    $rt = ExecJson @("ec2", "describe-route-tables", "--filters", "Name=association.subnet-id,Values=$SubId", "--region", $Reg, "--output", "json")
    if (-not $rt -or -not $rt.RouteTables -or $rt.RouteTables.Count -eq 0) { return $false }
    $routes = $rt.RouteTables[0].Routes
    foreach ($r in $routes) {
        if ($r.DestinationCidrBlock -eq "0.0.0.0/0" -and $r.GatewayId) {
            if ($r.GatewayId.StartsWith("nat-") -or $r.GatewayId.StartsWith("igw-")) { return $true }
        }
    }
    return $false
}
function Test-VpcHasRequiredVpce {
    param([string]$Vpc, [string]$Reg)
    $vpce = ExecJson @("ec2", "describe-vpc-endpoints", "--filters", "Name=vpc-id,Values=$Vpc", "--region", $Reg, "--output", "json")
    if (-not $vpce -or -not $vpce.VpcEndpoints) { return $false }
    $serviceNames = $vpce.VpcEndpoints | ForEach-Object { $_.ServiceName }
    $suffixes = @("ecr.api", "ecr.dkr", "ecs", "ecs-telemetry", "logs", "ssm", "ec2messages", "ssmmessages")
    foreach ($suf in $suffixes) {
        $has = $false
        foreach ($sn in $serviceNames) {
            if ($sn -and $sn.EndsWith($suf)) { $has = $true; break }
        }
        if (-not $has) { return $false }
    }
    return $true
}

$WorkingSubnetIds = @()
foreach ($sid in $SubnetIds) {
    if (Test-SubnetHasOutbound -SubId $sid -Reg $Region) { $WorkingSubnetIds += $sid }
}
if ($WorkingSubnetIds.Count -eq 0) {
    if (Test-VpcHasRequiredVpce -Vpc $VpcId -Reg $Region) {
        $WorkingSubnetIds = @($SubnetIds)
        Write-Host "  No NAT/IGW outbound; using all subnets (VPCE set present)." -ForegroundColor Gray
    } else {
        Write-Host "FAIL: No subnet has 0.0.0.0/0 to NAT or IGW, and VPC does not have required VPC Endpoints (ecr.api, ecr.dkr, ecs, ecs-telemetry, logs, ssm, ec2messages, ssmmessages). Add NAT/IGW or create VPCEs." -ForegroundColor Red
        exit 1
    }
} else {
    $SubnetIds = @($WorkingSubnetIds)
    Write-Host "  Using $($WorkingSubnetIds.Count) subnet(s) with outbound (NAT/IGW)." -ForegroundColor Gray
}
$SubnetIds = @($WorkingSubnetIds)
if (-not $VpcId -or $SubnetIds.Count -eq 0 -or -not $SecurityGroupId) {
    Write-Host "FAIL: Could not resolve VpcId, SubnetIds, SecurityGroupId. Pass explicitly or ensure Video CE ($VideoCeNameForDiscovery / academy-video-batch-ce) exists." -ForegroundColor Red
    exit 1
}
# Ensure SG egress allows 0.0.0.0/0 (reuse Batch SG; add if missing)
$sgDesc = ExecJson @("ec2", "describe-security-groups", "--group-ids", $SecurityGroupId, "--region", $Region, "--output", "json")
$hasEgressAll = $false
if ($sgDesc -and $sgDesc.SecurityGroups -and $sgDesc.SecurityGroups.Count -gt 0) {
    $egress = $sgDesc.SecurityGroups[0].IpPermissionsEgress
    foreach ($e in $egress) {
        if ($e.IpProtocol -eq "-1" -or $e.IpProtocol -eq "all") {
            $hasCidr = $e.IpRanges | Where-Object { $_.CidrIp -eq "0.0.0.0/0" }
            if ($hasCidr) { $hasEgressAll = $true; break }
        }
    }
}
if (-not $hasEgressAll) {
    Write-Host "  Adding egress 0.0.0.0/0 to SG $SecurityGroupId" -ForegroundColor Yellow
    try { Invoke-Aws -ArgsArray @("ec2", "authorize-security-group-egress", "--group-id", $SecurityGroupId, "--protocol", "all", "--cidr", "0.0.0.0/0", "--region", $Region) -ErrorMessage "authorize-security-group-egress failed" } catch {
    }
}
Write-Host "  VpcId=$VpcId SubnetIds=$($SubnetIds -join ',')" -ForegroundColor Gray
Write-Host "  SecurityGroupId=$SecurityGroupId $(if ($videoCeObj) { "(from $($videoCeObj.computeEnvironmentName))" } else { "" })" -ForegroundColor Gray

# IAM (same as video CE)
$BatchServiceRoleName = "academy-batch-service-role"
$InstanceProfileName = "academy-batch-ecs-instance-profile"
$serviceRoleArn = (ExecJson @("iam", "get-role", "--role-name", $BatchServiceRoleName, "--output", "json")).Role.Arn
$instanceProfileArn = (ExecJson @("iam", "get-instance-profile", "--instance-profile-name", $InstanceProfileName, "--output", "json")).InstanceProfile.Arn
if (-not $serviceRoleArn -or -not $instanceProfileArn) {
    Write-Host "FAIL: IAM role $BatchServiceRoleName or instance profile $InstanceProfileName not found. Run batch_video_setup first." -ForegroundColor Red
    exit 1
}

# Create Ops CE (instanceTypes: default_arm64 per region; create or update state)
Write-Host "`n[1] Compute Environment: $ComputeEnvName" -ForegroundColor Cyan
Write-Host "  (instanceTypes: default_arm64, max 2 vCPU, On-Demand)" -ForegroundColor Gray
$ceJsonPath = Join-Path $InfraPath "batch\ops_compute_env.json"
$ceContent = Get-Content $ceJsonPath -Raw
$ceContent = $ceContent -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $serviceRoleArn
$ceContent = $ceContent -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $instanceProfileArn
$ceContent = $ceContent -replace "PLACEHOLDER_SECURITY_GROUP_ID", $SecurityGroupId
$subnetArr = ($SubnetIds | ForEach-Object { "`"$_`"" }) -join ","
$ceContent = $ceContent -replace '"PLACEHOLDER_SUBNET_1"', $subnetArr
$ceFile = Join-Path $RepoRoot "batch_ops_ce_temp.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($ceFile, $ceContent, $utf8NoBom)
$ceFileUri = "file://" + ($ceFile -replace '\\', '/')

$ce = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $ComputeEnvName, "--region", $Region, "--output", "json")
$ceObj = $ce.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
if ($ceObj) {
    if ($ceObj.state -eq "DISABLED") {
        Write-Host "  Compute environment exists but DISABLED; enabling." -ForegroundColor Yellow
        try {
            Invoke-Aws -ArgsArray @("batch", "update-compute-environment", "--compute-environment", $ComputeEnvName, "--state", "ENABLED", "--region", $Region) -ErrorMessage "update-compute-environment failed"
        } catch {
            Write-Host "  FAIL: $_" -ForegroundColor Red
            Remove-Item $ceFile -Force -ErrorAction SilentlyContinue
            throw
        }
    } else {
        Write-Host "  Compute environment exists; skipping create." -ForegroundColor Gray
    }
} else {
    Write-Host "  Creating compute environment (default_arm64, max 2 vCPU)" -ForegroundColor Yellow
    try {
        Invoke-Aws -ArgsArray @("batch", "create-compute-environment", "--cli-input-json", $ceFileUri, "--region", $Region) -ErrorMessage "create-compute-environment failed"
    } catch {
        if ($PSCmdlet.BoundParameters.ContainsKey("Verbose") -and $PSCmdlet.BoundParameters["Verbose"]) {
            Write-Verbose "Request payload file: $ceFile"
            Write-Verbose "Payload content:"
            Write-Verbose ([System.IO.File]::ReadAllText($ceFile, [System.Text.UTF8Encoding]::new($false)))
        }
        Write-Host "  FAIL: $_" -ForegroundColor Red
        Remove-Item $ceFile -Force -ErrorAction SilentlyContinue
        throw
    }
}
Remove-Item $ceFile -Force -ErrorAction SilentlyContinue

Write-Host "  Waiting for compute environment VALID..." -ForegroundColor Gray
$wait = 0
while ($wait -lt 120) {
    $ce2 = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $ComputeEnvName, "--region", $Region, "--output", "json")
    $obj = $ce2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
    $status = $obj.status
    if ($status -eq "VALID") { break }
    if ($status -eq "INVALID") { Write-Host "  FAIL: CE state INVALID." -ForegroundColor Red; exit 1 }
    Start-Sleep -Seconds 5
    $wait += 5
}
$ceArn = Get-ComputeEnvironmentArn -Name $ComputeEnvName
if (-not $ceArn) { Write-Host "  FAIL: Could not get CE ARN." -ForegroundColor Red; exit 1 }
Write-Host "  CE ARN: $ceArn" -ForegroundColor Green

# Create Ops Queue
Write-Host "`n[2] Job Queue: $JobQueueName" -ForegroundColor Cyan
$jqPath = Join-Path $InfraPath "batch\ops_job_queue.json"
$jqContent = Get-Content $jqPath -Raw
$jqContent = $jqContent -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $ceArn
$jqTempFile = Join-Path $RepoRoot "batch_ops_jq_temp.json"
[System.IO.File]::WriteAllText($jqTempFile, $jqContent, $utf8NoBom)
$jqTempUri = "file://" + ($jqTempFile -replace '\\', '/')

$jq = ExecJson @("batch", "describe-job-queues", "--job-queues", $JobQueueName, "--region", $Region, "--output", "json")
$queueExists = $jq -and ($jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName })
if (-not $queueExists) {
    Write-Host "  Creating job queue" -ForegroundColor Yellow
    try {
        Invoke-Aws -ArgsArray @("batch", "create-job-queue", "--cli-input-json", $jqTempUri, "--region", $Region) -ErrorMessage "create-job-queue failed"
    } catch {
        Write-Host "  FAIL: $_" -ForegroundColor Red
        Remove-Item $jqTempFile -Force -ErrorAction SilentlyContinue
        throw
    }
} else {
    Write-Host "  Job queue exists." -ForegroundColor Gray
}
Remove-Item $jqTempFile -Force -ErrorAction SilentlyContinue

$queueArn = Get-JobQueueArn -Name $JobQueueName
if (-not $queueArn) { Write-Host "  FAIL: Job queue not found after create." -ForegroundColor Red; exit 1 }
Write-Host "  Queue ARN: $queueArn" -ForegroundColor Green

# Write state
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$opsState = @{
    OpsComputeEnvName = $ComputeEnvName
    OpsComputeEnvArn  = $ceArn
    OpsJobQueueName  = $JobQueueName
    OpsJobQueueArn   = $queueArn
}
$opsStatePath = Join-Path $OutDir "batch_ops_state.json"
[System.IO.File]::WriteAllText($opsStatePath, ($opsState | ConvertTo-Json), $utf8NoBom)
Write-Host "`n  Wrote $opsStatePath" -ForegroundColor Gray
Write-Host "`nDONE. Ops CE and queue ready. Use -OpsJobQueueName $JobQueueName when deploying EventBridge." -ForegroundColor Green
