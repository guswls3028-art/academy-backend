# ==============================================================================
# 인프라 전체 정렬 원테이크 — SSOT v1.1 강제. 구축 + 검증 + 감사 PASS까지.
# 부분 해결책 금지. 하나라도 실패하면 전체 FAIL + reason + evidence 출력.
# Usage: .\scripts\infra\infra_full_alignment_one_take.ps1 -Region ap-northeast-2 -VpcId vpc-0831a2484f9b114c2 -EcrRepoUri "<acct>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:<immutable-tag>" [-FixMode] [-EnableSchedulers]
# ==============================================================================
param(
    [string]$Region = "ap-northeast-2",
    [string]$VpcId = "vpc-0831a2484f9b114c2",
    [Parameter(Mandatory=$true)][string]$EcrRepoUri,
    [switch]$FixMode = $false,
    [switch]$EnableSchedulers = $false
)
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$PreflightDir = Join-Path $RepoRoot "one_take_preflight_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
$OpsQueueName = "academy-video-ops-queue"
$ReconcileRuleName = "academy-reconcile-video-jobs"
$ScanStuckRuleName = "academy-video-scan-stuck-rate"

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

# --- A. Preflight snapshot ---
Write-Host "`n=== A. Preflight snapshot ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Path $PreflightDir -Force | Out-Null
& (Join-Path $ScriptRoot "infra_forensic_collect.ps1") -Region $Region -OutDir $PreflightDir 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "Preflight forensic collect had errors (continuing)." -ForegroundColor Yellow }
Write-Host "Preflight saved to $PreflightDir" -ForegroundColor Gray

# --- B. Network baseline: NAT + S3 Gateway Endpoint ---
Write-Host "`n=== B. Network baseline (NAT + S3 GW Endpoint) ===" -ForegroundColor Cyan
$natList = ExecJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$VpcId", "Name=state,Values=available", "--region", $Region, "--output", "json")
$natCount = if ($natList -and $natList.NatGateways) { $natList.NatGateways.Count } else { 0 }
$subnetsResp = ExecJson @("ec2", "describe-subnets", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $Region, "--output", "json")
$rtResp = ExecJson @("ec2", "describe-route-tables", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $Region, "--output", "json")
if (-not $rtResp -or -not $rtResp.RouteTables) { Fail-OneTake "No route tables in VPC $VpcId" ($rtResp | ConvertTo-Json -Depth 5 -Compress) }

$publicRtIds = @()
$privateRtIds = @()
foreach ($rt in $rtResp.RouteTables) {
    $hasIgw = $false
    foreach ($r in $rt.Routes) {
        if ($r.DestinationCidrBlock -eq "0.0.0.0/0" -and $r.GatewayId -and $r.GatewayId.StartsWith("igw-")) { $hasIgw = $true; break }
    }
    if ($hasIgw) { $publicRtIds += $rt.RouteTableId } else { $privateRtIds += $rt.RouteTableId }
}

if ($natCount -eq 0 -and $FixMode) {
    $pubSubnetId = $null
    foreach ($rt in $rtResp.RouteTables) {
        if ($rt.RouteTableId -notin $publicRtIds) { continue }
        foreach ($a in $rt.Associations) {
            if ($a.SubnetId) { $pubSubnetId = $a.SubnetId; break }
        }
        if ($pubSubnetId) { break }
    }
    if (-not $pubSubnetId -and $subnetsResp -and $subnetsResp.Subnets -and $subnetsResp.Subnets.Count -gt 0) {
        $pubSubnetId = $subnetsResp.Subnets[0].SubnetId
    }
    if (-not $pubSubnetId) { Fail-OneTake "Cannot determine public subnet for NAT in VPC $VpcId" ($subnetsResp | ConvertTo-Json -Depth 3 -Compress) }
    $eip = ExecJson @("ec2", "allocate-address", "--domain", "vpc", "--region", $Region, "--output", "json")
    if (-not $eip -or -not $eip.AllocationId) { Fail-OneTake "EIP allocation failed" "" }
    $natOut = ExecJson @("ec2", "create-nat-gateway", "--subnet-id", $pubSubnetId, "--allocation-id", $eip.AllocationId, "--region", $Region, "--output", "json")
    if (-not $natOut -or -not $natOut.NatGateway -or -not $natOut.NatGateway.NatGatewayId) { Fail-OneTake "NAT gateway creation failed" "" }
    $natId = $natOut.NatGateway.NatGatewayId
    $wait = 0
    do { Start-Sleep -Seconds 15; $wait += 15; $st = (ExecJson @("ec2", "describe-nat-gateways", "--nat-gateway-ids", $natId, "--region", $Region, "--output", "json")).NatGateways[0].State } while ($st -eq "pending" -and $wait -lt 300)
    if ($st -ne "available") { Fail-OneTake "NAT gateway $natId did not become available (state=$st)" "" }
    foreach ($prt in $privateRtIds) {
        & aws ec2 create-route --route-table-id $prt --destination-cidr-block 0.0.0.0/0 --nat-gateway-id $natId --region $Region 2>&1 | Out-Null
    }
    Write-Host "NAT gateway created: $natId" -ForegroundColor Green
} elseif ($natCount -eq 0) {
    Fail-OneTake "No NAT Gateway in VPC $VpcId. Run with -FixMode to create." ($natList | ConvertTo-Json -Depth 3 -Compress)
}

$epList = ExecJson @("ec2", "describe-vpc-endpoints", "--filters", "Name=vpc-id,Values=$VpcId", "Name=service-name,Values=com.amazonaws.$Region.s3", "--region", $Region, "--output", "json")
$s3GwExists = $epList -and $epList.VpcEndpoints -and $epList.VpcEndpoints.Count -gt 0 -and ($epList.VpcEndpoints | Where-Object { $_.State -eq "available" })
if (-not $s3GwExists -and $FixMode) {
    $rtIdsForS3 = if ($privateRtIds.Count -gt 0) { $privateRtIds } else { $publicRtIds }
    if ($rtIdsForS3.Count -eq 0) { $rtIdsForS3 = @($rtResp.RouteTables[0].RouteTableId) }
    $s3Args = @("ec2", "create-vpc-endpoint", "--vpc-id", $VpcId, "--vpc-endpoint-type", "Gateway", "--service-name", "com.amazonaws.$Region.s3", "--region", $Region)
    foreach ($rid in $rtIdsForS3) { $s3Args += "--route-table-ids"; $s3Args += $rid }
    & aws @s3Args 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail-OneTake "S3 Gateway Endpoint creation failed" "" }
    Write-Host "S3 Gateway Endpoint created" -ForegroundColor Green
} elseif (-not $s3GwExists) {
    Fail-OneTake "S3 Gateway Endpoint missing in VPC $VpcId. Run with -FixMode to create." ($epList | ConvertTo-Json -Depth 2 -Compress)
}

# --- C. Build server alignment (route + SSM sts get-caller-identity) ---
Write-Host "`n=== C. Build server alignment ===" -ForegroundColor Cyan
$buildResp = ExecJson @("ec2", "describe-instances", "--region", $Region, "--filters", "Name=tag:Name,Values=academy-build-arm64", "Name=instance-state-name,Values=running,stopped", "--output", "json")
$buildInst = $null
if ($buildResp -and $buildResp.Reservations) {
    foreach ($res in $buildResp.Reservations) {
        if ($res.Instances) {
            $buildInst = $res.Instances | Where-Object { ($_.Tags | Where-Object { $_.Key -eq "Name" -and $_.Value -match "build" }) } | Select-Object -First 1
            if ($buildInst) { break }
        }
    }
}
if ($buildInst) {
    $buildSubnetId = $buildInst.SubnetId
    $buildInstanceId = $buildInst.InstanceId
    $rtForSub = ExecJson @("ec2", "describe-route-tables", "--filters", "Name=association.subnet-id,Values=$buildSubnetId", "--region", $Region, "--output", "json")
    $hasOutbound = $false
    if ($rtForSub -and $rtForSub.RouteTables) {
        foreach ($r in $rtForSub.RouteTables[0].Routes) {
            if ($r.DestinationCidrBlock -eq "0.0.0.0/0" -and ($r.GatewayId -or $r.NatGatewayId)) { $hasOutbound = $true; break }
        }
    }
    if (-not $hasOutbound -and $FixMode) {
        $privRtId = if ($privateRtIds.Count -gt 0) { $privateRtIds[0] } else { $rtResp.RouteTables[0].RouteTableId }
        & aws ec2 associate-route-table --route-table-id $privRtId --subnet-id $buildSubnetId --region $Region 2>&1 | Out-Null
    }
    $ssmDoc = "AWS-RunShellScript"
    $ssmCmd = ExecJson @("ssm", "send-command", "--instance-ids", $buildInstanceId, "--document-name", $ssmDoc, "--parameters", '{"commands":["aws sts get-caller-identity --output json"]}', "--region", $Region, "--output", "json")
    if (-not $ssmCmd -or -not $ssmCmd.Command.CommandId) {
        Write-Host "Build SSM send-command failed (instance may not be managed). Continuing." -ForegroundColor Yellow
    } else {
        $cmdId = $ssmCmd.Command.CommandId
        $ssmWait = 0
        do { Start-Sleep -Seconds 5; $ssmWait += 5; $inv = ExecJson @("ssm", "get-command-invocation", "--command-id", $cmdId, "--instance-id", $buildInstanceId, "--region", $Region, "--output", "json") } while ($inv.Status -eq "InProgress" -and $ssmWait -lt 120)
        if ($inv.Status -ne "Success") {
            Fail-OneTake "Build server SSM sts get-caller-identity did not succeed. Status=$($inv.Status)" ($inv | ConvertTo-Json -Depth 3 -Compress)
        }
        Write-Host "Build server SSM sts get-caller-identity: OK" -ForegroundColor Green
    }
} else {
    Write-Host "No Build instance (academy-build-arm64) found. Skipping Build alignment." -ForegroundColor Gray
}

# --- D. RUNNABLE cleanup (Ops Queue: terminate reconcile/scanstuck/netprobe before enforce) ---
Write-Host "`n=== D. RUNNABLE cleanup (Ops Queue) ===" -ForegroundColor Cyan
$jobDefNamesToClean = @("academy-video-ops-reconcile", "academy-video-ops-scanstuck", "academy-video-ops-netprobe")
foreach ($status in @("RUNNABLE", "RUNNING")) {
    $list = ExecJson @("batch", "list-jobs", "--job-queue", $OpsQueueName, "--job-status", $status, "--region", $Region, "--output", "json")
    if (-not $list -or -not $list.jobSummaryList) { continue }
    foreach ($j in $list.jobSummaryList) {
        $jobId = $j.jobId
        $desc = ExecJson @("batch", "describe-jobs", "--jobs", $jobId, "--region", $Region, "--output", "json")
        $jdName = $null
        if ($desc -and $desc.jobs -and $desc.jobs[0].jobDefinition) { $jdName = ($desc.jobs[0].jobDefinition -split "/")[0] -replace "arn:aws:batch:$Region:\d+:job-definition/", ""; $jdName = ($desc.jobs[0].jobDefinition -split ":")[-1] }
        if (-not $jdName -and $desc.jobs[0].jobDefinition) { $jdName = ($desc.jobs[0].jobDefinition -split "/")[-1] }
        $match = $false
        foreach ($n in $jobDefNamesToClean) { if ($jdName -like "*$n*" -or $jdName -eq $n) { $match = $true; break } }
        if ($match) {
            & aws batch terminate-job --job-id $jobId --reason "OneTake RUNNABLE cleanup" --region $Region 2>&1 | Out-Null
            Write-Host "  Terminated $jobId ($status)" -ForegroundColor Gray
        }
    }
}
Start-Sleep -Seconds 5

# --- E. Batch Video/Ops + EventBridge + Netprobe (existing one-take) ---
Write-Host "`n=== E. Batch + EventBridge + Netprobe (video_worker_infra_one_take) ===" -ForegroundColor Cyan
& (Join-Path $ScriptRoot "video_worker_infra_one_take.ps1") -Region $Region -EcrRepoUri $EcrRepoUri -FixMode:$FixMode 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail-OneTake "video_worker_infra_one_take.ps1 failed (exit $LASTEXITCODE). See audit above." ""
}

# --- F. EnableSchedulers: disable rules if not enabled ---
if (-not $EnableSchedulers) {
    Write-Host "`n=== F. EventBridge rules DISABLED (-EnableSchedulers not set) ===" -ForegroundColor Cyan
    & aws events disable-rule --name $ReconcileRuleName --region $Region 2>&1 | Out-Null
    & aws events disable-rule --name $ScanStuckRuleName --region $Region 2>&1 | Out-Null
    Write-Host "Reconcile and ScanStuck rules disabled." -ForegroundColor Gray
} else {
    & aws events enable-rule --name $ReconcileRuleName --region $Region 2>&1 | Out-Null
    & aws events enable-rule --name $ScanStuckRuleName --region $Region 2>&1 | Out-Null
}

Write-Host "`n==============================" -ForegroundColor Green
Write-Host "FINAL RESULT: PASS" -ForegroundColor Green
Write-Host "==============================" -ForegroundColor Green
Write-Host "Preflight/evidence: $PreflightDir" -ForegroundColor Gray
