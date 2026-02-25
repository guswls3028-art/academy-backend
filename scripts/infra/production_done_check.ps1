# ==============================================================================
# Production "done" check: CE, queue (ENABLED), ops jobdefs ACTIVE, EventBridge targets, SSM, netprobe.
# Exit 0 only if all required checks pass. Uses batch_final_state.json for JobQueueName when present.
# Usage: .\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$ComputeEnvName = "academy-video-batch-ce",
    [string]$JobQueueName = "academy-video-batch-queue",
    [string]$OpsJobQueueName = "academy-video-ops-queue",
    [string]$ApiVpcId = ""
)
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$fail = 0

# Resolve JobQueueName from batch_final_state.json if present (video queue)
$statePath = Join-Path $RepoRoot "docs\deploy\actual_state\batch_final_state.json"
if (Test-Path -LiteralPath $statePath) {
    try {
        $state = Get-Content $statePath -Raw | ConvertFrom-Json
        if ($state.FinalJobQueueName) { $JobQueueName = $state.FinalJobQueueName }
    } catch {}
}
# Ops queue for EventBridge and netprobe (optional: from batch_ops_state.json)
$opsStatePath = Join-Path $RepoRoot "docs\deploy\actual_state\batch_ops_state.json"
if (Test-Path -LiteralPath $opsStatePath) {
    try {
        $opsState = Get-Content $opsStatePath -Raw | ConvertFrom-Json
        if ($opsState.OpsJobQueueName) { $OpsJobQueueName = $opsState.OpsJobQueueName }
    } catch {}
}

function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    $str = ($out | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($str)) { return $null }
    try { return ($str | ConvertFrom-Json) } catch { return $null }
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

# CE exists and (if targetVpcId) in API VPC; state ENABLED, status VALID
$ceList = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $ComputeEnvName, "--region", $Region, "--output", "json")
$ce = $ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
if (-not $ce) {
    Write-Host "FAIL: Compute environment $ComputeEnvName not found." -ForegroundColor Red
    $fail = 1
} else {
    if ($ce.state -ne "ENABLED") {
        Write-Host "FAIL: CE $ComputeEnvName state=$($ce.state) (expected ENABLED)." -ForegroundColor Red
        $fail = 1
    } elseif ($ce.status -ne "VALID") {
        Write-Host "FAIL: CE $ComputeEnvName status=$($ce.status) (expected VALID)." -ForegroundColor Red
        $fail = 1
    } else {
        $ceVpcId = ""
        if ($ce.computeResources.subnets) {
            $subResp = ExecJson @("ec2", "describe-subnets", "--subnet-ids", $ce.computeResources.subnets[0], "--region", $Region, "--output", "json")
            if ($subResp.Subnets) { $ceVpcId = $subResp.Subnets[0].VpcId }
        }
        if ($targetVpcId -and $ceVpcId -ne $targetVpcId) {
            Write-Host "FAIL: CE $ComputeEnvName is in VPC $ceVpcId, expected API VPC $targetVpcId." -ForegroundColor Red
            $fail = 1
        } else { Write-Host "OK: CE $ComputeEnvName (ENABLED, VALID)" -ForegroundColor Green }
    }
}

# Queue: must exist and be ENABLED (strict; describe-job-queues empty or error = FAIL)
$jq = ExecJson @("batch", "describe-job-queues", "--job-queues", $JobQueueName, "--region", $Region, "--output", "json")
if (-not $jq) {
    Write-Host "FAIL: describe-job-queues failed or returned no data for $JobQueueName." -ForegroundColor Red
    $fail = 1
} else {
    $q = $jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName } | Select-Object -First 1
    if (-not $q) {
        Write-Host "FAIL: Job queue $JobQueueName not found (empty result)." -ForegroundColor Red
        $fail = 1
    } elseif ($q.state -ne "ENABLED") {
        Write-Host "FAIL: Job queue $JobQueueName state=$($q.state) (expected ENABLED)." -ForegroundColor Red
        $fail = 1
    } else {
        Write-Host "OK: Queue $JobQueueName ENABLED" -ForegroundColor Green
    }
}

# Worker + ops jobdefs ACTIVE
foreach ($jdName in @("academy-video-batch-jobdef", "academy-video-ops-reconcile", "academy-video-ops-scanstuck", "academy-video-ops-netprobe")) {
    $jd = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $jdName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
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

# VPC 모드일 때 API_BASE_URL는 반드시 Private IP (Batch 내부 통신)
if ($targetVpcId) {
    $ssmRaw = & aws @('ssm', 'get-parameter', '--name', '/academy/workers/env', '--region', $Region, '--with-decryption', '--output', 'json') 2>&1
    if ($LASTEXITCODE -eq 0 -and $ssmRaw) {
        $ssmStr = ($ssmRaw | Out-String).Trim()
        try {
            $outer = $ssmStr | ConvertFrom-Json
            $valStr = $outer.Parameter.Value
            try { $payload = $valStr | ConvertFrom-Json } catch { $valBytes = [Convert]::FromBase64String($valStr); $valStr = [System.Text.Encoding]::UTF8.GetString($valBytes); $payload = $valStr | ConvertFrom-Json }
            $apiBase = $payload.API_BASE_URL
            $isPrivate = $false
            if ($apiBase -match '^http://([^/:]+)') {
                $hostPart = $Matches[1]
                if ($hostPart -match '^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.)') { $isPrivate = $true }
            }
            if (-not $isPrivate) {
                Write-Host "FAIL: API_BASE_URL must be private IP when running in VPC mode (e.g. http://10.x.x.x:8000). Run discover_api_network.ps1 then ssm_bootstrap_video_worker.ps1 -UsePrivateApiIp -Overwrite." -ForegroundColor Red
                $fail = 1
            } else {
                Write-Host "OK: API_BASE_URL is private (VPC internal)" -ForegroundColor Green
            }
        } catch {
            Write-Host "WARN: Could not verify API_BASE_URL from SSM (VPC mode)." -ForegroundColor Yellow
        }
    }
}

# Log groups
$lgWorker = ExecJson @("logs", "describe-log-groups", "--log-group-name-prefix", "/aws/batch/academy-video-worker", "--region", $Region, "--output", "json")
$lgOps = ExecJson @("logs", "describe-log-groups", "--log-group-name-prefix", "/aws/batch/academy-video-ops", "--region", $Region, "--output", "json")
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
$cw = ExecJson @("cloudwatch", "describe-alarms", "--alarm-names") + $alarmNames + @("--region", $Region, "--output", "json")
$found = @(if ($cw.MetricAlarms) { $cw.MetricAlarms | ForEach-Object { $_.AlarmName } } else { @() })
$missingAlarms = $alarmNames | Where-Object { $_ -notin $found }
if ($missingAlarms.Count -gt 0) {
    Write-Host "WARN: CloudWatch alarms missing: $($missingAlarms -join ', '). Run cloudwatch_deploy_video_alarms.ps1" -ForegroundColor Yellow
} else { Write-Host "OK: CloudWatch alarms" -ForegroundColor Green }

# Netprobe: only if jobdef ACTIVE; submit and poll to SUCCEEDED/FAILED; print logStreamName only (no log fetch)
$npJd = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", "academy-video-ops-netprobe", "--status", "ACTIVE", "--region", $Region, "--output", "json")
if (-not $npJd -or -not $npJd.jobDefinitions -or $npJd.jobDefinitions.Count -eq 0) {
    Write-Host "FAIL: academy-video-ops-netprobe not ACTIVE; cannot run netprobe." -ForegroundColor Red
    $fail = 1
} else {
    Write-Host "Submitting netprobe job..." -ForegroundColor Cyan
    $npName = "donecheck-netprobe-" + (Get-Date -Format "yyyyMMddHHmmss")
    $npSubmit = ExecJson @("batch", "submit-job", "--job-name", $npName, "--job-queue", $JobQueueName, "--job-definition", "academy-video-ops-netprobe", "--region", $Region, "--output", "json")
    if (-not $npSubmit -or -not $npSubmit.jobId) {
        Write-Host "FAIL: Netprobe submit failed." -ForegroundColor Red
        $fail = 1
    } else {
        $npId = $npSubmit.jobId
        $npWait = 0
        while ($npWait -lt 200) {
            $npDesc = ExecJson @("batch", "describe-jobs", "--jobs", $npId, "--region", $Region, "--output", "json")
            if (-not $npDesc -or -not $npDesc.jobs -or $npDesc.jobs.Count -eq 0) {
                Start-Sleep -Seconds 10
                $npWait += 10
                continue
            }
            $npStatus = $npDesc.jobs[0].status
            if ($npStatus -eq "SUCCEEDED") {
                $logStream = $npDesc.jobs[0].container.logStreamName
                $exitCode = $npDesc.jobs[0].container.exitCode
                if ($null -ne $exitCode -and $exitCode -ne 0) {
                    Write-Host "FAIL: Netprobe container exitCode=$exitCode (expected 0)." -ForegroundColor Red
                    $fail = 1
                } else {
                    Write-Host "OK: Netprobe SUCCEEDED (exitCode=0, logStreamName=$logStream)" -ForegroundColor Green
                }
                break
            }
            if ($npStatus -eq "FAILED") {
                Write-Host "FAIL: Netprobe job FAILED (connectivity proof failed)." -ForegroundColor Red
                Write-Host "  Fix: 1) Run ssm_bootstrap_video_worker.ps1 -Overwrite so SSM has JSON with DJANGO_SETTINGS_MODULE=worker. 2) Rebuild/push academy-video-worker image. 3) Ensure Batch CE is in API VPC and SG allows RDS(5432)/Redis(6379)." -ForegroundColor Yellow
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
}

if ($fail -ne 0) {
    Write-Host "`nPRODUCTION DONE CHECK: FAIL" -ForegroundColor Red
    Write-Host "  Resolve the FAIL lines above; then re-run this script." -ForegroundColor Gray
    exit 1
}
Write-Host "`nPRODUCTION DONE CHECK: PASS" -ForegroundColor Green
Write-Host "VIDEO WORKER PRODUCTION READY" -ForegroundColor Green
exit 0
