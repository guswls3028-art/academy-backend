# One-take Batch Video infra diagnose. Run from repo root: .\scripts\diagnose_batch_video_infra.ps1
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

# Resolved API config: .env overrides base defaults
$apiQueue = "academy-video-batch-queue"
$apiJobDef = "academy-video-batch-jobdef"
$envPath = Join-Path $RepoRoot ".env"
if (Test-Path $envPath) {
    $envContent = Get-Content $envPath -Raw
    if ($envContent -match 'VIDEO_BATCH_JOB_QUEUE=(\S+)') { $apiQueue = $Matches[1].Trim() }
    if ($envContent -match 'VIDEO_BATCH_JOB_DEFINITION=(\S+)') { $apiJobDef = $Matches[1].Trim() }
}

# Script/JSON constants (from batch_video_setup.ps1 and JSONs)
$scriptQueue = "academy-video-batch-queue"
$scriptJobDef = "academy-video-batch-jobdef"
$queueJsonName = "academy-video-batch-queue"
$queueJsonCE = "academy-video-batch-ce"
$jobDefJsonName = "academy-video-batch-jobdef"
$jobDefLogGroup = "/aws/batch/academy-video-worker"

Write-Host "=== STEP 0 CONFIG_DIFF ==="
$qMatch = ($apiQueue -eq $scriptQueue -and $apiQueue -eq $queueJsonName)
$jMatch = ($apiJobDef -eq $scriptJobDef -and $apiJobDef -eq $jobDefJsonName)
Write-Host "API_QUEUE=$apiQueue SCRIPT_QUEUE=$scriptQueue QUEUE_JSON=$queueJsonName -> $(if($qMatch){'MATCH'}else{'MISMATCH'})"
Write-Host "API_JOBDEF=$apiJobDef SCRIPT_JOBDEF=$scriptJobDef JOBDEF_JSON=$jobDefJsonName -> $(if($jMatch){'MATCH'}else{'MISMATCH'})"

Write-Host "`n=== STEP 1 AWS ==="
$sts = aws sts get-caller-identity 2>&1
$awsOk = ($LASTEXITCODE -eq 0)
if (-not $awsOk) { Write-Host "AWS_ACCESS=FAIL"; Write-Host $sts }
$region = $env:AWS_REGION; if (-not $region) { $region = $env:AWS_DEFAULT_REGION }; if (-not $region) { $region = aws configure get region 2>$null }; if (-not $region) { $region = "ap-northeast-2" }
$profile = $env:AWS_PROFILE; if (-not $profile) { $profile = aws configure get profile 2>$null }
Write-Host "ACTIVE_REGION=$region ACTIVE_PROFILE=$profile"

if (-not $awsOk) {
    Write-Host "`n=== STEPS 2-5 SKIP (AWS_ACCESS=FAIL) ==="
    Write-Host "QUEUE_CHECK: SKIP"
    Write-Host "CE_CHECK: SKIP"
    Write-Host "JOBDEF_CHECK: SKIP"
    Write-Host "IAM_CHECK: SKIP"
    Write-Host "LOG_GROUP_CHECK: SKIP"
    Write-Host "LOG_CONTENT_CHECK: SKIP"
    Write-Host "SMOKE_SUBMIT_CHECK: SKIP"
} else {
    Write-Host "`n=== STEP 2 QUEUE ==="
    $jq = aws batch describe-job-queues --job-queues $apiQueue --region $region --output json 2>&1
    $queueOk = $false
    if ($LASTEXITCODE -eq 0 -and $jq) {
        $obj = $jq | ConvertFrom-Json
        $q = $obj.jobQueues | Where-Object { $_.jobQueueName -eq $apiQueue } | Select-Object -First 1
        if ($q) {
            Write-Host "state=$($q.state) status=$($q.status) jobQueueArn=$($q.jobQueueArn)"
            $q.computeEnvironmentOrder | ForEach-Object { Write-Host "computeEnvironment order=$($_.order) ce=$($_.computeEnvironment)" }
            $queueOk = ($q.state -eq "ENABLED" -and $q.status -eq "VALID" -and $q.computeEnvironmentOrder.Count -ge 1)
        }
    }
    if (-not $queueOk) { Write-Host "QUEUE_CHECK: FAIL - queue not found or not ENABLED/VALID" }

    Write-Host "`n=== STEP 2 CE ==="
    $ceOk = $true
    if ($queueOk -and $q.computeEnvironmentOrder.Count -gt 0) {
        foreach ($ce in $q.computeEnvironmentOrder) {
            $ceName = $ce.computeEnvironment -replace '.*/', ''
            $ceOut = aws batch describe-compute-environments --compute-environments $ceName --region $region --output json 2>&1
            if ($LASTEXITCODE -eq 0 -and $ceOut) {
                $ceObj = ($ceOut | ConvertFrom-Json).computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ceName } | Select-Object -First 1
                if ($ceObj) {
                    Write-Host "CE $ceName state=$($ceObj.state) status=$($ceObj.status) type=$($ceObj.type) maxvCpus=$($ceObj.computeResources.maxvCpus)"
                    if ($ceObj.state -ne "ENABLED" -or $ceObj.status -ne "VALID" -or [int]$ceObj.computeResources.maxvCpus -le 0) { $ceOk = $false }
                } else { $ceOk = $false }
            } else { $ceOk = $false; Write-Host "CE $ceName describe FAIL" }
        }
    }
    Write-Host "CE_CHECK: $(if($ceOk){'OK'}else{'FAIL'})"

    Write-Host "`n=== STEP 3 JOBDEF ==="
    $jdOut = aws batch describe-job-definitions --job-definition-name $apiJobDef --status ACTIVE --region $region --output json 2>&1
    $jobDefOk = $false
    if ($LASTEXITCODE -eq 0 -and $jdOut) {
        $jdList = ($jdOut | ConvertFrom-Json).jobDefinitions
        $latest = $jdList | Sort-Object -Property revision -Descending | Select-Object -First 1
        if ($latest) {
            Write-Host "revision=$($latest.revision) type=$($latest.type) image=$($latest.containerProperties.image)"
            Write-Host "executionRoleArn=$($latest.containerProperties.executionRoleArn) jobRoleArn=$($latest.containerProperties.jobRoleArn)"
            Write-Host "logDriver=$($latest.containerProperties.logConfiguration.logDriver) logGroup=$($latest.containerProperties.logConfiguration.options.'awslogs-group')"
            Write-Host "retryStrategy.attempts=$($latest.retryStrategy.attempts)"
            $jobDefOk = [bool]$latest.containerProperties.image
            if (-not $latest.containerProperties.executionRoleArn) { Write-Host "WARN: executionRoleArn missing" }
            if (-not $latest.containerProperties.jobRoleArn) { Write-Host "WARN: jobRoleArn missing" }
        }
    }
    if (-not $jobDefOk) { Write-Host "JOBDEF_CHECK: FAIL - no ACTIVE job def or empty image" }

    Write-Host "`n=== STEP 3 IAM ==="
    $iamOk = "SKIP"
    if ($jobDefOk -and $latest.containerProperties.jobRoleArn) {
        $roleName = $latest.containerProperties.jobRoleArn -replace '.*/', ''
        $roleOut = aws iam get-role --role-name $roleName 2>&1
        if ($LASTEXITCODE -eq 0) {
            aws iam list-attached-role-policies --role-name $roleName 2>&1 | Out-Host
            $iamOk = "OK"
        } elseif ($roleOut -match "AccessDenied") { $iamOk = "SKIP(AccessDenied)" }
        else { $iamOk = "FAIL"; Write-Host "jobRole $roleName - $roleOut" }
    }
    if ($jobDefOk -and $latest.containerProperties.executionRoleArn) {
        $exName = $latest.containerProperties.executionRoleArn -replace '.*/', ''
        $exOut = aws iam get-role --role-name $exName 2>&1
        if ($LASTEXITCODE -ne 0 -and $exOut -match "NoSuchEntity") { $iamOk = "FAIL"; Write-Host "executionRole $exName missing" }
    }
    Write-Host "IAM_CHECK: $iamOk"

    Write-Host "`n=== STEP 4 LOG GROUP ==="
    $logGroup = $jobDefLogGroup
    if ($jobDefOk -and $latest.containerProperties.logConfiguration.options.'awslogs-group') { $logGroup = $latest.containerProperties.logConfiguration.options.'awslogs-group' }
    $lgOut = aws logs describe-log-groups --log-group-name-prefix $logGroup --region $region --output json 2>&1
    $logGroupOk = $false
    if ($LASTEXITCODE -eq 0 -and $lgOut) {
        $lgList = ($lgOut | ConvertFrom-Json).logGroups
        $logGroupOk = ($lgList | Where-Object { $_.logGroupName -eq $logGroup }).Count -gt 0
    }
    Write-Host "LOG_GROUP_CHECK: $(if($logGroupOk){'OK'}else{'FAIL'})"

    $logContentOk = "SKIP"
    if ($logGroupOk) {
        $streams = aws logs describe-log-streams --log-group-name $logGroup --order-by LastEventTime --descending --max-items 5 --region $region --output json 2>&1
        if ($LASTEXITCODE -eq 0 -and $streams) {
            $streamList = ($streams | ConvertFrom-Json).logStreams
            $foundStart = $false; $foundComplete = $false
            foreach ($s in $streamList) {
                $ev = aws logs get-log-events --log-group-name $logGroup --log-stream-name $s.logStreamName --limit 200 --region $region --output json 2>&1
                if ($ev) {
                    $evObj = $ev | ConvertFrom-Json
                    $evObj.events | ForEach-Object { if ($_.message -match "BATCH_PROCESS_START") { $foundStart = $true }; if ($_.message -match "BATCH_JOB_COMPLETED") { $foundComplete = $true } }
                }
            }
            if ($foundStart) { Write-Host "BATCH_PROCESS_START: FOUND" } else { Write-Host "BATCH_PROCESS_START: NOT_FOUND" }
            if ($foundComplete) { Write-Host "BATCH_JOB_COMPLETED: FOUND" } else { Write-Host "BATCH_JOB_COMPLETED: NOT_FOUND" }
            $logContentOk = if ($foundStart -and $foundComplete) { "OK" } elseif (-not $foundStart -and -not $foundComplete) { "WARN" } else { "WARN" }
        }
    }
    Write-Host "LOG_CONTENT_CHECK: $logContentOk"

    Write-Host "`n=== STEP 5 SMOKE SUBMIT ==="
    if ($env:ALLOW_TEST_SUBMIT -eq "true" -or $env:ALLOW_TEST_SUBMIT -eq "1") {
        $sub = aws batch submit-job --job-name cursor-video-smoke-test --job-queue $apiQueue --job-definition $apiJobDef --parameters "job_id=cursor-smoke-diagnose" --region $region --output json 2>&1
        if ($LASTEXITCODE -eq 0 -and $sub) {
            $jid = ($sub | ConvertFrom-Json).jobId
            Write-Host "jobId=$jid"
            $desc = aws batch describe-jobs --jobs $jid --region $region --query "jobs[0].{status:status, reason:statusReason}" --output json 2>&1
            Write-Host $desc
            Write-Host "SMOKE_SUBMIT_CHECK: OK"
        } else { Write-Host "SMOKE_SUBMIT_CHECK: FAIL"; Write-Host $sub }
    } else { Write-Host "SMOKE_SUBMIT_CHECK: SKIP (ALLOW_TEST_SUBMIT not set)" }
}

Write-Host "`n========== FINAL REPORT =========="
$cfgQ = if ($qMatch) { "OK" } else { "FAIL" }
$cfgJ = if ($jMatch) { "OK" } else { "FAIL" }
Write-Host "CONFIG_MATCH_QUEUE: $cfgQ"
Write-Host "CONFIG_MATCH_JOBDEF: $cfgJ"
Write-Host "AWS_ACCESS: $(if($awsOk){'OK'}else{'FAIL'})"
if (-not $awsOk) {
    Write-Host "QUEUE_CHECK: SKIP"
    Write-Host "CE_CHECK: SKIP"
    Write-Host "JOBDEF_CHECK: SKIP"
    Write-Host "IAM_CHECK: SKIP"
    Write-Host "LOG_GROUP_CHECK: SKIP"
    Write-Host "LOG_CONTENT_CHECK: SKIP"
    Write-Host "SMOKE_SUBMIT_CHECK: SKIP"
    Write-Host "`nROOT_CAUSE_HINTS:"
    Write-Host "- AWS credentials invalid or not configured (InvalidClientTokenId). Set AWS_PROFILE or AWS_ACCESS_KEY_ID/SECRET and retry."
} else {
    Write-Host "QUEUE_CHECK: $(if($queueOk){'OK'}else{'FAIL'})"
    Write-Host "CE_CHECK: $(if($ceOk){'OK'}else{'FAIL'})"
    Write-Host "JOBDEF_CHECK: $(if($jobDefOk){'OK'}else{'FAIL'})"
    Write-Host "IAM_CHECK: $iamOk"
    Write-Host "LOG_GROUP_CHECK: $(if($logGroupOk){'OK'}else{'FAIL'})"
    Write-Host "LOG_CONTENT_CHECK: $logContentOk"
    Write-Host "SMOKE_SUBMIT_CHECK: $(if($env:ALLOW_TEST_SUBMIT){'OK/FAIL from above'}else{'SKIP'})"
    Write-Host "`nROOT_CAUSE_HINTS:"
    if ($cfgQ -eq "FAIL" -or $cfgJ -eq "FAIL") { Write-Host "- CONFIG mismatch: check .env VIDEO_BATCH_* and scripts/infra/batch_video_setup.ps1 / batch/*.json" }
    if (-not $queueOk) { Write-Host "- Queue missing: run scripts/infra/batch_video_setup_full.ps1 in this account/region" }
    if (-not $ceOk) { Write-Host "- CE invalid: VPC/instance role/capacity or CE disabled; check batch_ensure_ce_enabled.ps1" }
    if ($iamOk -eq "FAIL") { Write-Host "- Job/execution role missing: scripts create academy-video-batch-job-role, academy-batch-ecs-task-execution-role" }
    if (-not $logGroupOk) { Write-Host "- Log group missing: job definition logConfiguration or logs:CreateLogStream permission" }
}
