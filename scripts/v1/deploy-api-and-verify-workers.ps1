# ==============================================================================
# API 재배포 + 전체 워커 연결 검증 (SSOT V1.0.0)
# API ASG instance refresh → 헬스체크 대기 → 워커 연결 전체 검증
# Usage: pwsh scripts/v1/deploy-api-and-verify-workers.ps1 [-AwsProfile default] [-SkipRefresh]
# ==============================================================================
param(
    [string]$AwsProfile = "default",
    [switch]$SkipRefresh  # 이미 refresh 진행 중이면 검증만 실행
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$Region = "ap-northeast-2"

# --- Init ---
. (Join-Path $ScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = $Region }
}
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"

$report = @()
$allPass = $true
function Add-Result($stage, $item, $status, $detail) {
    $color = switch ($status) { "PASS" { "Green" } "WARN" { "Yellow" } "FAIL" { "Red" } default { "Gray" } }
    Write-Host "  [$status] $item — $detail" -ForegroundColor $color
    $script:report += [PSCustomObject]@{ Stage=$stage; Item=$item; Status=$status; Detail=$detail }
    if ($status -eq "FAIL") { $script:allPass = $false }
}

$timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host " API Deploy + Worker Verification (SSOT V1.0.0)" -ForegroundColor Cyan
Write-Host " $timestamp" -ForegroundColor Cyan
Write-Host "============================================`n" -ForegroundColor Cyan

# ==============================================================================
# STAGE 0: Image Freshness Check (Git SHA vs CI Build vs ECR)
# ==============================================================================
Write-Host "=== STAGE 0: Image Freshness Check ===" -ForegroundColor Cyan

$repoRoot = Get-RepoRoot
$gitHeadSha = $null
$ciBuildSha = $null
$ciBuildDigests = @{}
$ecrDigests = @{}
$imageRepos = @("academy-api", "academy-video-worker", "academy-messaging-worker", "academy-ai-worker-cpu", "academy-base")

# 0-1. Git HEAD SHA
try {
    Push-Location $repoRoot
    $null = git fetch origin 2>&1
    $gitHeadSha = (git rev-parse origin/main 2>&1).Trim()
    $gitShort = $gitHeadSha.Substring(0, 7)
    Pop-Location
    Add-Result "0-IMAGE" "git/HEAD" "PASS" "$gitShort ($gitHeadSha)"
} catch {
    Pop-Location
    Add-Result "0-IMAGE" "git/HEAD" "WARN" "git fetch failed: $_"
}

# 0-2. Latest CI build SHA + status (via gh CLI)
$ciRunStatus = $null
$ciRunConclusion = $null
try {
    $ghOutput = gh run list --limit 10 --json databaseId,status,conclusion,headSha,workflowName 2>&1
    $ghRuns = $ghOutput | ConvertFrom-Json
    $buildRuns = @($ghRuns | Where-Object { $_.workflowName -match "Build and Push" })

    # Find latest completed successful build
    $latestSuccess = $buildRuns | Where-Object { $_.status -eq "completed" -and $_.conclusion -eq "success" } | Select-Object -First 1
    # Find any in-progress build
    $inProgress = $buildRuns | Where-Object { $_.status -eq "in_progress" } | Select-Object -First 1

    if ($inProgress) {
        $ciRunStatus = "in_progress"
        $ciBuildSha = $inProgress.headSha
        $ciShort = $ciBuildSha.Substring(0, 7)
        Add-Result "0-IMAGE" "ci/in-progress" "WARN" "Build in progress for $ciShort (run #$($inProgress.databaseId))"
    }

    if ($latestSuccess) {
        $lastSuccessSha = $latestSuccess.headSha
        $lastSuccessShort = $lastSuccessSha.Substring(0, 7)
        Add-Result "0-IMAGE" "ci/last-success" "PASS" "$lastSuccessShort (run #$($latestSuccess.databaseId))"

        # Check if git HEAD matches last successful build
        if ($gitHeadSha -and $gitHeadSha -ne $lastSuccessSha) {
            if ($inProgress -and $inProgress.headSha -eq $gitHeadSha) {
                Add-Result "0-IMAGE" "ci/HEAD-sync" "WARN" "HEAD=$($gitHeadSha.Substring(0,7)) != lastBuild=$lastSuccessShort (build in progress)"
            } else {
                Add-Result "0-IMAGE" "ci/HEAD-sync" "FAIL" "HEAD=$($gitHeadSha.Substring(0,7)) != lastBuild=$lastSuccessShort (no build running!)"
            }
        } elseif ($gitHeadSha) {
            Add-Result "0-IMAGE" "ci/HEAD-sync" "PASS" "HEAD matches last successful build"
        }
    } else {
        Add-Result "0-IMAGE" "ci/last-success" "WARN" "No successful build found in recent runs"
    }
} catch {
    Add-Result "0-IMAGE" "ci/status" "WARN" "gh CLI failed: $_"
}

# 0-3. CI build report digest (from ci-build.latest.md)
$ciBuildReportPath = Join-Path $repoRoot "docs\00-SSOT\v1\reports\ci-build.latest.md"
$ciBuildReportSha = $null
if (Test-Path $ciBuildReportPath) {
    try {
        $ciContent = Get-Content $ciBuildReportPath -Raw
        if ($ciContent -match '\*\*gitSha:\*\*\s*([0-9a-f]{7,40})') { $ciBuildReportSha = $matches[1] }
        elseif ($ciContent -match 'gitSha:\s*([0-9a-f]{7,40})') { $ciBuildReportSha = $matches[1] }
        # Parse digest table: | repo | tag | digest |
        foreach ($line in ($ciContent -split "`n")) {
            if ($line -match '^\|\s*(academy-\S+)\s*\|\s*latest\s*\|\s*(sha256:\S+)\s*\|') {
                $ciBuildDigests[$matches[1]] = $matches[2]
            }
        }
        $ciReportShort = if ($ciBuildReportSha -and $ciBuildReportSha.Length -ge 7) { $ciBuildReportSha.Substring(0, 7) } elseif ($ciBuildReportSha) { $ciBuildReportSha } else { "unknown" }
        Add-Result "0-IMAGE" "ci-report/sha" "PASS" "$ciReportShort ($($ciBuildDigests.Count) images)"
    } catch {
        Add-Result "0-IMAGE" "ci-report" "WARN" "parse failed: $_"
    }
} else {
    Add-Result "0-IMAGE" "ci-report" "WARN" "ci-build.latest.md not found"
}

# 0-4. ECR latest digest for each image
foreach ($repo in $imageRepos) {
    try {
        $ecrJson = Invoke-Aws @("ecr", "describe-images",
            "--repository-name", $repo,
            "--image-ids", "imageTag=latest",
            "--query", "imageDetails[0].{digest:imageDigest,pushed:imagePushedAt}",
            "--region", $Region) -ErrorMessage "ecr-$repo"
        $ecrImg = $ecrJson | ConvertFrom-Json
        $ecrDigests[$repo] = $ecrImg.digest

        # Compare with CI report digest
        $ciDigest = $ciBuildDigests[$repo]
        if ($ciDigest -and $ciDigest -eq $ecrImg.digest) {
            Add-Result "0-IMAGE" "ecr/$repo" "PASS" "digest matches CI report (pushed $($ecrImg.pushed))"
        } elseif ($ciDigest) {
            # Digest mismatch = ECR was updated after CI report (newer build pushed)
            Add-Result "0-IMAGE" "ecr/$repo" "WARN" "digest differs from CI report — newer image pushed after report (pushed $($ecrImg.pushed))"
        } else {
            Add-Result "0-IMAGE" "ecr/$repo" "PASS" "$($ecrImg.digest.Substring(0, 19))... (pushed $($ecrImg.pushed))"
        }
    } catch {
        Add-Result "0-IMAGE" "ecr/$repo" "FAIL" "$_"
    }
}

# 0-5. Summary: is the running API using the latest image?
if ($gitHeadSha -and $ciBuildReportSha) {
    if ($gitHeadSha -eq $ciBuildReportSha) {
        Add-Result "0-IMAGE" "freshness" "PASS" "Git HEAD = CI build = ECR latest (all in sync)"
    } elseif ($ciRunStatus -eq "in_progress") {
        Add-Result "0-IMAGE" "freshness" "WARN" "New build in progress — ECR will update when CI completes"
    } else {
        $commitsBehind = 0
        try {
            Push-Location $repoRoot
            $commitsBehind = [int](git rev-list --count "$ciBuildReportSha..origin/main" 2>&1)
            Pop-Location
        } catch { Pop-Location }
        Add-Result "0-IMAGE" "freshness" "WARN" "ECR is $commitsBehind commit(s) behind HEAD"
    }
}

# ==============================================================================
# STAGE 1: API ASG Instance Refresh
# ==============================================================================
Write-Host "=== STAGE 1: API ASG Instance Refresh ===" -ForegroundColor Cyan

if ($SkipRefresh) {
    Write-Host "  -SkipRefresh: refresh 생략, 현재 상태 확인" -ForegroundColor Yellow
} else {
    $minHealthy = if ($script:ApiInstanceRefreshMinHealthyPercentage -gt 0) { $script:ApiInstanceRefreshMinHealthyPercentage } else { 100 }
    $warmup = if ($script:ApiInstanceRefreshInstanceWarmup -gt 0) { $script:ApiInstanceRefreshInstanceWarmup } else { 300 }
    $prefs = Convert-JsonArgToFileRef (@{MinHealthyPercentage=$minHealthy;InstanceWarmup=$warmup} | ConvertTo-Json -Compress)

    Write-Host "  Starting instance refresh: $($script:ApiASGName) (MinHealthy=$minHealthy%, Warmup=${warmup}s)" -ForegroundColor White
    try {
        $refreshResult = Invoke-Aws @("autoscaling", "start-instance-refresh",
            "--auto-scaling-group-name", $script:ApiASGName,
            "--preferences", $prefs,
            "--region", $Region) -ErrorMessage "start-instance-refresh"
        $refreshId = ($refreshResult | ConvertFrom-Json).InstanceRefreshId
        Add-Result "1-REFRESH" "instance-refresh-start" "PASS" "RefreshId=$refreshId"
    } catch {
        if ($_.Exception.Message -match "InstanceRefreshInProgress") {
            Add-Result "1-REFRESH" "instance-refresh-start" "PASS" "Already in progress (idempotent)"
        } else {
            Add-Result "1-REFRESH" "instance-refresh-start" "FAIL" "$_"
        }
    }
}

# Check current refresh status
try {
    $refreshJson = Invoke-Aws @("autoscaling", "describe-instance-refreshes",
        "--auto-scaling-group-name", $script:ApiASGName,
        "--query", "InstanceRefreshes[0].{Status:Status,Pct:PercentageComplete}",
        "--region", $Region) -ErrorMessage "describe-instance-refreshes"
    $refreshState = ($refreshJson | ConvertFrom-Json)
    Add-Result "1-REFRESH" "refresh-status" $(if ($refreshState.Status -in @("Successful","InProgress","Pending")) { "PASS" } else { "FAIL" }) "$($refreshState.Status) ($($refreshState.Pct)%)"
} catch {
    Add-Result "1-REFRESH" "refresh-status" "WARN" "조회 실패: $_"
}

# ==============================================================================
# STAGE 2: API Health Check (wait up to 10 min)
# ==============================================================================
Write-Host "`n=== STAGE 2: API Health Check ===" -ForegroundColor Cyan

$healthzUrl = "https://api.hakwonplus.com/healthz"
$healthUrl = "https://api.hakwonplus.com/health"
$maxWait = 600
$elapsed = 0
$healthzOk = $false

Write-Host "  Waiting for /healthz 200 (max ${maxWait}s)..." -ForegroundColor White
while ($elapsed -lt $maxWait) {
    try {
        $resp = Invoke-WebRequest -Uri $healthzUrl -UseBasicParsing -TimeoutSec 10 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) { $healthzOk = $true; break }
    } catch { }
    Start-Sleep -Seconds 15
    $elapsed += 15
    Write-Host "    ... ${elapsed}s elapsed" -ForegroundColor DarkGray
}

if ($healthzOk) {
    Add-Result "2-HEALTH" "/healthz" "PASS" "200 OK (${elapsed}s)"
} else {
    Add-Result "2-HEALTH" "/healthz" "FAIL" "Not 200 after ${maxWait}s"
}

# /health (readiness)
try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp2 = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    $sw.Stop()
    $ms = $sw.ElapsedMilliseconds
    if ($resp2.StatusCode -eq 200) {
        $status = if ($ms -gt 2000) { "WARN" } else { "PASS" }
        Add-Result "2-HEALTH" "/health" $status "200 OK (${ms}ms)"
    } else {
        Add-Result "2-HEALTH" "/health" "FAIL" "$($resp2.StatusCode)"
    }
} catch {
    Add-Result "2-HEALTH" "/health" "FAIL" "$_"
}

# ==============================================================================
# STAGE 3: API ASG Instance Status
# ==============================================================================
Write-Host "`n=== STAGE 3: API ASG Instance Status ===" -ForegroundColor Cyan

try {
    $asgJson = Invoke-Aws @("autoscaling", "describe-auto-scaling-groups",
        "--auto-scaling-group-names", $script:ApiASGName,
        "--query", "AutoScalingGroups[0].{Min:MinSize,Desired:DesiredCapacity,Max:MaxSize,Instances:Instances[*].[InstanceId,HealthStatus,LifecycleState]}",
        "--region", $Region) -ErrorMessage "describe-asg"
    $asg = $asgJson | ConvertFrom-Json
    $inService = @($asg.Instances | Where-Object { $_[2] -eq "InService" -and $_[1] -eq "Healthy" })
    $minSize = $asg.Min
    if ($inService.Count -ge $minSize) {
        Add-Result "3-ASG" "api-asg" "PASS" "InService=$($inService.Count) >= min=$minSize (Desired=$($asg.Desired), Max=$($asg.Max))"
    } else {
        Add-Result "3-ASG" "api-asg" "FAIL" "InService=$($inService.Count) < min=$minSize"
    }
    foreach ($inst in $asg.Instances) {
        Add-Result "3-ASG" "  $($inst[0])" $(if ($inst[1] -eq "Healthy" -and $inst[2] -eq "InService") { "PASS" } else { "WARN" }) "$($inst[1])/$($inst[2])"
    }
} catch {
    Add-Result "3-ASG" "api-asg" "FAIL" "$_"
}

# ==============================================================================
# STAGE 4: Worker ASG Status
# ==============================================================================
Write-Host "`n=== STAGE 4: Worker ASG Status ===" -ForegroundColor Cyan

foreach ($worker in @(
    @{ Name="messaging"; AsgName=$script:MessagingWorkerASGName },
    @{ Name="ai";        AsgName=$script:AiWorkerASGName }
)) {
    $asgName = $worker.AsgName
    if (-not $asgName) { $asgName = "academy-v1-$($worker.Name)-worker-asg" }
    try {
        $wJson = Invoke-Aws @("autoscaling", "describe-auto-scaling-groups",
            "--auto-scaling-group-names", $asgName,
            "--query", "AutoScalingGroups[0].{Desired:DesiredCapacity,InService:length(Instances[?LifecycleState=='InService']),Min:MinSize,Max:MaxSize}",
            "--region", $Region) -ErrorMessage "describe-$($worker.Name)-asg"
        $w = $wJson | ConvertFrom-Json
        if ($null -eq $w -or $null -eq $w.Desired) {
            Add-Result "4-WORKERS" "$($worker.Name)-asg" "WARN" "ASG not found: $asgName"
        } else {
            $status = if ($w.InService -eq $w.Desired) { "PASS" } else { "WARN" }
            Add-Result "4-WORKERS" "$($worker.Name)-asg" $status "InService=$($w.InService)/Desired=$($w.Desired) (idle=0 정상)"
        }
    } catch {
        Add-Result "4-WORKERS" "$($worker.Name)-asg" "WARN" "$_"
    }
}

# ==============================================================================
# STAGE 5: SQS Queue Connectivity
# ==============================================================================
Write-Host "`n=== STAGE 5: SQS Queue Connectivity ===" -ForegroundColor Cyan

foreach ($q in @(
    @{ Name="messaging"; QueueName="academy-v1-messaging-queue" },
    @{ Name="ai";        QueueName="academy-v1-ai-queue" }
)) {
    try {
        $urlJson = Invoke-Aws @("sqs", "get-queue-url",
            "--queue-name", $q.QueueName,
            "--region", $Region) -ErrorMessage "get-queue-url-$($q.Name)"
        $qUrl = ($urlJson | ConvertFrom-Json).QueueUrl

        $attrJson = Invoke-Aws @("sqs", "get-queue-attributes",
            "--queue-url", $qUrl,
            "--attribute-names", "All",
            "--region", $Region) -ErrorMessage "get-queue-attributes-$($q.Name)"
        $attrs = ($attrJson | ConvertFrom-Json).Attributes

        $visible = [int]$attrs.ApproximateNumberOfMessages
        $inFlight = [int]$attrs.ApproximateNumberOfMessagesNotVisible
        $visTimeout = $attrs.VisibilityTimeout

        $status = if ($visible -le 100) { "PASS" } else { "WARN" }
        Add-Result "5-SQS" "$($q.Name)-main" $status "Visible=$visible, InFlight=$inFlight, VisTimeout=${visTimeout}s"
    } catch {
        Add-Result "5-SQS" "$($q.Name)-main" "FAIL" "$_"
    }

    # DLQ
    try {
        $dlqUrlJson = Invoke-Aws @("sqs", "get-queue-url",
            "--queue-name", "$($q.QueueName)-dlq",
            "--region", $Region) -ErrorMessage "get-dlq-url-$($q.Name)"
        $dlqUrl = ($dlqUrlJson | ConvertFrom-Json).QueueUrl

        $dlqAttrJson = Invoke-Aws @("sqs", "get-queue-attributes",
            "--queue-url", $dlqUrl,
            "--attribute-names", "All",
            "--region", $Region) -ErrorMessage "get-dlq-attributes-$($q.Name)"
        $dlqAttrs = ($dlqAttrJson | ConvertFrom-Json).Attributes
        $dlqVisible = [int]$dlqAttrs.ApproximateNumberOfMessages

        $status = if ($dlqVisible -eq 0) { "PASS" } elseif ($dlqVisible -le 5) { "WARN" } else { "FAIL" }
        Add-Result "5-SQS" "$($q.Name)-dlq" $status "DLQ=$dlqVisible"
    } catch {
        Add-Result "5-SQS" "$($q.Name)-dlq" "WARN" "DLQ not found or error: $_"
    }
}

# ==============================================================================
# STAGE 6: Video Batch Connectivity
# ==============================================================================
Write-Host "`n=== STAGE 6: Video Batch Connectivity ===" -ForegroundColor Cyan

# SSM VIDEO_BATCH_* env verification
try {
    $ssmRaw = Invoke-Aws @("ssm", "get-parameter",
        "--name", "/academy/api/env",
        "--with-decryption",
        "--query", "Parameter.Value",
        "--output", "text",
        "--region", $Region) -ErrorMessage "ssm-get-api-env"

    $ssmJson = $ssmRaw
    if ($ssmRaw -match '^[A-Za-z0-9+/]+=*$') {
        try { $ssmJson = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($ssmRaw)) } catch { }
    }
    $ssmObj = $ssmJson | ConvertFrom-Json

    $batchKeys = @{
        "VIDEO_BATCH_JOB_QUEUE"          = "academy-v1-video-batch-queue"
        "VIDEO_BATCH_JOB_DEFINITION"     = "academy-v1-video-batch-jobdef"
        "VIDEO_BATCH_JOB_QUEUE_LONG"     = "academy-v1-video-batch-long-queue"
        "VIDEO_BATCH_JOB_DEFINITION_LONG"= "academy-v1-video-batch-long-jobdef"
    }
    foreach ($kv in $batchKeys.GetEnumerator()) {
        $actual = $ssmObj.PSObject.Properties[$kv.Key].Value
        if ($actual -eq $kv.Value) {
            Add-Result "6-BATCH" "ssm/$($kv.Key)" "PASS" "$actual"
        } else {
            Add-Result "6-BATCH" "ssm/$($kv.Key)" "FAIL" "actual='$actual' expected='$($kv.Value)'"
        }
    }

    # REDIS_HOST check
    $redisHost = $ssmObj.PSObject.Properties["REDIS_HOST"].Value
    if ($redisHost) {
        Add-Result "6-BATCH" "ssm/REDIS_HOST" "PASS" "$redisHost"
    } else {
        Add-Result "6-BATCH" "ssm/REDIS_HOST" "FAIL" "missing"
    }
} catch {
    Add-Result "6-BATCH" "ssm-api-env" "FAIL" "$_"
}

# Batch queue status
foreach ($bq in @(
    @{ Name="standard"; Queue="academy-v1-video-batch-queue" },
    @{ Name="long";     Queue="academy-v1-video-batch-long-queue" },
    @{ Name="ops";      Queue="academy-v1-video-ops-queue" }
)) {
    try {
        $bqJson = Invoke-Aws @("batch", "describe-job-queues",
            "--job-queues", $bq.Queue,
            "--query", "jobQueues[0].{state:state,status:status}",
            "--region", $Region) -ErrorMessage "batch-queue-$($bq.Name)"
        $bqState = $bqJson | ConvertFrom-Json
        if ($bqState.state -eq "ENABLED" -and $bqState.status -eq "VALID") {
            Add-Result "6-BATCH" "queue/$($bq.Name)" "PASS" "ENABLED/VALID"
        } else {
            Add-Result "6-BATCH" "queue/$($bq.Name)" "FAIL" "$($bqState.state)/$($bqState.status)"
        }
    } catch {
        Add-Result "6-BATCH" "queue/$($bq.Name)" "FAIL" "$_"
    }
}

# Batch CE status
foreach ($ce in @(
    @{ Name="standard"; CE="academy-v1-video-batch-ce" },
    @{ Name="long";     CE="academy-v1-video-batch-long-ce" },
    @{ Name="ops";      CE="academy-v1-video-ops-ce" }
)) {
    try {
        $ceJson = Invoke-Aws @("batch", "describe-compute-environments",
            "--compute-environments", $ce.CE,
            "--query", "computeEnvironments[0].{state:state,status:status}",
            "--region", $Region) -ErrorMessage "batch-ce-$($ce.Name)"
        $ceState = $ceJson | ConvertFrom-Json
        if ($ceState.state -eq "ENABLED" -and $ceState.status -eq "VALID") {
            Add-Result "6-BATCH" "ce/$($ce.Name)" "PASS" "ENABLED/VALID"
        } else {
            Add-Result "6-BATCH" "ce/$($ce.Name)" "FAIL" "$($ceState.state)/$($ceState.status)"
        }
    } catch {
        Add-Result "6-BATCH" "ce/$($ce.Name)" "FAIL" "$_"
    }
}

# ==============================================================================
# STAGE 7: Workers SSM Env Verification
# ==============================================================================
Write-Host "`n=== STAGE 7: Workers SSM Env Verification ===" -ForegroundColor Cyan

try {
    $wEnvRaw = Invoke-Aws @("ssm", "get-parameter",
        "--name", "/academy/workers/env",
        "--with-decryption",
        "--query", "Parameter.Value",
        "--output", "text",
        "--region", $Region) -ErrorMessage "ssm-get-workers-env"

    $wEnvJson = $wEnvRaw
    try { $wEnvJson = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($wEnvRaw)) } catch { }
    $wEnvObj = $wEnvJson | ConvertFrom-Json

    $requiredKeys = @(
        "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
        "REDIS_HOST", "REDIS_PORT",
        "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT",
        "API_BASE_URL", "INTERNAL_WORKER_TOKEN",
        "DJANGO_SETTINGS_MODULE"
    )
    foreach ($k in $requiredKeys) {
        $v = $wEnvObj.PSObject.Properties[$k].Value
        if ($v -and $v.Trim() -ne "") {
            $display = if ($k -match "PASSWORD|SECRET|TOKEN|KEY") { "***" } else { $v }
            Add-Result "7-WENV" "workers/$k" "PASS" "$display"
        } else {
            Add-Result "7-WENV" "workers/$k" "FAIL" "missing or empty"
        }
    }

    # Messaging-specific
    $mqName = $wEnvObj.PSObject.Properties["MESSAGING_SQS_QUEUE_NAME"].Value
    if ($mqName -eq "academy-v1-messaging-queue") {
        Add-Result "7-WENV" "workers/MESSAGING_SQS_QUEUE_NAME" "PASS" "$mqName"
    } elseif ($mqName) {
        Add-Result "7-WENV" "workers/MESSAGING_SQS_QUEUE_NAME" "WARN" "$mqName (expected academy-v1-messaging-queue)"
    } else {
        Add-Result "7-WENV" "workers/MESSAGING_SQS_QUEUE_NAME" "WARN" "not set"
    }
} catch {
    Add-Result "7-WENV" "workers-env" "FAIL" "$_"
}

# ==============================================================================
# STAGE 8: EventBridge Rules
# ==============================================================================
Write-Host "`n=== STAGE 8: EventBridge Rules ===" -ForegroundColor Cyan

foreach ($rule in @(
    @{ Name="reconcile";       RuleName="academy-v1-reconcile-video-jobs" },
    @{ Name="scan-stuck";      RuleName="academy-v1-video-scan-stuck-rate" },
    @{ Name="enqueue-uploaded"; RuleName="academy-v1-enqueue-uploaded-videos" }
)) {
    try {
        $ruleJson = Invoke-Aws @("events", "describe-rule",
            "--name", $rule.RuleName,
            "--region", $Region) -ErrorMessage "eventbridge-$($rule.Name)"
        $ruleObj = $ruleJson | ConvertFrom-Json
        if ($ruleObj.State -eq "ENABLED") {
            Add-Result "8-EVENTS" $rule.Name "PASS" "ENABLED — $($ruleObj.ScheduleExpression)"
        } else {
            Add-Result "8-EVENTS" $rule.Name "WARN" "$($ruleObj.State)"
        }
    } catch {
        Add-Result "8-EVENTS" $rule.Name "WARN" "$_"
    }
}

# ==============================================================================
# FINAL REPORT
# ==============================================================================
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host " VERIFICATION REPORT" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

$passCount = ($report | Where-Object { $_.Status -eq "PASS" }).Count
$warnCount = ($report | Where-Object { $_.Status -eq "WARN" }).Count
$failCount = ($report | Where-Object { $_.Status -eq "FAIL" }).Count

Write-Host "`n  PASS: $passCount  |  WARN: $warnCount  |  FAIL: $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } elseif ($warnCount -gt 0) { "Yellow" } else { "Green" })

$verdict = if ($failCount -gt 0) { "FAIL" } elseif ($warnCount -gt 0) { "WARNING" } else { "PASS" }
Write-Host "`n  VERDICT: $verdict" -ForegroundColor $(if ($verdict -eq "FAIL") { "Red" } elseif ($verdict -eq "WARNING") { "Yellow" } else { "Green" })

# Save report
$reportDir = Join-Path (Get-RepoRoot) "docs\00-SSOT\v1\reports"
if (-not (Test-Path $reportDir)) { New-Item -ItemType Directory -Path $reportDir -Force | Out-Null }
$reportPath = Join-Path $reportDir "api-deploy-worker-verify.latest.md"

$md = @"
# API Deploy + Worker Verification Report

**Generated:** $timestamp
**SSOT Version:** V1.0.0
**Verdict:** $verdict (PASS=$passCount, WARN=$warnCount, FAIL=$failCount)

---

| Stage | Item | Status | Detail |
|-------|------|--------|--------|
"@

foreach ($r in $report) {
    $md += "`n| $($r.Stage) | $($r.Item) | **$($r.Status)** | $($r.Detail) |"
}

$md += @"

---

**SSOT Reference:** ``docs/00-SSOT/v1/DEPLOY-VERIFICATION-SSOT.md`` (V1.0.0)
"@

$md | Out-File -FilePath $reportPath -Encoding UTF8 -Force
Write-Host "`n  Report saved: $reportPath" -ForegroundColor DarkGray

if ($verdict -eq "FAIL") { exit 1 }
exit 0
