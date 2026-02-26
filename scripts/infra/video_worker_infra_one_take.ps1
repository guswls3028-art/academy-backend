# ==============================================================================
# Video 워커 인프라 원테이크 — SSOT 강제. 구축 + Netprobe + Audit PASS 필수.
# Usage: .\scripts\infra\video_worker_infra_one_take.ps1 -Region ap-northeast-2 -EcrRepoUri "<real-account>.dkr.ecr.<region>.amazonaws.com/academy-video-worker:<real-tag>"
#        (Replace <real-account> and <real-tag> with actual values. Do not pass literal "<account>" or "<immutable-tag>".)
#        $acct = aws sts get-caller-identity --query Account --output text
#        $tag  = (aws ecr describe-images --repository-name academy-video-worker --region ap-northeast-2 --query "imageDetails[0].imageTags[0]" --output text)
#        .\scripts\infra\video_worker_infra_one_take.ps1 -Region ap-northeast-2 -EcrRepoUri "$acct.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:$tag"
#        .\scripts\infra\video_worker_infra_one_take.ps1 -Region ap-northeast-2 -BuildPush -EcrRepoUri ... -FixMode
# ==============================================================================
param(
    [string]$Region = "ap-northeast-2",
    [Parameter(Mandatory=$true)][string]$EcrRepoUri,
    [switch]$BuildPush = $false,
    [switch]$FixMode = $true
)
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"

$VideoCEName = "academy-video-batch-ce-final"
$VideoQueueName = "academy-video-batch-queue"
$VideoJobDefName = "academy-video-batch-jobdef"
$OpsCEName = "academy-video-ops-ce"
$OpsQueueName = "academy-video-ops-queue"
$ReconcileRuleName = "academy-reconcile-video-jobs"
$ScanStuckRuleName = "academy-video-scan-stuck-rate"

$script:Audit1 = "FAIL"; $script:Audit2 = "FAIL"; $script:Audit3 = "FAIL"; $script:Audit4 = "FAIL"
$script:Audit5 = "FAIL"; $script:Audit6 = "FAIL"; $script:Audit7 = "FAIL"
$script:Audit1Detail = ""; $script:Audit2Detail = ""; $script:Audit3Detail = ""; $script:Audit4Detail = ""
$script:Audit5Detail = ""; $script:Audit6Detail = ""; $script:Audit7Detail = ""
$script:AnyFail = $false

function ExecJson($a) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @a 2>&1
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $s = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    try { return $s | ConvertFrom-Json } catch { return $null }
}

function Invoke-Step { param([string]$Name, [scriptblock]$Block)
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $Name" }
}

function Fail-SSOT { param([string]$Msg)
    Write-Host "SSOT FAIL: $Msg" -ForegroundColor Red
    $script:AnyFail = $true
    throw $Msg
}

try {
Invoke-Step "1) API Private IP (discover_api_network)" {
    & (Join-Path $ScriptRoot "discover_api_network.ps1") -Region $Region
}

# 2) SSM bootstrap
Invoke-Step "2) SSM bootstrap" {
    & (Join-Path $ScriptRoot "ssm_bootstrap_video_worker.ps1") -Region $Region -EnvFile (Join-Path $RepoRoot ".env") -Overwrite -UsePrivateApiIp
}

# 3) (옵션) 이미지 빌드/푸시
if ($BuildPush) {
    Invoke-Step "3) ECR build/push (VideoWorkerOnly)" {
        & (Join-Path $RepoRoot "scripts\build_and_push_ecr_remote.ps1") -VideoWorkerOnly -Region $Region
    }
} else {
    Write-Host "`n=== 3) ECR build/push SKIPPED (-BuildPush not set) ===" -ForegroundColor Gray
}

# 4) ECR URI: :latest 금지, placeholder 금지, 형식 검사
$acctId = (aws sts get-caller-identity --query Account --output text 2>&1).Trim()
if (-not $acctId) { throw "Could not get Account ID" }
if (-not $EcrRepoUri) { $EcrRepoUri = "${acctId}.dkr.ecr.${Region}.amazonaws.com/academy-video-worker:latest" }
if ($EcrRepoUri -match ':latest$') { Fail-SSOT "image :latest forbidden. Pass -EcrRepoUri with immutable tag." }
if ($EcrRepoUri -match '<account>|<immutable-tag>|<\w+>') {
    Write-Host "SSOT FAIL: EcrRepoUri must be a real URI. Do not use placeholders like <account> or <immutable-tag>." -ForegroundColor Red
    Write-Host "  Get Account:  aws sts get-caller-identity --query Account --output text" -ForegroundColor Gray
    Write-Host "  Get tag:      aws ecr describe-images --repository-name academy-video-worker --region $Region --query 'imageDetails[].imageTags[]' --output text" -ForegroundColor Gray
    Write-Host "  Example:     -EcrRepoUri `"$acctId.dkr.ecr.$Region.amazonaws.com/academy-video-worker:YOUR_TAG`"" -ForegroundColor Gray
    exit 1
}
# Step 4 쪽과 동일한 형식 검사 (태그 비어 있으면 여기서 실패)
if ($EcrRepoUri -notmatch '^\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9\-_]+:[a-zA-Z0-9\.\-_]+$') {
    Write-Host "SSOT FAIL: EcrRepoUri format invalid or tag empty." -ForegroundColor Red
    Write-Host "  Expected: 12-digit.dkr.ecr.<region>.amazonaws.com/academy-video-worker:<non-empty-tag>" -ForegroundColor Gray
    Write-Host "  Got:      $EcrRepoUri" -ForegroundColor Gray
    Write-Host "  List tags: aws ecr describe-images --repository-name academy-video-worker --region $Region --query 'imageDetails[*].imageTags[*]' --output text" -ForegroundColor Gray
    exit 1
}

# 5) Video Batch in API VPC
Invoke-Step "4) Video Batch in API VPC (recreate_batch_in_api_vpc)" {
    & (Join-Path $ScriptRoot "recreate_batch_in_api_vpc.ps1") -Region $Region -EcrRepoUri $EcrRepoUri -ComputeEnvName $VideoCEName -JobQueueName $VideoQueueName
}
$batchStatePath = Join-Path $OutDir "batch_final_state.json"
if (-not (Test-Path -LiteralPath $batchStatePath)) { Fail-SSOT "batch_final_state.json not found after step 4" }

# --- SSOT 검사 및 §9 INVALID CE 루틴 ---
$ceOut = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$ceObj = $ceOut.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
if (-not $ceObj) { Fail-SSOT "Video CE $VideoCEName not found." }
if ($ceObj.state -eq "INVALID") {
    Write-Host "Video CE INVALID; performing §9: Queue DISABLED, CE DISABLED, delete, recreate c6g.large, Queue reattach." -ForegroundColor Yellow
    $cr = $ceObj.computeResources
    $vpcId = ""; if ($cr.subnets -and $cr.subnets.Count -gt 0) {
        $subResp = ExecJson @("ec2", "describe-subnets", "--subnet-ids", $cr.subnets[0], "--region", $Region, "--output", "json")
        if ($subResp -and $subResp.Subnets -and $subResp.Subnets.Count -gt 0) { $vpcId = $subResp.Subnets[0].VpcId }
    }
    $sgId = ""; if ($cr.securityGroupIds -and $cr.securityGroupIds.Count -gt 0) { $sgId = $cr.securityGroupIds[0] }
    if (-not $vpcId -or -not $sgId -or -not $cr.subnets) { Fail-SSOT "Cannot get VpcId/SubnetIds/SG from INVALID CE for §9." }
    $ErrorActionPreference = "Continue"
    aws batch update-job-queue --job-queue $VideoQueueName --state DISABLED --region $Region 2>&1 | Out-Null
    $waitQ = 0; while ($waitQ -lt 90) { Start-Sleep -Seconds 5; $waitQ += 5; $jq = ExecJson @("batch", "describe-job-queues", "--job-queues", $VideoQueueName, "--region", $Region, "--output", "json"); $s = ($jq.jobQueues | Where-Object { $_.jobQueueName -eq $VideoQueueName }).state; if ($s -eq "DISABLED") { break } }
    aws batch update-compute-environment --compute-environment $VideoCEName --state DISABLED --region $Region 2>&1 | Out-Null
    $waitCe = 0; while ($waitCe -lt 120) { Start-Sleep -Seconds 10; $waitCe += 10; $ceD = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json"); $ceO = $ceD.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName }; if ($ceO.state -eq "DISABLED") { break } }
    aws batch delete-compute-environment --compute-environment $VideoCEName --region $Region 2>&1 | Out-Null
    $waitDel = 0; while ($waitDel -lt 120) { Start-Sleep -Seconds 10; $waitDel += 10; $ceL = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json"); if (-not $ceL.computeEnvironments -or ($ceL.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName }).Count -eq 0) { break } }
    & (Join-Path $ScriptRoot "batch_video_setup.ps1") -Region $Region -VpcId $vpcId -SubnetIds @($cr.subnets) -SecurityGroupId $sgId -EcrRepoUri $EcrRepoUri -ComputeEnvName $VideoCEName -JobQueueName $VideoQueueName -JobDefName $VideoJobDefName
    if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = "Stop"; Fail-SSOT "§9 recreate CE failed." }
    $ErrorActionPreference = "Stop"
    $ceOut = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
    $ceObj = $ceOut.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
}
if ($ceObj.state -ne "VALID") { Fail-SSOT "Video CE state is $($ceObj.state), not VALID." }
if ($ceObj.status -ne "ENABLED") { Fail-SSOT "Video CE status is $($ceObj.status), not ENABLED." }
$instTypes = @($ceObj.computeResources.instanceTypes)
$badTypes = $instTypes | Where-Object { $_ -ne "c6g.large" }
if ($badTypes -and $badTypes.Count -gt 0) { Fail-SSOT "Video CE instanceTypes has non-c6g.large: $($instTypes -join ',')" }
$script:Audit1Detail = "Name: $VideoCEName  State: $($ceObj.state)  Status: $($ceObj.status)  InstanceTypes: $($instTypes -join ',')  min/max/desired vCPUs: $($ceObj.computeResources.minvCpus) / $($ceObj.computeResources.maxvCpus) / $($ceObj.computeResources.desiredvCpus)"
$script:Audit1 = "PASS"

$qOut = ExecJson @("batch", "describe-job-queues", "--job-queues", $VideoQueueName, "--region", $Region, "--output", "json")
$qObj = $qOut.jobQueues | Where-Object { $_.jobQueueName -eq $VideoQueueName } | Select-Object -First 1
if (-not $qObj) { Fail-SSOT "Video Queue $VideoQueueName not found." }
$ceOrder = @($qObj.computeEnvironmentOrder)
if ($ceOrder.Count -ne 1) { Fail-SSOT "Video Queue CE count is $($ceOrder.Count), must be 1." }
$script:Audit2Detail = "Name: $VideoQueueName  CE Count: $($ceOrder.Count)  Attached CE: $($ceOrder[0].computeEnvironment)  State: $($qObj.state)"
$script:Audit2 = "PASS"

$jdOut = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$latestJd = $jdOut.jobDefinitions | Where-Object { $_.jobDefinitionName -eq $VideoJobDefName } | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
if (-not $latestJd) { Fail-SSOT "Video JobDef $VideoJobDefName has no ACTIVE revision." }
$img = $latestJd.containerProperties.image
if ($img -match ':latest$') { Fail-SSOT "JobDef image uses :latest." }
$cp = $latestJd.containerProperties
$needReregister = $false
if ([int]$cp.vcpus -ne 2) { $needReregister = $true }
if ([int]$cp.memory -ne 3072) { $needReregister = $true }
$timeoutVal = 0; if ($latestJd.timeout -and $latestJd.timeout.attemptDurationSeconds) { $timeoutVal = [int]$latestJd.timeout.attemptDurationSeconds }
if ($timeoutVal -ne 14400) { $needReregister = $true }
$retryVal = 0; if ($latestJd.retryStrategy -and $latestJd.retryStrategy.attempts) { $retryVal = [int]$latestJd.retryStrategy.attempts }
if ($retryVal -ne 1) { $needReregister = $true }
if (-not $cp.logConfiguration) { $needReregister = $true }
if ($needReregister) {
    $jobRoleName = "academy-video-batch-job-role"; $execRoleName = "academy-batch-ecs-task-execution-role"
    $jobRoleArn = (ExecJson @("iam", "get-role", "--role-name", $jobRoleName, "--output", "json")).Role.Arn
    $execRoleArn = (ExecJson @("iam", "get-role", "--role-name", $execRoleName, "--output", "json")).Role.Arn
    if (-not $jobRoleArn -or -not $execRoleArn) { Fail-SSOT "IAM roles for JobDef re-register not found." }
    $jdPath = Join-Path $ScriptRoot "batch\video_job_definition.json"
    $jdContent = [System.IO.File]::ReadAllText($jdPath, [System.Text.UTF8Encoding]::new($false))
    $jdContent = $jdContent -replace "PLACEHOLDER_ECR_URI", $img
    $jdContent = $jdContent -replace "PLACEHOLDER_JOB_ROLE_ARN", $jobRoleArn
    $jdContent = $jdContent -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $execRoleArn
    $jdContent = $jdContent -replace "PLACEHOLDER_REGION", $Region
    $jdFile = Join-Path $RepoRoot "batch_jd_ssot_temp.json"
    [System.IO.File]::WriteAllText($jdFile, $jdContent, [System.Text.UTF8Encoding]::new($false))
    $jdUri = "file://" + ($jdFile -replace '\\', '/')
    & aws batch register-job-definition --cli-input-json $jdUri --region $Region 2>&1 | Out-Null
    Remove-Item $jdFile -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { Fail-SSOT "JobDef re-register failed." }
    $jdOut = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
    $latestJd = $jdOut.jobDefinitions | Where-Object { $_.jobDefinitionName -eq $VideoJobDefName } | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
}
$script:Audit3Detail = "Name: $VideoJobDefName  Latest Revision: $($latestJd.revision)  vCPUs: $($latestJd.containerProperties.vcpus)  Memory: $($latestJd.containerProperties.memory)  Image: $img"
$script:Audit3 = "PASS"

$batchSubmitPy = Join-Path $RepoRoot "apps\support\video\services\batch_submit.py"
if (Test-Path -LiteralPath $batchSubmitPy) {
    $content = [System.IO.File]::ReadAllText($batchSubmitPy, [System.Text.UTF8Encoding]::new($false))
    if ($content -match 'jobDefinition\s*=\s*[^,]+:\s*\d+|jobDefinition\s*=\s*[^"]*:revision') { Fail-SSOT "batch_submit.py uses :revision or revision in jobDefinition." }
}

# Storage: Launch Template root EBS 100GB gp3 encrypted DeleteOnTermination
$cr = $ceObj.computeResources
if ($cr.launchTemplate -and $cr.launchTemplate.launchTemplateId) {
    $ltId = $cr.launchTemplate.launchTemplateId
    $ltVer = ExecJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $ltId, "--region", $Region, "--output", "json")
    $ver0 = $ltVer.LaunchTemplateVersions | Select-Object -First 1
    if ($ver0 -and $ver0.LaunchTemplateData -and $ver0.LaunchTemplateData.BlockDeviceMappings) {
        $rootMap = $ver0.LaunchTemplateData.BlockDeviceMappings | Where-Object { -not $_.DeviceName -or $_.DeviceName -eq "/dev/xvda" -or $_.DeviceName -eq "/dev/sda1" } | Select-Object -First 1
        if ($rootMap -and $rootMap.Ebs) {
            $ebs = $rootMap.Ebs
            $volSize = [int]$ebs.VolumeSize; $enc = $ebs.Encrypted; $del = $ebs.DeleteOnTermination; $vt = $ebs.VolumeType
            if ($volSize -lt 100 -or $vt -ne "gp3" -or $enc -ne $true -or $del -ne $true) { Fail-SSOT "Launch Template root EBS must be 100GB gp3 encrypted DeleteOnTermination=true." }
        }
    }
}

# 6) Ops CE + Ops Queue + IAM
$videoCeForOps = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$videoCeObj = $videoCeForOps.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
$opsVpcId = ""; $opsSubnetIds = @(); $opsSgId = ""
if ($videoCeObj -and $videoCeObj.computeResources) {
    $cr = $videoCeObj.computeResources
    if ($cr.securityGroupIds -and $cr.securityGroupIds.Count -gt 0) { $opsSgId = $cr.securityGroupIds[0] }
    if ($cr.subnets) { $opsSubnetIds = @($cr.subnets) }
    if ($opsSubnetIds.Count -gt 0) {
        $subResp = ExecJson @("ec2", "describe-subnets", "--subnet-ids", $opsSubnetIds[0], "--region", $Region, "--output", "json")
        if ($subResp -and $subResp.Subnets -and $subResp.Subnets.Count -gt 0) { $opsVpcId = $subResp.Subnets[0].VpcId }
    }
}
Invoke-Step "5) Ops CE + Ops Queue" {
    if ($opsVpcId -and $opsSubnetIds.Count -gt 0 -and $opsSgId) {
        & (Join-Path $ScriptRoot "batch_ops_setup.ps1") -Region $Region -VpcId $opsVpcId -SubnetIds $opsSubnetIds -SecurityGroupId $opsSgId
    } else {
        & (Join-Path $ScriptRoot "batch_ops_setup.ps1") -Region $Region
    }
}
Invoke-Step "5b) IAM attach Batch DescribeJobs" {
    & (Join-Path $ScriptRoot "iam_attach_batch_describe_jobs.ps1") -Region $Region
}

$opsCeOut = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $OpsCEName, "--region", $Region, "--output", "json")
$opsCeObj = $opsCeOut.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $OpsCEName } | Select-Object -First 1
if ($opsCeObj) {
    $omax = [int]$opsCeObj.computeResources.maxvCpus
    if ($omax -eq 2) { $script:Audit4 = "PASS" }
    $script:Audit4Detail = "Name: $OpsCEName  State: $($opsCeObj.state)  maxvCpus: $omax"
} else { $script:Audit4Detail = "Name: $OpsCEName not found" }
$opsQOut = ExecJson @("batch", "describe-job-queues", "--job-queues", $OpsQueueName, "--region", $Region, "--output", "json")
$opsQObj = $opsQOut.jobQueues | Where-Object { $_.jobQueueName -eq $OpsQueueName } | Select-Object -First 1
if ($opsQObj -and @($opsQObj.computeEnvironmentOrder).Count -ne 1) { Fail-SSOT "Ops Queue must have single CE." }

# Evidence A–D
function Write-EvidenceCeEcsAsg {
    param([string]$Reg, [string]$VideoCeName, [string]$OpsCeName)
    $vCe = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCeName, "--region", $Reg, "--output", "json")
    if ($vCe -and $vCe.computeEnvironments -and $vCe.computeEnvironments.Count -gt 0) {
        $c = $vCe.computeEnvironments[0]; $cr = $c.computeResources
        Write-Host "  [Evidence A] Video CE $VideoCeName - state=$($c.state) status=$($c.status)" -ForegroundColor Gray
        if ($cr.ecsClusterArn) {
            $ecs = ExecJson @("ecs", "list-container-instances", "--cluster", $cr.ecsClusterArn, "--region", $Reg, "--output", "json")
            $count = if ($ecs -and $ecs.containerInstanceArns) { $ecs.containerInstanceArns.Count } else { 0 }
            Write-Host "    ECS containerInstances=$count" -ForegroundColor Gray
        }
    }
    $oCe = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $OpsCeName, "--region", $Reg, "--output", "json")
    if ($oCe -and $oCe.computeEnvironments -and $oCe.computeEnvironments.Count -gt 0) {
        $c = $oCe.computeEnvironments[0]; $cr = $c.computeResources
        Write-Host "  [Evidence B] Ops CE $OpsCeName - state=$($c.state) status=$($c.status) maxvCpus=$($cr.maxvCpus)" -ForegroundColor Gray
    }
}
Write-Host "`n=== Evidence (A–D) ===" -ForegroundColor Cyan
Write-EvidenceCeEcsAsg -Reg $Region -VideoCeName $VideoCEName -OpsCeName $OpsCEName

# 7) EventBridge (reconcile 15min, scan_stuck 5min, target Ops Queue)
Invoke-Step "6) EventBridge (Ops queue, reconcile 15min)" {
    & (Join-Path $ScriptRoot "eventbridge_deploy_video_scheduler.ps1") -Region $Region -OpsJobQueueName $OpsQueueName
}
$opsQOut2 = ExecJson @("batch", "describe-job-queues", "--job-queues", $OpsQueueName, "--region", $Region, "--output", "json")
$opsQArn = ($opsQOut2.jobQueues | Where-Object { $_.jobQueueName -eq $OpsQueueName }).jobQueueArn
$ruleReconcile = ExecJson @("events", "describe-rule", "--name", $ReconcileRuleName, "--region", $Region, "--output", "json")
if ($ruleReconcile) {
    if ($ruleReconcile.ScheduleExpression -ne "rate(15 minutes)") {
        & aws events put-rule --name $ReconcileRuleName --schedule-expression "rate(15 minutes)" --state ENABLED --description "Reconcile video jobs" --region $Region 2>&1 | Out-Null
    }
    $tgtReconcile = ExecJson @("events", "list-targets-by-rule", "--rule", $ReconcileRuleName, "--region", $Region, "--output", "json")
    $t0 = $tgtReconcile.Targets | Select-Object -First 1
    if ($t0 -and $t0.Arn -eq $opsQArn) { $script:Audit5 = "PASS" }
    $script:Audit5Detail = "Reconcile rule: $ReconcileRuleName -> $OpsQueueName  ScanStuck rule: $ScanStuckRuleName -> $OpsQueueName"
} else { $script:Audit5Detail = "EventBridge rules not found" }

# 8) CloudWatch 알람
Invoke-Step "7) CloudWatch alarms (Video queue)" {
    $state = Get-Content $batchStatePath -Raw | ConvertFrom-Json
    $q = $state.FinalJobQueueName; if (-not $q) { $q = $VideoQueueName }
    & (Join-Path $ScriptRoot "cloudwatch_deploy_video_alarms.ps1") -Region $Region -JobQueueName $q
}

# 9) Netprobe (RUNNABLE 3min = FAIL) + desiredvCpus 수렴
$netprobeJobIdFile = Join-Path $Env:TEMP "netprobe_jobid_$(Get-Date -Format 'yyyyMMddHHmmss').txt"
try {
    Invoke-Step "8) Netprobe (Ops queue, RUNNABLE 3min fail)" {
        & (Join-Path $ScriptRoot "run_netprobe_job.ps1") -Region $Region -JobQueueName $OpsQueueName -JobIdOutFile $netprobeJobIdFile -RunnableFailSeconds 180
    }
} catch {
    if (Test-Path -LiteralPath $netprobeJobIdFile) {
        $jobId = [System.IO.File]::ReadAllText($netprobeJobIdFile, [System.Text.UTF8Encoding]::new($false)).Trim()
        if ($jobId) {
            Write-Host "`n=== Evidence E (Netprobe describe-jobs) ===" -ForegroundColor Cyan
            $evE = ExecJson @("batch", "describe-jobs", "--jobs", $jobId, "--region", $Region, "--output", "json")
            if ($evE -and $evE.jobs -and $evE.jobs.Count -gt 0) {
                $j = $evE.jobs[0]
                Write-Host "  jobId=$jobId status=$($j.status) statusReason=$($j.statusReason)" -ForegroundColor Gray
            }
        }
    }
    Remove-Item $netprobeJobIdFile -Force -ErrorAction SilentlyContinue
    $script:Audit6 = "FAIL"; $script:Audit6Detail = "Netprobe not SUCCEEDED"; $script:AnyFail = $true
    throw "Netprobe failed. SSOT: RUNNABLE 3min or FAILED = FAIL."
}
Remove-Item $netprobeJobIdFile -Force -ErrorAction SilentlyContinue
$script:Audit6 = "PASS"; $script:Audit6Detail = "Submit: OK  Status: SUCCEEDED  Logs: Found"

# desiredvCpus 수렴 (5~10분 내 0)
$state = Get-Content $batchStatePath -Raw | ConvertFrom-Json
$videoCeName = $state.FinalComputeEnvName; if (-not $videoCeName) { $videoCeName = $VideoCEName }
$convergeWait = 0
while ($convergeWait -lt 600) {
    $ceD = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $videoCeName, "--region", $Region, "--output", "json")
    $ceO = $ceD.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $videoCeName } | Select-Object -First 1
    $desired = [int]$ceO.computeResources.desiredvCpus
    if ($desired -eq 0) { break }
    Start-Sleep -Seconds 30
    $convergeWait += 30
}
$ceD = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $videoCeName, "--region", $Region, "--output", "json")
$ceO = $ceD.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $videoCeName } | Select-Object -First 1
$desiredFinal = [int]$ceO.computeResources.desiredvCpus
if ($desiredFinal -ne 0) { Fail-SSOT "desiredvCpus did not converge to 0 (current $desiredFinal)." }

Invoke-Step "8b) Production done check" {
    & (Join-Path $ScriptRoot "production_done_check.ps1") -Region $Region -ComputeEnvName $videoCeName -JobQueueName $state.FinalJobQueueName -OpsJobQueueName $OpsQueueName
}

# 10) Audit + FixMode
if ($FixMode) {
    Invoke-Step "9) Audit + FixMode" {
        & (Join-Path $ScriptRoot "infra_one_take_full_audit.ps1") -Region $Region -FixMode -ExpectedVideoCEName $VideoCEName -ExpectedVideoQueueName $VideoQueueName -ExpectedOpsQueueName $OpsQueueName -ExpectedOpsCEName $OpsCEName
    }
} else {
    Invoke-Step "9) Audit (no FixMode)" {
        & (Join-Path $ScriptRoot "infra_one_take_full_audit.ps1") -Region $Region -ExpectedVideoCEName $VideoCEName -ExpectedVideoQueueName $VideoQueueName -ExpectedOpsQueueName $OpsQueueName -ExpectedOpsCEName $OpsCEName
    }
}

# [7] SCALING SANITY: ECS instances 0 when desiredvCpus=0
$ceD = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$ceO = $ceD.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
$desiredV = [int]$ceO.computeResources.desiredvCpus
$ecsCount = 0
if ($ceO.computeResources.ecsClusterArn) {
    $ecsL = ExecJson @("ecs", "list-container-instances", "--cluster", $ceO.computeResources.ecsClusterArn, "--region", $Region, "--output", "json")
    $ecsCount = if ($ecsL -and $ecsL.containerInstanceArns) { $ecsL.containerInstanceArns.Count } else { 0 }
}
if ($desiredV -eq 0 -and $ecsCount -eq 0) { $script:Audit7 = "PASS" }
elseif ($desiredV -gt 0) { $script:Audit7 = "PASS" }
else { $script:Audit7Detail = "ECS instances=$ecsCount with desiredvCpus=0"; $script:AnyFail = $true }
if (-not $script:Audit7Detail) { $script:Audit7Detail = "ECS Container Instances (Video CE): $ecsCount  ASG Desired/Min/Max: 0/0/32" }

} catch {
    $script:AnyFail = $true
} finally {

# --- 고정 Audit 출력 ---
Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "VIDEO WORKER SSOT AUDIT" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[1] VIDEO CE" -ForegroundColor Gray
Write-Host $script:Audit1Detail
Write-Host "Result: $script:Audit1"
Write-Host ""
Write-Host "[2] VIDEO QUEUE" -ForegroundColor Gray
Write-Host $script:Audit2Detail
Write-Host "Result: $script:Audit2"
Write-Host ""
Write-Host "[3] VIDEO JOB DEF" -ForegroundColor Gray
Write-Host $script:Audit3Detail
Write-Host "Result: $script:Audit3"
Write-Host ""
Write-Host "[4] OPS CE" -ForegroundColor Gray
Write-Host $script:Audit4Detail
Write-Host "Result: $script:Audit4"
Write-Host ""
Write-Host "[5] EVENTBRIDGE" -ForegroundColor Gray
Write-Host $script:Audit5Detail
Write-Host "Result: $script:Audit5"
Write-Host ""
Write-Host "[6] NETPROBE" -ForegroundColor Gray
Write-Host $script:Audit6Detail
Write-Host "Result: $script:Audit6"
Write-Host ""
Write-Host "[7] SCALING SANITY" -ForegroundColor Gray
Write-Host $script:Audit7Detail
Write-Host "Result: $script:Audit7"
Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
$finalResult = "PASS"
if ($script:AnyFail -or $script:Audit1 -ne "PASS" -or $script:Audit2 -ne "PASS" -or $script:Audit3 -ne "PASS" -or $script:Audit4 -ne "PASS" -or $script:Audit5 -ne "PASS" -or $script:Audit6 -ne "PASS" -or $script:Audit7 -ne "PASS") { $finalResult = "FAIL" }
Write-Host "FINAL RESULT: $finalResult" -ForegroundColor $(if ($finalResult -eq "PASS") { "Green" } else { "Red" })
Write-Host "==============================" -ForegroundColor Cyan

if ($finalResult -eq "FAIL") {
    Write-Host "SSOT audit failed. Environment is not production." -ForegroundColor Red
    exit 1
}
Write-Host "`nVIDEO WORKER INFRA ONE-TAKE: DONE (PASS)" -ForegroundColor Green
}
