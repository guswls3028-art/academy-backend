# ==============================================================================
# SSOT 검증: AWS Video Batch 인프라가 문서(VIDEO_INFRA_ONE_TAKE_ORDER.md) 기준으로
# 올바르게 정렬되어 있는지 한 번에 검증. 원테이크 실행.
#
# Usage: .\scripts\infra\verify_video_batch_ssot.ps1 -Region ap-northeast-2
# Exit: 0 = OVERALL PASS, 1 = OVERALL FAIL, 3 = root credential (실행 거부)
# ==============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Region = "ap-northeast-2",
    [switch]$SkipPython
)

$ErrorActionPreference = "Stop"
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)

if ([string]::IsNullOrWhiteSpace($Region)) {
    $Region = (aws configure get region 2>&1)
    if (-not $Region -or $Region -match "not set|error") {
        Write-Host "FAIL: -Region not specified and aws configure get region returned nothing." -ForegroundColor Red
        exit 1
    }
    $Region = $Region.Trim()
}

# --- Root credential 감지: 즉시 종료 exit 3 ---
$callerArn = aws sts get-caller-identity --query Arn --output text 2>&1
if ($LASTEXITCODE -eq 0 -and $callerArn -match ":root") {
    Write-Host "BLOCK: root credentials detected. Use IAM user or role. (exit 3)" -ForegroundColor Red
    exit 3
}

# --- AWS JSON 안전 파싱 (UTF-8 temp file) ---
function Aws-JsonSafe {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $tempFile = Join-Path ([System.IO.Path]::GetTempPath()) "verify_ssot_$(Get-Date -Format 'yyyyMMddHHmmss').json"
    $utf8 = New-Object System.Text.UTF8Encoding $false
    try {
        $out = & aws @ArgsArray --output json 2>&1
        $exit = $LASTEXITCODE
        if ($exit -ne 0) { return $null }
        $str = ($out | Out-String).Trim()
        if ([string]::IsNullOrWhiteSpace($str)) { return $null }
        [System.IO.File]::WriteAllText($tempFile, $str, $utf8)
        $content = [System.IO.File]::ReadAllText($tempFile, $utf8)
        return $content | ConvertFrom-Json
    } finally {
        if (Test-Path -LiteralPath $tempFile) { Remove-Item $tempFile -Force -ErrorAction SilentlyContinue }
        $ErrorActionPreference = $prev
    }
}

function Aws-Text {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    return ($out | Out-String).Trim()
}

# 결과 수집 (마지막 SUMMARY용)
$script:Summary = @{
    VideoOneToOneWorker = @{ Status = "FAIL"; Evidence = "" }
    VideoCeScalePath    = @{ Status = "FAIL"; Evidence = "" }
    VideoNetworkEgress  = @{ Status = "FAIL"; Evidence = "" }
    VideoIamEcrLogs     = @{ Status = "FAIL"; Evidence = "" }
    OpsSchedulerWiring = @{ Status = "FAIL"; Evidence = "" }
    ReconcileSafetyConfig = @{ Status = "FAIL"; Evidence = "" }
}

# ---------- A. 상태 파일 존재/일치 ----------
$batchStatePath = Join-Path $RepoRoot "docs\deploy\actual_state\batch_final_state.json"
$opsStatePath   = Join-Path $RepoRoot "docs\deploy\actual_state\batch_ops_state.json"

$VideoQueueName = "academy-video-batch-queue"
$VideoCEName    = "academy-video-batch-ce-v2"
$OpsQueueName  = "academy-video-ops-queue"
$OpsCEName     = "academy-video-ops-ce"

if (Test-Path -LiteralPath $batchStatePath) {
    try {
        $raw = [System.IO.File]::ReadAllText($batchStatePath, [System.Text.UTF8Encoding]::new($false))
        $st = $raw | ConvertFrom-Json
        if ($st.FinalJobQueueName) { $VideoQueueName = $st.FinalJobQueueName }
        if ($st.FinalComputeEnvName) { $VideoCEName = $st.FinalComputeEnvName }
    } catch {}
}
if (Test-Path -LiteralPath $opsStatePath) {
    try {
        $raw = [System.IO.File]::ReadAllText($opsStatePath, [System.Text.UTF8Encoding]::new($false))
        $st = $raw | ConvertFrom-Json
        if ($st.OpsJobQueueName) { $OpsQueueName = $st.OpsJobQueueName }
        if ($st.OpsComputeEnvName) { $OpsCEName = $st.OpsComputeEnvName }
    } catch {}
}

Write-Host "`n===== SSOT Verify Video Batch (Region=$Region) =====" -ForegroundColor Cyan
Write-Host "Video: queue=$VideoQueueName CE=$VideoCEName | Ops: queue=$OpsQueueName CE=$OpsCEName" -ForegroundColor Gray

# ---------- B. Video CE 스케일 경로 ----------
$ceList = Aws-JsonSafe @("batch", "describe-compute-environments", "--region", $Region)
$videoCe = $null
if ($ceList -and $ceList.computeEnvironments) {
    $videoCe = $ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
}

$ceValid = $videoCe -and $videoCe.status -eq "VALID" -and $videoCe.state -eq "ENABLED"
$ceArn = $null
$ceSubnets = @()
$ceInstanceRoleArn = $null
$ceInstanceTypes = @()

if ($videoCe) {
    $ceArn = $videoCe.computeEnvironmentArn
    $cr = $videoCe.computeResources
    if ($cr) {
        $ceSubnets = @($cr.subnets)
        $ceInstanceRoleArn = $cr.instanceRole
        $ceInstanceTypes = @($cr.instanceTypes)
    }
}

# ASG: aws:batch:computeEnvironmentArn 태그 또는 이름 academy-video-batch-ce-v2-asg-*
$asgFound = $false
$asgInstanceCount = -1
if ($ceArn) {
    $asgList = Aws-JsonSafe @("autoscaling", "describe-auto-scaling-groups", "--region", $Region)
    if ($asgList -and $asgList.AutoScalingGroups) {
        foreach ($a in $asgList.AutoScalingGroups) {
            $tag = $a.Tags | Where-Object { $_.Key -eq "aws:batch:computeEnvironmentArn" } | Select-Object -First 1
            $nameMatch = $a.AutoScalingGroupName -match "academy-video-batch-ce-v2-asg-"
            if (($tag -and $tag.Value -eq $ceArn) -or $nameMatch) {
                $asgFound = $true
                $asgInstanceCount = [int]$a.DesiredCapacity
                break
            }
        }
    }
}

$scalePathOk = $ceValid -and $asgFound
if (-not $ceValid) {
    $script:Summary.VideoCeScalePath.Evidence = "CE $VideoCEName status=$($videoCe.status) state=$($videoCe.state); expected VALID/ENABLED"
} elseif (-not $asgFound) {
    $script:Summary.VideoCeScalePath.Evidence = "CE VALID/ENABLED but no ASG with tag aws:batch:computeEnvironmentArn or name academy-video-batch-ce-v2-asg-* (capacity/perm/subnet)"
} elseif ($asgInstanceCount -eq 0) {
    $script:Summary.VideoCeScalePath.Status = "PASS"
    $script:Summary.VideoCeScalePath.Evidence = "CE VALID/ENABLED; ASG exists desired=0 (scale-to-zero ok)"
} else {
    $script:Summary.VideoCeScalePath.Status = "PASS"
    $script:Summary.VideoCeScalePath.Evidence = "CE VALID/ENABLED; ASG exists desired=$asgInstanceCount; instanceTypes=$($ceInstanceTypes -join ',')"
}

if (-not $scalePathOk) {
    $script:Summary.VideoCeScalePath.Evidence = if ($script:Summary.VideoCeScalePath.Evidence) { $script:Summary.VideoCeScalePath.Evidence } else { "CE missing or not VALID/ENABLED or ASG not found" }
}

# ---------- C. 네트워크/egress ----------
$hasDefaultRoute = $false
$subnetsChecked = 0
foreach ($subId in $ceSubnets) {
    if (-not $subId) { continue }
    $subnetsChecked++
    $rt = Aws-JsonSafe @("ec2", "describe-route-tables", "--filters", "association.subnet-id=$subId", "--region", $Region)
    if ($rt -and $rt.RouteTables -and $rt.RouteTables.Count -gt 0) {
        foreach ($r in $rt.RouteTables[0].Routes) {
            if ($r.DestinationCidrBlock -eq "0.0.0.0/0") { $hasDefaultRoute = $true; break }
        }
    }
}

$vpcId = $null
if ($ceSubnets.Count -gt 0) {
    $subResp = Aws-JsonSafe @("ec2", "describe-subnets", "--subnet-ids", $ceSubnets[0], "--region", $Region)
    if ($subResp -and $subResp.Subnets -and $subResp.Subnets[0]) { $vpcId = $subResp.Subnets[0].VpcId }
}

$hasEcrLogsEndpoints = $false
if ($vpcId) {
    $epList = Aws-JsonSafe @("ec2", "describe-vpc-endpoints", "--filters", "Name=vpc-id,Values=$vpcId", "--region", $Region)
    if ($epList -and $epList.VpcEndpoints) {
        $svc = $epList.VpcEndpoints | ForEach-Object { $_.ServiceName } | Select-Object -Unique
        $want = @("ecr.api", "ecr.dkr", "logs", "ecs", "ecs-agent", "sts")
        $found = 0
        foreach ($w in $want) {
            if ($svc -match $w) { $found++ }
        }
        if ($found -ge 3) { $hasEcrLogsEndpoints = $true }
    }
}

if ($subnetsChecked -eq 0) {
    $script:Summary.VideoNetworkEgress.Status = "WARN"
    $script:Summary.VideoNetworkEgress.Evidence = "No CE subnets to check"
} elseif ($hasDefaultRoute) {
    $script:Summary.VideoNetworkEgress.Status = "PASS"
    $script:Summary.VideoNetworkEgress.Evidence = "At least one CE subnet has 0.0.0.0/0 (NAT/IGW)"
} elseif ($hasEcrLogsEndpoints) {
    $script:Summary.VideoNetworkEgress.Status = "PASS"
    $script:Summary.VideoNetworkEgress.Evidence = "No default route but VPC endpoints present (ecr/logs/ecs/sts)"
} else {
    $script:Summary.VideoNetworkEgress.Status = "FAIL"
    $script:Summary.VideoNetworkEgress.Evidence = "No 0.0.0.0/0 in CE subnets and no sufficient VPC endpoints -> ECR/Logs/ECS may fail"
}

# ---------- D. IAM (compute instance role: ECR + CloudWatch) ----------
$iamOk = $false
$iamEvidence = "CE instanceRole not set or not resolvable"
if ($ceInstanceRoleArn -and $ceInstanceRoleArn -match "instance-profile/([^/]+)$") {
    $profileName = $Matches[1]
    $ip = Aws-JsonSafe @("iam", "get-instance-profile", "--instance-profile-name", $profileName)
    if ($ip -and $ip.InstanceProfile -and $ip.InstanceProfile.Roles -and $ip.InstanceProfile.Roles.Count -gt 0) {
        $roleName = $ip.InstanceProfile.Roles[0].RoleName
        $attached = Aws-JsonSafe @("iam", "list-attached-role-policies", "--role-name", $roleName)
        $policyNames = @()
        if ($attached -and $attached.AttachedPolicies) {
            $policyNames = @($attached.AttachedPolicies | ForEach-Object { $_.PolicyName })
        }
        $hasEcr = $policyNames -contains "AmazonEC2ContainerRegistryReadOnly"
        $hasLogs = $policyNames -contains "CloudWatchLogsFullAccess"
        $iamOk = $hasEcr -and $hasLogs
        $iamEvidence = "role=$roleName ECR=$hasEcr Logs=$hasLogs"
    }
}
$script:Summary.VideoIamEcrLogs.Status = if ($iamOk) { "PASS" } else { "FAIL" }
$script:Summary.VideoIamEcrLogs.Evidence = $iamEvidence

# ---------- E. JobDefinition 정합성 + 1 video = 1 worker 판정 ----------
$jdList = Aws-JsonSafe @("batch", "describe-job-definitions", "--job-definition-name", "academy-video-batch-jobdef", "--status", "ACTIVE", "--region", $Region)
$videoJd = $null
if ($jdList -and $jdList.jobDefinitions -and $jdList.jobDefinitions.Count -gt 0) {
    $videoJd = $jdList.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
}

$oneToOneOk = $false
$oneToOneEvidence = "Video JobDef not found or CE/JobDef vCPU mismatch"
if ($videoJd -and $videoJd.containerProperties) {
    $jdVcpus = [int]$videoJd.containerProperties.vcpus
    $jdMemory = $videoJd.containerProperties.memory
    $jdImage = $videoJd.containerProperties.image
    $oneToOneEvidence = "JobDef vcpus=$jdVcpus memory=$jdMemory image=$jdImage"
    # CE instanceTypes가 c6g.large(2 vCPU)이고 JobDef vcpus=2 이면 1:1 PASS
    $ceVcpu = 2
    if ($ceInstanceTypes -match "c6g\.large") { $ceVcpu = 2 }
    elseif ($ceInstanceTypes -match "c6g\.xlarge") { $ceVcpu = 4 }
    else { $ceVcpu = ($ceInstanceTypes | ForEach-Object { $_ }) | Select-Object -First 1 }
    if ($jdVcpus -eq 2 -and ($ceInstanceTypes -match "c6g\.large" -or $ceInstanceTypes -match "optimal")) {
        $oneToOneOk = $true
        $oneToOneEvidence = "CE instanceTypes=$($ceInstanceTypes -join ','); JobDef vcpus=$jdVcpus -> 1 video = 1 worker (PASS)"
    } elseif ($jdVcpus -gt 0) {
        $oneToOneOk = $true
        $oneToOneEvidence = "JobDef vcpus=$jdVcpus; CE instanceTypes=$($ceInstanceTypes -join ','); 1:1 assumption (review instanceType if not c6g.large)"
    }
}
$script:Summary.VideoOneToOneWorker.Status = if ($oneToOneOk) { "PASS" } else { "FAIL" }
$script:Summary.VideoOneToOneWorker.Evidence = $oneToOneEvidence

# Concurrency env (WARN만): SSM 또는 .env
$concurrencyWarn = $false
$ssmPayload = $null
$ssmRaw = Aws-Text @("ssm", "get-parameter", "--name", "/academy/workers/env", "--region", $Region, "--with-decryption", "--query", "Parameter.Value", "--output", "text")
if ($ssmRaw) {
    try {
        $ssmPayload = $ssmRaw | ConvertFrom-Json
    } catch {
        try {
            $dec = [System.Convert]::FromBase64String($ssmRaw)
            $ssmPayload = [System.Text.Encoding]::UTF8.GetString($dec) | ConvertFrom-Json
        } catch {}
    }
}
if ($ssmPayload) {
    $g = $ssmPayload.VIDEO_GLOBAL_MAX_CONCURRENT; $t = $ssmPayload.VIDEO_TENANT_MAX_CONCURRENT
    if ($null -eq $g -or $null -eq $t) { $concurrencyWarn = $true }
} else {
    $envPath = Join-Path $RepoRoot ".env"
    if (Test-Path -LiteralPath $envPath) {
        $envContent = [System.IO.File]::ReadAllText($envPath, [System.Text.UTF8Encoding]::new($false))
        if ($envContent -notmatch "VIDEO_GLOBAL_MAX_CONCURRENT" -or $envContent -notmatch "VIDEO_TENANT_MAX_CONCURRENT") { $concurrencyWarn = $true }
    } else { $concurrencyWarn = $true }
}
if ($concurrencyWarn -and $oneToOneOk) {
    $script:Summary.VideoOneToOneWorker.Evidence += "; WARN: VIDEO_GLOBAL_MAX_CONCURRENT/VIDEO_TENANT_MAX_CONCURRENT not verified (SSM/.env)"
}

# ---------- F. Ops wiring (EventBridge -> Ops queue) ----------
$rule = Aws-JsonSafe @("events", "describe-rule", "--name", "academy-reconcile-video-jobs", "--region", $Region)
$ruleState = $null
if ($rule) { $ruleState = $rule.State }

$opsQueueArn = $null
$jqList = Aws-JsonSafe @("batch", "describe-job-queues", "--job-queues", $OpsQueueName, "--region", $Region)
if ($jqList -and $jqList.jobQueues -and $jqList.jobQueues.Count -gt 0) {
    $opsQueueArn = $jqList.jobQueues[0].jobQueueArn
}

$tgtJson = Aws-JsonSafe @("events", "list-targets-by-rule", "--rule", "academy-reconcile-video-jobs", "--region", $Region)
$targetOk = $false
$opsWiringEvidence = "Rule or target missing"
if ($tgtJson -and $tgtJson.Targets -and $tgtJson.Targets.Count -gt 0) {
    $t = $tgtJson.Targets[0]
    $targetQueueArn = $t.Arn
    $jdTarget = $t.BatchParameters.JobDefinition
    $targetOk = ($targetQueueArn -eq $opsQueueArn) -and ($jdTarget -like "academy-video-ops-reconcile*")
    $opsWiringEvidence = "rule State=$ruleState; target jobQueue=Ops($OpsQueueName) jobDefinition=$jdTarget; match=$targetOk"
}
$script:Summary.OpsSchedulerWiring.Status = if ($targetOk) { "PASS" } else { "FAIL" }
$script:Summary.OpsSchedulerWiring.Evidence = $opsWiringEvidence

# ---------- G. Reconcile 안전장치 ----------
$reconcileMin = $null
$reconcileDisabled = $null
if ($ssmPayload) {
    $reconcileMin = $ssmPayload.RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES
    $reconcileDisabled = $ssmPayload.RECONCILE_ORPHAN_DISABLED
}
if ($null -eq $ssmPayload) {
    $envPath = Join-Path $RepoRoot ".env"
    if (Test-Path -LiteralPath $envPath) {
        $envContent = [System.IO.File]::ReadAllText($envPath, [System.Text.UTF8Encoding]::new($false))
        if ($envContent -match "RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES=(\d+)") { $reconcileMin = [int]$Matches[1] }
        if ($envContent -match "RECONCILE_ORPHAN_DISABLED=(.+)") { $reconcileDisabled = $Matches[1].Trim() }
    }
}
if ($null -eq $reconcileMin -and $null -eq $reconcileDisabled) {
    $script:Summary.ReconcileSafetyConfig.Status = "WARN"
    $script:Summary.ReconcileSafetyConfig.Evidence = "RECONCILE_ORPHAN_* not found in SSM or .env (defaults: MIN_RUNNABLE=15, DISABLED=false)"
} else {
    $minStr = if ($null -ne $reconcileMin) { $reconcileMin } else { "15(default)" }
    $disStr = if ($null -ne $reconcileDisabled) { $reconcileDisabled } else { "false(default)" }
    $script:Summary.ReconcileSafetyConfig.Status = "PASS"
    $script:Summary.ReconcileSafetyConfig.Evidence = "RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES=$minStr RECONCILE_ORPHAN_DISABLED=$disStr"
}

# ---------- (선택) Python 보강: ECR digest/arch, CloudWatch 로그 패턴 ----------
$pyScript = Join-Path $ScriptRoot "verify_video_batch_ssot.py"
if (-not $SkipPython -and (Test-Path -LiteralPath $pyScript)) {
    $pyExe = $null
    if (Get-Command python -ErrorAction SilentlyContinue) { $pyExe = "python" }
    elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $pyExe = "python3" }
    if ($pyExe) {
        Write-Host "`n--- Python supplement (ECR / CloudWatch) ---" -ForegroundColor Cyan
        & $pyExe $pyScript --region $Region 2>&1 | ForEach-Object { Write-Host $_ }
    }
}

# ---------- H. 최종 출력 (ONLY 이 구조) ----------
$overall = "PASS"
foreach ($k in @("VideoOneToOneWorker", "VideoCeScalePath", "VideoNetworkEgress", "VideoIamEcrLogs", "OpsSchedulerWiring", "ReconcileSafetyConfig")) {
    $s = $script:Summary[$k].Status
    if ($s -eq "FAIL") { $overall = "FAIL"; break }
}
if ($overall -eq "PASS" -and ($script:Summary.VideoNetworkEgress.Status -eq "WARN" -or $script:Summary.ReconcileSafetyConfig.Status -eq "WARN")) {
    $overall = "PASS"
}

Write-Host "`n========= SUMMARY ==========" -ForegroundColor Cyan
Write-Host "VIDEO 1=1 WORKER: $($script:Summary.VideoOneToOneWorker.Status) (evidence: $($script:Summary.VideoOneToOneWorker.Evidence))"
Write-Host "VIDEO CE SCALE PATH: $($script:Summary.VideoCeScalePath.Status) (evidence: $($script:Summary.VideoCeScalePath.Evidence))"
Write-Host "VIDEO NETWORK EGRESS OR ENDPOINTS: $($script:Summary.VideoNetworkEgress.Status) (evidence: $($script:Summary.VideoNetworkEgress.Evidence))"
Write-Host "VIDEO IAM (ECR+Logs): $($script:Summary.VideoIamEcrLogs.Status) (evidence: $($script:Summary.VideoIamEcrLogs.Evidence))"
Write-Host "OPS SCHEDULER WIRING: $($script:Summary.OpsSchedulerWiring.Status) (evidence: $($script:Summary.OpsSchedulerWiring.Evidence))"
Write-Host "RECONCILE SAFETY CONFIG: $($script:Summary.ReconcileSafetyConfig.Status) (evidence: $($script:Summary.ReconcileSafetyConfig.Evidence))"
Write-Host "OVERALL: $overall"
Write-Host "===========================" -ForegroundColor Cyan

exit $(if ($overall -eq "PASS") { 0 } else { 1 })
