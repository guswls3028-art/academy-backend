# ==============================================================================
# Production "done" check: CE in API VPC, queue, jobdefs, EventBridge, SSM, log groups, alarms, netprobe SUCCEEDED.
# Exit 0 only if all required checks pass. Usage: .\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$ComputeEnvName = "academy-video-batch-ce",
    [string]$JobQueueName = "academy-video-batch-queue",
    [string]$ApiVpcId = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$fail = 0

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

# Resolve API VPC if not provided
$targetVpcId = $ApiVpcId
if (-not $targetVpcId) {
    $apiPath = Join-Path $RepoRoot "docs\deploy\actual_state\api_instance.json"
    if (Test-Path -LiteralPath $apiPath) {
        $targetVpcId = (Get-Content $apiPath -Raw | ConvertFrom-Json).VpcId
    }
}
if (-not $targetVpcId) {
    Write-Host "WARN: ApiVpcId not set and api_instance.json not found; skipping CE-in-API-VPC check." -ForegroundColor Yellow
}

# CE exists and (if targetVpcId) in API VPC
$ceList = ExecJson "aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output json 2>&1"
$ce = $ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
if (-not $ce) {
    Write-Host "FAIL: Compute environment $ComputeEnvName not found." -ForegroundColor Red
    $fail = 1
} else {
    $ceVpcId = ""
    if ($ce.computeResources.subnets) {
        $subResp = ExecJson "aws ec2 describe-subnets --subnet-ids $($ce.computeResources.subnets[0]) --region $Region --output json 2>&1"
        if ($subResp.Subnets) { $ceVpcId = $subResp.Subnets[0].VpcId }
    }
    if ($targetVpcId -and $ceVpcId -ne $targetVpcId) {
        Write-Host "FAIL: CE $ComputeEnvName is in VPC $ceVpcId, expected API VPC $targetVpcId." -ForegroundColor Red
        $fail = 1
    } else { Write-Host "OK: CE $ComputeEnvName" -ForegroundColor Green }
}

# Queue points to CE
$jq = ExecJson "aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json 2>&1"
$q = $jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName } | Select-Object -First 1
if (-not $q -or $q.state -ne "ENABLED") {
    Write-Host "FAIL: Job queue $JobQueueName not found or not ENABLED." -ForegroundColor Red
    $fail = 1
} else { Write-Host "OK: Queue $JobQueueName ENABLED" -ForegroundColor Green }

# Worker + ops jobdefs ACTIVE
foreach ($jdName in @("academy-video-batch-jobdef", "academy-video-ops-reconcile", "academy-video-ops-scanstuck", "academy-video-ops-netprobe")) {
    $jd = ExecJson "aws batch describe-job-definitions --job-definition-name $jdName --status ACTIVE --region $Region --output json 2>&1"
    if (-not $jd.jobDefinitions -or $jd.jobDefinitions.Count -eq 0) {
        Write-Host "FAIL: Job definition $jdName not ACTIVE." -ForegroundColor Red
        $fail = 1
    } else { Write-Host "OK: JobDef $jdName" -ForegroundColor Green }
}

# EventBridge rules ENABLED with Batch targets
& (Join-Path $ScriptRoot "verify_eventbridge_wiring.ps1") -Region $Region -JobQueueName $JobQueueName
if ($LASTEXITCODE -ne 0) { $fail = 1 }

# SSM parameter exists and valid
& (Join-Path $ScriptRoot "verify_ssm_env_shape.ps1") -Region $Region
if ($LASTEXITCODE -ne 0) { $fail = 1 }

# Log groups
$lgWorker = ExecJson "aws logs describe-log-groups --log-group-name-prefix /aws/batch/academy-video-worker --region $Region --output json 2>&1"
$lgOps = ExecJson "aws logs describe-log-groups --log-group-name-prefix /aws/batch/academy-video-ops --region $Region --output json 2>&1"
if (-not ($lgWorker.logGroups | Where-Object { $_.logGroupName -eq "/aws/batch/academy-video-worker" })) {
    Write-Host "FAIL: Log group /aws/batch/academy-video-worker not found." -ForegroundColor Red
    $fail = 1
} else { Write-Host "OK: Log group /aws/batch/academy-video-worker" -ForegroundColor Green }
if (-not ($lgOps.logGroups | Where-Object { $_.logGroupName -eq "/aws/batch/academy-video-ops" })) {
    Write-Host "FAIL: Log group /aws/batch/academy-video-ops not found." -ForegroundColor Red
    $fail = 1
} else { Write-Host "OK: Log group /aws/batch/academy-video-ops" -ForegroundColor Green }

# CloudWatch alarms (warn if missing)
$alarmNames = @("academy-video-DeadJobs", "academy-video-UploadFailures", "academy-video-FailedJobs", "academy-video-BatchJobFailures", "academy-video-QueueRunnable")
$cw = ExecJson "aws cloudwatch describe-alarms --alarm-names $alarmNames --region $Region --output json 2>&1"
$found = @(if ($cw.MetricAlarms) { $cw.MetricAlarms | ForEach-Object { $_.AlarmName } } else { @() })
$missingAlarms = $alarmNames | Where-Object { $_ -notin $found }
if ($missingAlarms.Count -gt 0) {
    Write-Host "WARN: CloudWatch alarms missing: $($missingAlarms -join ', '). Run cloudwatch_deploy_video_alarms.ps1" -ForegroundColor Yellow
} else { Write-Host "OK: CloudWatch alarms" -ForegroundColor Green }

# Netprobe: submit and ensure SUCCEEDED
Write-Host "Submitting netprobe job..." -ForegroundColor Cyan
$npName = "donecheck-netprobe-" + (Get-Date -Format "yyyyMMddHHmmss")
$npSubmit = ExecJson "aws batch submit-job --job-name $npName --job-queue $JobQueueName --job-definition academy-video-ops-netprobe --region $Region --output json"
if (-not $npSubmit -or -not $npSubmit.jobId) {
    Write-Host "FAIL: Netprobe submit failed." -ForegroundColor Red
    $fail = 1
} else {
    $npId = $npSubmit.jobId
    $npWait = 0
    while ($npWait -lt 200) {
        $npDesc = ExecJson "aws batch describe-jobs --jobs $npId --region $Region --output json"
        $npStatus = $npDesc.jobs[0].status
        if ($npStatus -eq "SUCCEEDED") {
            Write-Host "OK: Netprobe SUCCEEDED" -ForegroundColor Green
            break
        }
        if ($npStatus -eq "FAILED") {
            Write-Host "FAIL: Netprobe job FAILED (connectivity proof failed)." -ForegroundColor Red
            $fail = 1
            break
        }
        Start-Sleep -Seconds 10
        $npWait += 10
    }
    if ($npWait -ge 200) {
        Write-Host "FAIL: Netprobe job did not complete in time." -ForegroundColor Red
        $fail = 1
    }
}

if ($fail -ne 0) {
    Write-Host "`nPRODUCTION DONE CHECK: FAIL" -ForegroundColor Red
    exit 1
}
Write-Host "`nPRODUCTION DONE CHECK: PASS" -ForegroundColor Green
exit 0
