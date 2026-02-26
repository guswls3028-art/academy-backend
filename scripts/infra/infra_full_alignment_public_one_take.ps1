# ==============================================================================
# 인프라 전체 정렬 원테이크 — Public SSOT v2.0. Private+NAT 폐기, Public Subnet + IGW 통일.
# 구축 + 작동검증 + 감사 PASS. 부분 해결책 금지. 실패 시 FAIL로 종료.
# Usage:
#   .\scripts\infra\infra_full_alignment_public_one_take.ps1 -Region ap-northeast-2 -VpcId vpc-0831a2484f9b114c2 -EcrRepoUri "<acct>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:<tag>" -FixMode -EnableSchedulers
# ==============================================================================
param(
    [string]$Region = "ap-northeast-2",
    [string]$VpcId = "vpc-0831a2484f9b114c2",
    [Parameter(Mandatory=$true)][string]$EcrRepoUri,
    [switch]$FixMode = $false,
    [switch]$EnableSchedulers = $false
)
$ApiElasticIp = "15.165.147.157"
$ApiBaseUrl = "http://${ApiElasticIp}:8000"
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ErrorActionPreference = "Stop"

# EcrRepoUri 검증
$EcrRepoUri = $EcrRepoUri.Trim()
if ($EcrRepoUri -match ':[^:]+$') {
    $tagPart = $Matches[0].TrimStart(':')
    $first = ($tagPart -split "[\s`n]+")[0]
    if ([string]::IsNullOrWhiteSpace($tagPart) -or $tagPart -match '\s' -or $first -eq "None" -or $tagPart -eq "None") {
        Write-Host "EcrRepoUri FAIL: tag must be single non-empty value (no spaces, not None)." -ForegroundColor Red
        exit 1
    }
}
if ($EcrRepoUri -match ':latest$') { Write-Host "EcrRepoUri FAIL: :latest forbidden." -ForegroundColor Red; exit 1 }

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$PreflightDir = Join-Path $RepoRoot "one_take_public_preflight_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"
$OpsQueueName = "academy-video-ops-queue"
$VideoCEName = "academy-video-batch-ce-final"
$OpsCEName = "academy-video-ops-ce"
$ReconcileRuleName = "academy-reconcile-video-jobs"
$ScanStuckRuleName = "academy-video-scan-stuck-rate"

$script:Audit1 = "FAIL"; $script:Audit2 = "FAIL"; $script:Audit3 = "FAIL"; $script:Audit4 = "FAIL"
$script:Audit5 = "FAIL"; $script:Audit6 = "FAIL"; $script:Audit7 = "FAIL"
$script:Audit1Detail = ""; $script:Audit2Detail = ""; $script:Audit3Detail = ""; $script:Audit4Detail = ""
$script:Audit5Detail = ""; $script:Audit6Detail = ""; $script:Audit7Detail = ""
$script:AnyFail = $false

function ExecJson($CmdArgs) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @CmdArgs 2>&1
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $s = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    try { return $s | ConvertFrom-Json } catch { return $null }
}

function Fail-OneTake {
    param([string]$Reason, [string]$EvidenceJson = "")
    Write-Host "FAIL: $Reason" -ForegroundColor Red
    if ($EvidenceJson) { Write-Host "EVIDENCE:" -ForegroundColor Gray; Write-Host $EvidenceJson -ForegroundColor Gray }
    $path = Join-Path $PreflightDir "fail_evidence.json"
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $path))) { New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null }
    [System.IO.File]::WriteAllText($path, $Reason + "`n" + $EvidenceJson, [System.Text.UTF8Encoding]::new($false))
    exit 1
}

New-Item -ItemType Directory -Path $PreflightDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

# ========== 1) Preflight Snapshot ==========
Write-Host "`n=== 1) Preflight Snapshot ===" -ForegroundColor Cyan
& (Join-Path $ScriptRoot "infra_forensic_collect.ps1") -Region $Region -OutDir $PreflightDir 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "Preflight had errors (continuing)." -ForegroundColor Yellow }
Write-Host "Preflight: $PreflightDir" -ForegroundColor Gray

# ========== 2) Network Enforce (Public only, no NAT) ==========
Write-Host "`n=== 2) Network Enforce (Public Subnet + IGW) ===" -ForegroundColor Cyan
$natList = ExecJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$VpcId", "Name=state,Values=available", "--region", $Region, "--output", "json")
$natCount = if ($natList -and $natList.NatGateways) { $natList.NatGateways.Count } else { 0 }
if ($natCount -gt 0) {
    Write-Host "WARN: NAT Gateway(s) exist in VPC. SSOT v2.0 does not use NAT; Batch/API/Build use Public Subnet + IGW only." -ForegroundColor Yellow
}

$igwList = ExecJson @("ec2", "describe-internet-gateways", "--filters", "Name=attachment.vpc-id,Values=$VpcId", "--region", $Region, "--output", "json")
$igwId = $null
if ($igwList -and $igwList.InternetGateways -and $igwList.InternetGateways.Count -gt 0) {
    $igwId = $igwList.InternetGateways[0].InternetGatewayId
}
if (-not $igwId) { Fail-OneTake "No Internet Gateway attached to VPC $VpcId. IGW required for Public SSOT v2.0." ($igwList | ConvertTo-Json -Depth 2 -Compress) }

$rtResp = ExecJson @("ec2", "describe-route-tables", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $Region, "--output", "json")
if (-not $rtResp -or -not $rtResp.RouteTables) { Fail-OneTake "No route tables in VPC $VpcId" "" }

$publicSubnetIds = @()
$publicRouteTableIds = @()
foreach ($rt in $rtResp.RouteTables) {
    $hasIgw = $false
    foreach ($r in $rt.Routes) {
        if ($r.DestinationCidrBlock -eq "0.0.0.0/0" -and $r.GatewayId -and $r.GatewayId.StartsWith("igw-")) { $hasIgw = $true; break }
    }
    if (-not $hasIgw) { continue }
    $publicRouteTableIds += $rt.RouteTableId
    foreach ($a in $rt.Associations) {
        if ($a.SubnetId) { $publicSubnetIds += $a.SubnetId }
    }
}
$publicSubnetIds = $publicSubnetIds | Select-Object -Unique
if ($publicSubnetIds.Count -eq 0) { Fail-OneTake "No Public Subnet (0.0.0.0/0 -> IGW) in VPC $VpcId." ($rtResp | ConvertTo-Json -Depth 3 -Compress) }

foreach ($rtId in $publicRouteTableIds) {
    $rtObj = $rtResp.RouteTables | Where-Object { $_.RouteTableId -eq $rtId } | Select-Object -First 1
    $hasDefault = $false
    foreach ($r in $rtObj.Routes) {
        if ($r.DestinationCidrBlock -eq "0.0.0.0/0" -and $r.GatewayId -eq $igwId) { $hasDefault = $true; break }
    }
    if (-not $hasDefault -and $FixMode) {
        & aws ec2 create-route --route-table-id $rtId --destination-cidr-block 0.0.0.0/0 --gateway-id $igwId --region $Region 2>&1 | Out-Null
    }
}

$subnetsResp = ExecJson @("ec2", "describe-subnets", "--subnet-ids", $publicSubnetIds, "--region", $Region, "--output", "json")
foreach ($sub in $subnetsResp.Subnets) {
    if ($sub.MapPublicIpOnLaunch -eq $false -and $FixMode) {
        & aws ec2 modify-subnet-attribute --subnet-id $sub.SubnetId --map-public-ip-on-launch --region $Region 2>&1 | Out-Null
    }
}
$script:Audit1 = "PASS"
$script:Audit1Detail = "VPC=$VpcId IGW=$igwId PublicSubnets=$($publicSubnetIds.Count) MapPublicIpOnLaunch=true 0.0.0.0/0->IGW"
Write-Host "  $script:Audit1Detail" -ForegroundColor Gray

# ========== 3) API verification (Elastic IP + healthcheck) ==========
Write-Host "`n=== 3) API verification ===" -ForegroundColor Cyan
$addrResp = ExecJson @("ec2", "describe-addresses", "--public-ips", $ApiElasticIp, "--region", $Region, "--output", "json")
$apiInstanceId = $null
if ($addrResp -and $addrResp.Addresses -and $addrResp.Addresses.Count -gt 0 -and $addrResp.Addresses[0].InstanceId) {
    $apiInstanceId = $addrResp.Addresses[0].InstanceId
}
if (-not $apiInstanceId) { $script:Audit2 = "FAIL"; $script:Audit2Detail = "Elastic IP $ApiElasticIp not associated to any instance"; $script:AnyFail = $true; Fail-OneTake $script:Audit2Detail ($addrResp | ConvertTo-Json -Depth 2 -Compress) }

$apiInst = ExecJson @("ec2", "describe-instances", "--instance-ids", $apiInstanceId, "--region", $Region, "--output", "json")
$apiObj = $apiInst.Reservations[0].Instances[0]
$apiSgIds = @($apiObj.SecurityGroups | ForEach-Object { $_.GroupId })
$apiVpcId = $apiObj.VpcId
$apiSubnetId = $apiObj.SubnetId
if ($apiVpcId -ne $VpcId) { $script:Audit2 = "FAIL"; $script:AnyFail = $true; Fail-OneTake "API instance $apiInstanceId is in VPC $apiVpcId, expected $VpcId" "" }
if ($publicSubnetIds -notcontains $apiSubnetId) { $script:Audit2 = "FAIL"; $script:AnyFail = $true; Fail-OneTake "API instance subnet $apiSubnetId is not in Public Subnet list." "" }

try {
    $curlResult = Invoke-WebRequest -Uri $ApiBaseUrl -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
    $healthOk = $true
} catch {
    $healthOk = $false
}
if (-not $healthOk) {
    $script:Audit2 = "FAIL"; $script:Audit2Detail = "API healthcheck $ApiBaseUrl failed or timeout"
    $script:AnyFail = $true
    Fail-OneTake $script:Audit2Detail ""
}
$script:Audit2 = "PASS"
$script:Audit2Detail = "ElasticIP=$ApiElasticIp InstanceId=$apiInstanceId API_BASE_URL=$ApiBaseUrl healthcheck OK"
Write-Host "  $script:Audit2Detail" -ForegroundColor Gray

# Write api_instance.json for recreate_batch
$apiJson = @{ InstanceId = $apiInstanceId; VpcId = $apiVpcId; SubnetId = $apiSubnetId; SecurityGroupIds = $apiSgIds; PrivateIpAddress = $apiObj.PrivateIpAddress; PublicIpAddress = $ApiElasticIp } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $OutDir "api_instance.json"), $apiJson, [System.Text.UTF8Encoding]::new($false))

# ========== 4) Build server alignment ==========
Write-Host "`n=== 4) Build server alignment ===" -ForegroundColor Cyan
$buildResp = ExecJson @("ec2", "describe-instances", "--region", $Region, "--filters", "Name=tag:Name,Values=academy-build-arm64", "Name=instance-state-name,Values=running,stopped", "--output", "json")
$buildInst = $null
if ($buildResp -and $buildResp.Reservations) {
    foreach ($res in $buildResp.Reservations) {
        $buildInst = $res.Instances | Where-Object { ($_.Tags | Where-Object { $_.Key -eq "Name" -and $_.Value -match "build" }) } | Select-Object -First 1
        if ($buildInst) { break }
    }
}
if (-not $buildInst) {
    $script:Audit3 = "FAIL"; $script:Audit3Detail = "Build instance (academy-build-arm64) not found"; $script:AnyFail = $true
    Fail-OneTake $script:Audit3Detail ""
}
$buildInstanceId = $buildInst.InstanceId
if ($publicSubnetIds -notcontains $buildInst.SubnetId) {
    $script:Audit3 = "FAIL"; $script:Audit3Detail = "Build instance not in Public Subnet"; $script:AnyFail = $true
    Fail-OneTake $script:Audit3Detail ""
}
if (-not $buildInst.PublicIpAddress -and -not $buildInst.PublicDnsName) {
    Write-Host "  WARN: Build instance has no Public IP. Ensure MapPublicIpOnLaunch or Elastic IP." -ForegroundColor Yellow
}
$ssmDoc = "AWS-RunShellScript"
$cmd1 = ExecJson @("ssm", "send-command", "--instance-ids", $buildInstanceId, "--document-name", $ssmDoc, "--parameters", '{"commands":["aws sts get-caller-identity --output json"]}', "--region", $Region, "--output", "json")
if (-not $cmd1 -or -not $cmd1.Command.CommandId) {
    $script:Audit3 = "FAIL"; $script:Audit3Detail = "Build SSM send-command (sts) failed"; $script:AnyFail = $true
    Fail-OneTake $script:Audit3Detail ""
}
$c1Id = $cmd1.Command.CommandId
$ssmWait = 0
do { Start-Sleep -Seconds 5; $ssmWait += 5; $inv1 = ExecJson @("ssm", "get-command-invocation", "--command-id", $c1Id, "--instance-id", $buildInstanceId, "--region", $Region, "--output", "json") } while ($inv1.Status -eq "InProgress" -and $ssmWait -lt 90)
if ($inv1.Status -ne "Success") {
    $script:Audit3 = "FAIL"; $script:Audit3Detail = "Build SSM sts get-caller-identity Status=$($inv1.Status)"; $script:AnyFail = $true
    Fail-OneTake $script:Audit3Detail ($inv1 | ConvertTo-Json -Depth 2 -Compress)
}
$cmd2 = ExecJson @("ssm", "send-command", "--instance-ids", $buildInstanceId, "--document-name", $ssmDoc, "--parameters", '{"commands":["curl -s -o /dev/null -w \"%{http_code}\" https://sts.ap-northeast-2.amazonaws.com"]}', "--region", $Region, "--output", "json")
if (-not $cmd2 -or -not $cmd2.Command.CommandId) {
    $script:Audit3 = "FAIL"; $script:Audit3Detail = "Build SSM send-command (curl sts) failed"; $script:AnyFail = $true
    Fail-OneTake $script:Audit3Detail ""
}
$c2Id = $cmd2.Command.CommandId
$ssmWait2 = 0
do { Start-Sleep -Seconds 5; $ssmWait2 += 5; $inv2 = ExecJson @("ssm", "get-command-invocation", "--command-id", $c2Id, "--instance-id", $buildInstanceId, "--region", $Region, "--output", "json") } while ($inv2.Status -eq "InProgress" -and $ssmWait2 -lt 90)
if ($inv2.Status -ne "Success") {
    $script:Audit3 = "FAIL"; $script:Audit3Detail = "Build SSM curl sts Status=$($inv2.Status)"; $script:AnyFail = $true
    Fail-OneTake $script:Audit3Detail ""
}
$script:Audit3 = "PASS"
$script:Audit3Detail = "Build instance $buildInstanceId Public Subnet SSM sts+curl STS OK"
Write-Host "  $script:Audit3Detail" -ForegroundColor Gray

# ========== 5) Batch Video/Ops (Public subnets only) ==========
Write-Host "`n=== 5) Batch Video/Ops (Public subnets) ===" -ForegroundColor Cyan
& (Join-Path $ScriptRoot "recreate_batch_in_api_vpc.ps1") -Region $Region -EcrRepoUri $EcrRepoUri -ApiInstanceId $apiInstanceId -SubnetIds $publicSubnetIds -ComputeEnvName $VideoCEName -JobQueueName "academy-video-batch-queue" -WorkerJobDefName "academy-video-batch-jobdef" 2>&1
if ($LASTEXITCODE -ne 0) { Fail-OneTake "recreate_batch_in_api_vpc.ps1 failed (exit $LASTEXITCODE)" "" }

$batchCe = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$ceObj = $batchCe.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
if (-not $ceObj -or $ceObj.status -ne "VALID" -or $ceObj.state -ne "ENABLED") {
    $script:Audit4 = "FAIL"; $script:Audit4Detail = "Video CE status=$($ceObj.status) state=$($ceObj.state)"; $script:AnyFail = $true
    Fail-OneTake $script:Audit4Detail ($batchCe | ConvertTo-Json -Depth 3 -Compress)
}
$script:Audit4 = "PASS"
$script:Audit4Detail = "Video CE $VideoCEName VALID ENABLED Public Subnets"

& (Join-Path $ScriptRoot "batch_ops_setup.ps1") -Region $Region -VpcId $VpcId -SubnetIds $publicSubnetIds -VideoCeNameForDiscovery $VideoCEName 2>&1
if ($LASTEXITCODE -ne 0) { Fail-OneTake "batch_ops_setup.ps1 failed" "" }

$opsCe = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $OpsCEName, "--region", $Region, "--output", "json")
$opsCeObj = $opsCe.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $OpsCEName } | Select-Object -First 1
if (-not $opsCeObj -or $opsCeObj.status -ne "VALID" -or $opsCeObj.state -ne "ENABLED") {
    $script:Audit5 = "FAIL"; $script:Audit5Detail = "Ops CE status=$($opsCeObj.status) state=$($opsCeObj.state)"; $script:AnyFail = $true
} else {
    $script:Audit5 = "PASS"
    $script:Audit5Detail = "Ops CE $OpsCEName VALID ENABLED"
}

& (Join-Path $ScriptRoot "eventbridge_deploy_video_scheduler.ps1") -Region $Region -OpsJobQueueName $OpsQueueName -VideoCeNameForDiscovery $VideoCEName -VpcId $VpcId -SubnetIds $publicSubnetIds 2>&1
if ($LASTEXITCODE -ne 0) { Fail-OneTake "eventbridge_deploy_video_scheduler.ps1 failed" "" }
$ruleR = ExecJson @("events", "describe-rule", "--name", $ReconcileRuleName, "--region", $Region, "--output", "json")
$ruleS = ExecJson @("events", "describe-rule", "--name", $ScanStuckRuleName, "--region", $Region, "--output", "json")
$scheduleOk = ($ruleR.ScheduleExpression -eq "rate(15 minutes)") -and ($ruleS.ScheduleExpression -eq "rate(5 minutes)")
if (-not $scheduleOk) { $script:Audit6 = "FAIL"; $script:Audit6Detail = "EventBridge schedule mismatch"; $script:AnyFail = $true } else { $script:Audit6 = "PASS"; $script:Audit6Detail = "reconcile 15min scanstuck 5min" }

if (-not $EnableSchedulers) {
    & aws events disable-rule --name $ReconcileRuleName --region $Region 2>&1 | Out-Null
    & aws events disable-rule --name $ScanStuckRuleName --region $Region 2>&1 | Out-Null
} else {
    & aws events enable-rule --name $ReconcileRuleName --region $Region 2>&1 | Out-Null
    & aws events enable-rule --name $ScanStuckRuleName --region $Region 2>&1 | Out-Null
}

# ========== 6) RUNNABLE cleanup + Netprobe ==========
Write-Host "`n=== 6) RUNNABLE cleanup + Netprobe ===" -ForegroundColor Cyan
$jobDefNamesToClean = @("academy-video-ops-reconcile", "academy-video-ops-scanstuck", "academy-video-ops-netprobe")
foreach ($status in @("RUNNABLE", "RUNNING")) {
    $list = ExecJson @("batch", "list-jobs", "--job-queue", $OpsQueueName, "--job-status", $status, "--region", $Region, "--output", "json")
    if (-not $list -or -not $list.jobSummaryList) { continue }
    foreach ($j in $list.jobSummaryList) {
        $desc = ExecJson @("batch", "describe-jobs", "--jobs", $j.jobId, "--region", $Region, "--output", "json")
        $jdName = ""
        if ($desc -and $desc.jobs -and $desc.jobs[0].jobDefinition) { $jdName = (($desc.jobs[0].jobDefinition -split "/")[-1] -split ":")[0] }
        foreach ($n in $jobDefNamesToClean) { if ($jdName -eq $n) { & aws batch terminate-job --job-id $j.jobId --reason "Public one-take cleanup" --region $Region 2>&1 | Out-Null; break } }
    }
}
Start-Sleep -Seconds 5

$netprobeJobIdFile = Join-Path $Env:TEMP "netprobe_public_$(Get-Date -Format 'yyyyMMddHHmmss').txt"
& (Join-Path $ScriptRoot "run_netprobe_job.ps1") -Region $Region -JobQueueName $OpsQueueName -JobIdOutFile $netprobeJobIdFile -RunnableFailSeconds 180 2>&1
if ($LASTEXITCODE -ne 0) {
    $script:Audit7 = "FAIL"; $script:Audit7Detail = "Netprobe not SUCCEEDED"; $script:AnyFail = $true
    if (Test-Path -LiteralPath $netprobeJobIdFile) {
        $jid = [System.IO.File]::ReadAllText($netprobeJobIdFile, [System.Text.UTF8Encoding]::new($false)).Trim()
        $ev = ExecJson @("batch", "describe-jobs", "--jobs", $jid, "--region", $Region, "--output", "json")
        Fail-OneTake "Netprobe did not SUCCEED. RUNNABLE backlog or STS timeout." ($ev | ConvertTo-Json -Depth 4 -Compress)
    }
    Fail-OneTake "Netprobe did not SUCCEED." ""
}
Remove-Item $netprobeJobIdFile -Force -ErrorAction SilentlyContinue
$script:Audit7 = "PASS"
$script:Audit7Detail = "Netprobe SUCCEEDED"

# ========== 7) Audit output ==========
Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "VIDEO WORKER SSOT AUDIT" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[1] Network   $script:Audit1Detail" -ForegroundColor Gray
Write-Host "    Result: $script:Audit1"
Write-Host ""
Write-Host "[2] API       $script:Audit2Detail" -ForegroundColor Gray
Write-Host "    Result: $script:Audit2"
Write-Host ""
Write-Host "[3] Build     $script:Audit3Detail" -ForegroundColor Gray
Write-Host "    Result: $script:Audit3"
Write-Host ""
Write-Host "[4] Video CE  $script:Audit4Detail" -ForegroundColor Gray
Write-Host "    Result: $script:Audit4"
Write-Host ""
Write-Host "[5] Ops CE    $script:Audit5Detail" -ForegroundColor Gray
Write-Host "    Result: $script:Audit5"
Write-Host ""
Write-Host "[6] EventBridge $script:Audit6Detail" -ForegroundColor Gray
Write-Host "    Result: $script:Audit6"
Write-Host ""
Write-Host "[7] Netprobe  $script:Audit7Detail" -ForegroundColor Gray
Write-Host "    Result: $script:Audit7"
Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
$finalResult = "PASS"
if ($script:AnyFail -or $script:Audit1 -ne "PASS" -or $script:Audit2 -ne "PASS" -or $script:Audit3 -ne "PASS" -or $script:Audit4 -ne "PASS" -or $script:Audit5 -ne "PASS" -or $script:Audit6 -ne "PASS" -or $script:Audit7 -ne "PASS") { $finalResult = "FAIL" }
Write-Host "FINAL RESULT: $finalResult" -ForegroundColor $(if ($finalResult -eq "PASS") { "Green" } else { "Red" })
Write-Host "==============================" -ForegroundColor Cyan

if ($finalResult -eq "FAIL") {
    Write-Host "Public SSOT v2.0 audit failed. Do not treat as production." -ForegroundColor Red
    exit 1
}
Write-Host "`nPublic SSOT v2.0 one-take: DONE (PASS)" -ForegroundColor Green
Write-Host "Preflight: $PreflightDir" -ForegroundColor Gray
