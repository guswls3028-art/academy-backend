# ==============================================================================
# FACT-BASED AWS NETWORK CONNECTIVITY VERIFICATION
# Video Batch compute environment: VPC / SG / Subnet / connectivity to RDS, Redis, R2, API, ECR, Logs.
# Run with valid AWS credentials: Region ap-northeast-2, Account 809466760795.
# If the current run cannot execute AWS CLI (e.g. invalid security token), run this script
# in an environment with valid AWS credentials to obtain the full report.
# Usage: .\scripts\infra\verify_batch_network_connectivity.ps1 [-ComputeEnvName academy-video-batch-ce]
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$ComputeEnvName = "academy-video-batch-ce",
    [string]$FallbackCE = "academy-video-batch-ce-v3"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)

function Write-Section { param([string]$Title) Write-Host "`n========== $Title ==========" -ForegroundColor Cyan }
function Write-Fact { param([string]$Label, [string]$Value) Write-Host "  $Label : $Value" }

# Resolve CE (prefer production CE academy-video-batch-ce)
$ceList = aws batch describe-compute-environments --region $Region --output json 2>&1
$ceListStr = ($ceList | Out-String).Trim()
$ceListObj = $ceListStr | ConvertFrom-Json
$ce = $ceListObj.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
if (-not $ce) {
    $ce = $ceListObj.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $FallbackCE } | Select-Object -First 1
    if ($ce) { $ComputeEnvName = $FallbackCE }
}
if (-not $ce) {
    Write-Host "FAIL: Compute environment '$ComputeEnvName' or '$FallbackCE' not found." -ForegroundColor Red
    exit 1
}

Write-Section "SECTION 1 — RUNTIME NETWORK CONTEXT"
$cr = $ce.computeResources
$vpcId = $null
$subnetIds = @($cr.subnets)
$sgIds = @($cr.securityGroupIds)
Write-Fact "ComputeEnvironment" $ce.computeEnvironmentName
Write-Fact "State" $ce.state
Write-Fact "Status" $ce.status
Write-Fact "SecurityGroupIds" ($sgIds -join ", ")
Write-Fact "SubnetIds" ($subnetIds -join ", ")
# AssignPublicIp: Batch managed CE uses EC2 launch template; describe does not return it. Check subnet MapPublicIpOnLaunch.
Write-Fact "AssignPublicIp" "CANNOT VERIFY VIA CLI (not in describe-compute-environments output)"

foreach ($subId in $subnetIds) {
    Write-Host "`n  --- Subnet $subId ---" -ForegroundColor Gray
    $sub = aws ec2 describe-subnets --subnet-ids $subId --region $Region --output json | ConvertFrom-Json
    $s = $sub.Subnets[0]
    $vpcId = $s.VpcId
    Write-Fact "VpcId" $s.VpcId
    Write-Fact "MapPublicIpOnLaunch" $s.MapPublicIpOnLaunch
    $rtAssoc = aws ec2 describe-route-tables --filters "Name=association.subnet-id,Values=$subId" --region $Region --output json | ConvertFrom-Json
    if ($rtAssoc.RouteTables.Count -eq 0) {
        $rtMain = aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$vpcId" "Name=association.main,Values=true" --region $Region --output json | ConvertFrom-Json
        $rtAssoc = $rtMain
    }
    foreach ($rt in $rtAssoc.RouteTables) {
        foreach ($r in $rt.Routes) {
            if ($r.DestinationCidrBlock -eq "0.0.0.0/0") {
                Write-Fact "0.0.0.0/0" ($r.GatewayId + $r.NatGatewayId)
            }
        }
    }
}

Write-Fact "VPC (from subnet)" $vpcId

# VPC endpoints
$vpcEp = aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$vpcId" --region $Region --output json | ConvertFrom-Json
$epServices = $vpcEp.VpcEndpoints | ForEach-Object { ($_.ServiceName -split "/")[-1] }
Write-Fact "VPC Endpoints" ($epServices -join ", ")
Write-Fact "ecr.api" $(if ($epServices -contains "ecr.api") { "EXISTS" } else { "NONE" })
Write-Fact "ecr.dkr" $(if ($epServices -contains "ecr.dkr") { "EXISTS" } else { "NONE" })
Write-Fact "logs" $(if ($epServices -contains "logs") { "EXISTS" } else { "NONE" })
Write-Fact "ssm" $(if ($epServices -contains "ssm") { "EXISTS" } else { "NONE" })

# IGW
$igw = aws ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$vpcId" --region $Region --output json | ConvertFrom-Json
Write-Fact "InternetGateway" $(if ($igw.InternetGateways.Count -gt 0) { $igw.InternetGateways[0].InternetGatewayId } else { "NONE" })

# NAT
$nat = aws ec2 describe-nat-gateways --filter "Name=vpc-id,Values=$vpcId" "Name=state,Values=available" --region $Region --output json | ConvertFrom-Json
Write-Fact "NAT Gateway" $(if ($nat.NatGateways.Count -gt 0) { $nat.NatGateways[0].NatGatewayId } else { "NONE" })

Write-Section "SECTION 2 — SECURITY GROUP VALIDATION"
foreach ($sgId in $sgIds) {
    $sg = aws ec2 describe-security-groups --group-ids $sgId --region $Region --output json | ConvertFrom-Json
    $g = $sg.SecurityGroups[0]
    Write-Host "  SG: $sgId ($($g.GroupName))" -ForegroundColor Yellow
    Write-Host "  Inbound:" ; $g.IpPermissions | ForEach-Object { Write-Host "    $($_.FromPort)-$($_.ToPort) $($_.IpProtocol) $($_.UserIdGroupPairs.GroupId -join ',') $($_.IpRanges.CidrIp -join ',')" }
    Write-Host "  Outbound:" ; $g.IpPermissionsEgress | ForEach-Object { Write-Host "    $($_.FromPort)-$($_.ToPort) $($_.IpProtocol) $($_.UserIdGroupPairs.GroupId -join ',') $($_.IpRanges.CidrIp -join ',')" }
}

# SSM to get DB_HOST, REDIS_HOST, API_BASE_URL (fetch as JSON to avoid --output text truncation/encoding)
$ssmRaw = $null
$ssmExit = 0
try {
    $prevErrAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $ssmRaw = aws ssm get-parameter --name "/academy/workers/env" --region $Region --with-decryption --output json 2>&1
    $ssmExit = $LASTEXITCODE
    $ErrorActionPreference = $prevErrAction
} catch {
    $ErrorActionPreference = 'Stop'
    $ssmRaw = $null
}
if ($ssmExit -ne 0) { $ssmRaw = $null }
if ($ssmRaw -is [System.Management.Automation.ErrorRecord]) { $ssmRaw = $null }

$dbHost = $null; $redisHost = $null; $apiBaseUrl = $null; $r2Endpoint = $null
if ($ssmRaw) {
    try {
        $ssmOuter = $ssmRaw | ConvertFrom-Json
        $valueStr = $ssmOuter.Parameter.Value
        if ($valueStr -and ($valueStr -is [string])) {
            $ssmJson = $valueStr | ConvertFrom-Json
            $dbHost = $ssmJson.DB_HOST
            $redisHost = $ssmJson.REDIS_HOST
            $apiBaseUrl = $ssmJson.API_BASE_URL
            $r2Endpoint = $ssmJson.R2_ENDPOINT
        }
    } catch {
        Write-Host "  WARN: SSM parameter /academy/workers/env could not be parsed as JSON (hostnames will be empty)." -ForegroundColor Yellow
    }
}

Write-Section "SECTION 3 — SSM PARAMETER CONTENT (hostnames only)"
Write-Fact "DB_HOST" $dbHost
Write-Fact "REDIS_HOST" $redisHost
Write-Fact "API_BASE_URL" $apiBaseUrl
Write-Fact "R2_ENDPOINT" $r2Endpoint

# Resolve host type
$rdsEndpoint = $null
$rdsSgId = $null
try {
    $rdsList = aws rds describe-db-instances --region $Region --output json | ConvertFrom-Json
    $rdsEndpoint = $rdsList.DBInstances | Where-Object { $_.Endpoint.Address -eq $dbHost } | Select-Object -First 1
    if ($rdsEndpoint -and $rdsEndpoint.VpcSecurityGroups.Count -gt 0) { $rdsSgId = $rdsEndpoint.VpcSecurityGroups[0].VpcSecurityGroupId }
} catch {}
Write-Fact "DB_HOST type" $(if ($rdsEndpoint) { "RDS" } else { "UNKNOWN (not in describe-db-instances)" })

$elbList = $null
try { $elbList = aws elbv2 describe-load-balancers --region $Region --output json | ConvertFrom-Json } catch {}
$apiHost = $null
try { if ($apiBaseUrl) { $apiHost = ([System.Uri]$apiBaseUrl).Host } } catch {}
$elbMatch = $null
if ($elbList -and $elbList.LoadBalancers) { $elbMatch = $elbList.LoadBalancers | Where-Object { $_.DNSName -eq $apiHost } | Select-Object -First 1 }
Write-Fact "API host" $apiHost
Write-Fact "API host type" $(if ($elbMatch) { "ALB/NLB" } else { "UNKNOWN" })

# Redis: ElastiCache
$redisSgId = $null
$cacheList = $null
try { $cacheList = aws elasticache describe-cache-clusters --region $Region --show-cache-node-info --output json 2>&1 | ConvertFrom-Json } catch {}
$redisMatch = $null
if ($cacheList -and $cacheList.CacheClusters) {
    foreach ($cluster in $cacheList.CacheClusters) {
        if ($cluster.CacheNodes) {
            foreach ($node in $cluster.CacheNodes) {
                if ($node.Endpoint -and $node.Endpoint.Address -eq $redisHost) {
                    $redisMatch = $node
                    if ($cluster.SecurityGroups -and $cluster.SecurityGroups.Count -gt 0) { $redisSgId = $cluster.SecurityGroups[0].SecurityGroupId }
                    break
                }
            }
        }
    }
}
Write-Fact "REDIS_HOST type" $(if ($redisMatch) { "ElastiCache" } else { "UNKNOWN" })

# SG comparison: Batch SG -> RDS/Redis/API
$batchSg = $sgIds[0]
$batchAllowedRDS = $false
if ($rdsSgId) {
    try {
        $rdsInbound = aws ec2 describe-security-groups --group-ids $rdsSgId --region $Region --query "SecurityGroups[0].IpPermissions" --output json 2>&1 | ConvertFrom-Json
        foreach ($perm in $rdsInbound) {
            $fromBatch = $perm.UserIdGroupPairs | Where-Object { $_.GroupId -eq $batchSg }
            if ($fromBatch -and (($perm.FromPort -eq 5432) -or ($perm.FromPort -eq 3306) -or ($null -eq $perm.FromPort))) { $batchAllowedRDS = $true; break }
        }
    } catch {}
}
Write-Fact "Batch SG allowed to RDS (5432/3306)" $(if ($batchAllowedRDS) { "ALLOWED" } elseif (-not $rdsSgId) { "UNKNOWN" } else { "NOT ALLOWED" })

$batchAllowedRedis = $false
if ($redisSgId) {
    try {
        $redisInbound = aws ec2 describe-security-groups --group-ids $redisSgId --region $Region --query "SecurityGroups[0].IpPermissions" --output json | ConvertFrom-Json
        foreach ($perm in $redisInbound) {
            $fromBatch = $perm.UserIdGroupPairs | Where-Object { $_.GroupId -eq $batchSg }
            if ($fromBatch -and (($perm.FromPort -eq 6379) -or ($null -eq $perm.FromPort))) { $batchAllowedRedis = $true; break }
        }
    } catch {}
}
Write-Fact "Batch SG allowed to Redis (6379)" $(if ($batchAllowedRedis) { "ALLOWED" } elseif (-not $redisSgId) { "UNKNOWN" } else { "NOT ALLOWED" })

Write-Section "SECTION 4 — ACTIVE CONNECTIVITY PROOF"
$jobs = aws batch list-jobs --job-queue academy-video-batch-queue --job-status RUNNING --region $Region --output json 2>&1 | ConvertFrom-Json
$runJobId = $null
if ($jobs.jobSummaryList.Count -gt 0) { $runJobId = $jobs.jobSummaryList[0].jobId }
if (-not $runJobId) {
    Write-Host "  NO LIVE INSTANCE; submitting netprobe job for connectivity proof..." -ForegroundColor Yellow
    $npSubmit = aws batch submit-job --job-name "netprobe-verify-$((Get-Date).ToString('yyyyMMddHHmmss'))" --job-queue academy-video-batch-queue --job-definition academy-video-ops-netprobe --region $Region --output json 2>&1 | ConvertFrom-Json
    $npJobId = $npSubmit.jobId
    if ($npJobId) {
        $npWait = 0
        while ($npWait -lt 180) {
            $npDesc = aws batch describe-jobs --jobs $npJobId --region $Region --output json | ConvertFrom-Json
            $npStatus = $npDesc.jobs[0].status
            if ($npStatus -eq "SUCCEEDED") {
                $npCont = $npDesc.jobs[0].container
                $npLogStream = $npCont.logStreamName
                $npGroup = $npCont.logConfiguration.options."awslogs-group"
                Write-Fact "ACTIVE CONNECTIVITY PROOF (netprobe)" "SUCCEEDED jobId=$npJobId"
                if ($npLogStream -and $npGroup) {
                    $npEvents = aws logs get-log-events --log-group-name $npGroup --log-stream-name $npLogStream --limit 50 --region $Region --output json 2>&1 | ConvertFrom-Json
                    $npEvents.events | ForEach-Object { Write-Host "    $($_.message)" }
                }
                break
            }
            if ($npStatus -eq "FAILED") {
                Write-Host "  Netprobe job FAILED: $npJobId" -ForegroundColor Red
                break
            }
            Start-Sleep -Seconds 8
            $npWait += 8
        }
    } else { Write-Host "  Netprobe submit failed." -ForegroundColor Red }
} else {
    $jobDetail = aws batch describe-jobs --jobs $runJobId --region $Region --output json | ConvertFrom-Json
    $cont = $jobDetail.jobs[0].container
    $ec2Id = $cont.containerInstanceArn
    Write-Fact "Running job" $runJobId
    Write-Fact "containerInstanceArn" $ec2Id
    # ECS container instance -> EC2 instance ID
    $clusterArn = $jobDetail.jobs[0].container.containerInstanceArn -replace "/container/.*", ""
    $ciId = $jobDetail.jobs[0].container.containerInstanceArn -replace ".*/container/", ""
    $ci = aws ecs describe-container-instances --cluster $clusterArn --container-instances $ciId --region $Region --output json | ConvertFrom-Json
    $ec2InstanceId = $ci.containerInstances[0].ec2InstanceId
    Write-Fact "EC2 InstanceId" $ec2InstanceId
    $eni = aws ec2 describe-instances --instance-ids $ec2InstanceId --region $Region --query "Reservations[0].Instances[0].NetworkInterfaces[0].{SubnetId:SubnetId,GroupIds:Groups[*].GroupId}" --output json | ConvertFrom-Json
    Write-Fact "SubnetId" $eni.SubnetId
    Write-Fact "SecurityGroups" ($eni.GroupIds -join ", ")
}

Write-Section "SECTION 5 — OUTPUT FORMAT"

Write-Host "`nNETWORK TOPOLOGY SUMMARY" -ForegroundColor Cyan
Write-Fact "VPC" $vpcId
Write-Fact "Batch Subnets" ($subnetIds -join ", ")
Write-Fact "Batch SGs" ($sgIds -join ", ")
Write-Fact "Internet (0.0.0.0/0)" $(if ($igw.InternetGateways.Count -gt 0) { "IGW" } elseif ($nat.NatGateways.Count -gt 0) { "NAT" } else { "NONE" })

Write-Host "`nCONNECTIVITY MATRIX:" -ForegroundColor Cyan
Write-Host "  Batch -> RDS    : $(if ($batchAllowedRDS) { 'ALLOWED' } elseif ($rdsSgId) { 'BLOCKED' } else { 'UNKNOWN' })"
Write-Host "  Batch -> Redis  : $(if ($batchAllowedRedis) { 'ALLOWED' } elseif ($redisSgId) { 'BLOCKED' } else { 'UNKNOWN' })"
Write-Host "  Batch -> API    : UNKNOWN (API SG not resolved from API_BASE_URL)"
Write-Host "  Batch -> Internet : $(if ($igw.InternetGateways.Count -gt 0) { 'IGW' } elseif ($nat.NatGateways.Count -gt 0) { 'NAT' } else { 'NONE' })"

Write-Host "`nCRITICAL BREAKAGES LIST:" -ForegroundColor Red
if (-not $batchAllowedRDS -and $rdsSgId) { Write-Host "  - Batch SG not allowed to RDS SG (port 5432/3306)" }
if (-not $batchAllowedRedis -and $redisSgId) { Write-Host "  - Batch SG not allowed to Redis SG (port 6379)" }
if ($igw.InternetGateways.Count -eq 0 -and $nat.NatGateways.Count -eq 0) { Write-Host "  - No IGW or NAT for 0.0.0.0/0 (R2/ECR/Logs need internet or VPC endpoints)" }
if ($epServices -notcontains "ecr.api") { Write-Host "  - VPC endpoint ecr.api missing (ECR pull may fail in private subnet)" }
if ($epServices -notcontains "logs") { Write-Host "  - VPC endpoint logs missing (CloudWatch Logs may fail in private subnet)" }

Write-Host "`nDRIFT LIST (repo vs deployed):" -ForegroundColor Yellow
if ($ComputeEnvName -ne "academy-video-batch-ce-v3") { Write-Host "  - CE name: $ComputeEnvName (repo default: academy-video-batch-ce-v3)" }

Write-Host "`nFINAL VERDICT:" -ForegroundColor Cyan
$breakCount = 0
if (-not $batchAllowedRDS -and $rdsSgId) { $breakCount++ }
if (-not $batchAllowedRedis -and $redisSgId) { $breakCount++ }
if ($igw.InternetGateways.Count -eq 0 -and $nat.NatGateways.Count -eq 0) { $breakCount++ }
if ($breakCount -gt 0) { Write-Host "  NETWORK BROKEN" -ForegroundColor Red } elseif ($batchAllowedRDS -and $batchAllowedRedis) { Write-Host "  NETWORK READY" -ForegroundColor Green } else { Write-Host "  NETWORK PARTIAL" -ForegroundColor Yellow }
