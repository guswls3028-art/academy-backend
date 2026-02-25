# Deterministic Batch job definition fix: list all ACTIVE revisions, take highest by revision number,
# enforce memory=3584, submit test job, poll, describe CE. No [-1] or index guessing.
# Usage: .\scripts\infra\batch_video_fix_memory_and_verify.ps1 -Region ap-northeast-2

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$JobDefName = "academy-video-batch-jobdef",
    [string]$JobQueueName = "academy-video-batch-queue"
)

$ErrorActionPreference = "Stop"

function Invoke-AwsJson {
    param([string[]]$Arguments)
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & aws @Arguments 2>&1
        $text = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($text)) { return $null }
        return $text | ConvertFrom-Json
    } catch { return $null }
    finally { $ErrorActionPreference = $prevErr }
}

# --- 1) List ALL ACTIVE revisions, sort by revision numerically, identify highest ---
$allDefs = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $JobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
if (-not $allDefs -or -not $allDefs.jobDefinitions) {
    Write-Host "describe-job-definitions returned no ACTIVE jobDefinitions"
    Write-Host "ROOT CAUSE: No ACTIVE job definition found for $JobDefName"
    Write-Host "FIX APPLIED: None"
    Write-Host "CURRENT STATUS: N/A"
    exit 1
}

$list = @($allDefs.jobDefinitions)
$sorted = $list | Sort-Object { [int]($_.revision) }
$maxRevision = -1
foreach ($d in $sorted) {
    $r = [int]$d.revision
    if ($r -gt $maxRevision) { $maxRevision = $r }
}
$activeDef = $null
foreach ($d in $list) {
    if ([int]$d.revision -eq $maxRevision) { $activeDef = $d; break }
}
if (-not $activeDef) {
    Write-Host "ROOT CAUSE: Could not resolve highest ACTIVE revision"
    Write-Host "FIX APPLIED: None"
    Write-Host "CURRENT STATUS: N/A"
    exit 1
}

# --- 2) Print for highest ACTIVE revision ---
$rev = $activeDef.revision
$mem = $activeDef.containerProperties.memory
$vcpus = $activeDef.containerProperties.vcpus
$cmd = $activeDef.containerProperties.command
$jobRoleArn = $activeDef.containerProperties.jobRoleArn
$execRoleArn = $activeDef.containerProperties.executionRoleArn

Write-Host "revision: $rev"
Write-Host "containerProperties.memory: $mem"
Write-Host "containerProperties.vcpus: $vcpus"
Write-Host "command: $($cmd -join ' ')"
Write-Host "jobRoleArn: $jobRoleArn"
Write-Host "executionRoleArn: $execRoleArn"
Write-Host "ACTIVE_REVISION = $rev"
Write-Host "ACTIVE_MEMORY = $mem"

$revisionToUse = $rev
$needRegister = ($mem -ne 3584)

if ($needRegister) {
    # --- 3) Build register payload from ACTIVE definition; remove illegal keys; set memory=3584; register ---
    $illegal = @("revision", "status", "jobDefinitionArn", "containerOrchestrationType")
    $registerObj = @{}
    foreach ($key in $activeDef.PSObject.Properties.Name) {
        if ($key -notin $illegal) { $registerObj[$key] = $activeDef.$key }
    }
    $registerObj.containerProperties.memory = 3584
    $jdFile = Join-Path $env:TEMP "batch_jd_register_$(Get-Date -Format 'yyyyMMddHHmmss').json"
    $absPath = [System.IO.Path]::GetFullPath($jdFile)
    $jsonStr = $registerObj | ConvertTo-Json -Depth 25 -Compress:$false
    if ($jsonStr -match '"JobDefinitionName"') { $jsonStr = $jsonStr -replace '"JobDefinitionName"', '"jobDefinitionName"' }
    if ($jsonStr -match '"ContainerProperties"') { $jsonStr = $jsonStr -replace '"ContainerProperties"', '"containerProperties"' }
    if ($jsonStr -match '"ContainerProperties":\s*\{') {
        $jsonStr = $jsonStr -replace '"Memory":', '"memory":' -replace '"Vcpus":', '"vcpus":' -replace '"Image":', '"image":' -replace '"Command":', '"command":' -replace '"JobRoleArn":', '"jobRoleArn":' -replace '"ExecutionRoleArn":', '"executionRoleArn":' -replace '"ResourceRequirements":', '"resourceRequirements":' -replace '"LogConfiguration":', '"logConfiguration":' -replace '"Environment":', '"environment":' -replace '"Secrets":', '"secrets":' -replace '"MountPoints":', '"mountPoints":' -replace '"Volumes":', '"volumes":' -replace '"LinuxParameters":', '"linuxParameters":'
        $jsonStr = $jsonStr -replace '"LogDriver":', '"logDriver":' -replace '"Options":', '"options":' -replace '"Awslogs-group":', '"awslogs-group":' -replace '"Awslogs-region":', '"awslogs-region":' -replace '"Awslogs-stream-prefix":', '"awslogs-stream-prefix":'
    }
    if ($jsonStr -match '"PlatformCapabilities"') { $jsonStr = $jsonStr -replace '"PlatformCapabilities"', '"platformCapabilities"' }
    if ($jsonStr -match '"Parameters"') { $jsonStr = $jsonStr -replace '"Parameters"', '"parameters"' }
    if ($jsonStr -match '"RetryStrategy"') { $jsonStr = $jsonStr -replace '"RetryStrategy"', '"retryStrategy"' }
    if ($jsonStr -match '"Attempts":') { $jsonStr = $jsonStr -replace '"Attempts":', '"attempts":' }
    if ($jsonStr -match '"Timeout"') { $jsonStr = $jsonStr -replace '"Timeout"', '"timeout"' }
    if ($jsonStr -match '"AttemptDurationSeconds"') { $jsonStr = $jsonStr -replace '"AttemptDurationSeconds"', '"attemptDurationSeconds"' }
    if ($jsonStr -match '"Type"') { $jsonStr = $jsonStr -replace '(\s)"Type":', '$1"type":' }
    [System.IO.File]::WriteAllText($jdFile, $jsonStr, [System.Text.UTF8Encoding]::new($false))
    $fileUri = "file:///" + ($absPath -replace '\\', '/')
    $regOut = Invoke-AwsJson @("batch", "register-job-definition", "--cli-input-json", $fileUri, "--region", $Region, "--output", "json")
    Remove-Item $jdFile -Force -ErrorAction SilentlyContinue
    if (-not $regOut -or -not $regOut.revision) {
        Write-Host "ROOT CAUSE: ACTIVE_MEMORY was $mem (expected 3584); register-job-definition failed"
        Write-Host "FIX APPLIED: Attempted re-register; failed"
        Write-Host "CURRENT STATUS: N/A"
        exit 1
    }
    $revisionToUse = $regOut.revision
    $verifyDef = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition", "${JobDefName}:$revisionToUse", "--region", $Region, "--output", "json")
    $newMem = $null
    if ($verifyDef -and $verifyDef.jobDefinitions -and $verifyDef.jobDefinitions.Count -gt 0) {
        foreach ($d in $verifyDef.jobDefinitions) {
            if ([int]$d.revision -eq $revisionToUse) { $newMem = $d.containerProperties.memory; break }
        }
    }
    if ($newMem -ne 3584) {
        Write-Host "ROOT CAUSE: ACTIVE_MEMORY was $mem; re-registered but new revision memory is $newMem (expected 3584)"
        Write-Host "FIX APPLIED: Registered revision $revisionToUse"
        Write-Host "CURRENT STATUS: New revision memory=$newMem"
        exit 1
    }
    Write-Host "Registered new revision $revisionToUse; containerProperties.memory = 3584 (verified)"
}

# --- 4) Submit test job with exact revision ---
$submitOut = Invoke-AwsJson @("batch", "submit-job", "--job-name", "batch-verify-$([guid]::NewGuid().ToString().Substring(0,8))", "--job-queue", $JobQueueName, "--job-definition", "${JobDefName}:$revisionToUse", "--parameters", "job_id=test123", "--region", $Region, "--output", "json")
if (-not $submitOut -or -not $submitOut.jobId) {
    Write-Host "ROOT CAUSE: submit-job failed"
    Write-Host "FIX APPLIED: $($needRegister ? "Re-registered jobdef with memory=3584 (revision $revisionToUse)" : "None")"
    Write-Host "CURRENT STATUS: Test job submit failed"
    exit 1
}
$awsJobId = $submitOut.jobId
Write-Host "Submitted job $awsJobId (job-definition ${JobDefName}:$revisionToUse)"

# --- 5) Poll every 10s for up to 120s ---
$pollInterval = 10
$maxWait = 120
$elapsed = 0
$lastStatus = $null
$lastReason = $null
while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds $pollInterval
    $elapsed += $pollInterval
    $jobDesc = Invoke-AwsJson @("batch", "describe-jobs", "--jobs", $awsJobId, "--region", $Region, "--output", "json")
    if ($jobDesc -and $jobDesc.jobs -and $jobDesc.jobs.Count -gt 0) {
        $job = $jobDesc.jobs[0]
        $lastStatus = $job.status
        $lastReason = $job.statusReason
        Write-Host "status: $lastStatus"
        Write-Host "statusReason: $lastReason"
        if ($lastStatus -eq "STARTING" -or $lastStatus -eq "RUNNING" -or $lastStatus -eq "SUCCEEDED" -or $lastStatus -eq "FAILED") { break }
    }
}

# --- 6) Describe compute environment ---
$queueDesc = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $JobQueueName, "--region", $Region, "--output", "json")
$ceArn = $null
if ($queueDesc -and $queueDesc.jobQueues -and $queueDesc.jobQueues.Count -gt 0) {
    $order = $queueDesc.jobQueues[0].computeEnvironmentOrder
    foreach ($o in $order) {
        if ($o.order -eq 1) { $ceArn = $o.computeEnvironment; break }
    }
}
$ceName = $null
if ($ceArn) { $ceName = $ceArn.Split("/")[-1]; if (-not $ceName) { $ceName = $ceArn.Split(":")[-1] } }
$ceDesired = $null
$ceMax = $null
$ceState = $null
$ceStatus = $null
if ($ceName) {
    $ceDesc = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $ceName, "--region", $Region, "--output", "json")
    if ($ceDesc -and $ceDesc.computeEnvironments -and $ceDesc.computeEnvironments.Count -gt 0) {
        $cr = $ceDesc.computeEnvironments[0].computeResources
        $ceDesired = $cr.desiredvCpus
        $ceMax = $cr.maxvCpus
        $ceState = $ceDesc.computeEnvironments[0].state
        $ceStatus = $ceDesc.computeEnvironments[0].status
    }
}
Write-Host "Compute environment ($ceName): desiredvCpus=$ceDesired maxvCpus=$ceMax state=$ceState status=$ceStatus"

# --- 7) Final output only ---
$rootCause = "ACTIVE_MEMORY was $mem (expected 3584)"
if (-not $needRegister) { $rootCause = "ACTIVE_MEMORY already 3584; no change required" }
$fixApplied = "None"
if ($needRegister) { $fixApplied = "Re-registered $JobDefName with containerProperties.memory=3584; new revision=$revisionToUse (verified)" }
$currentStatus = "JobDef $JobDefName revision=$revisionToUse memory=3584; Test job $awsJobId status=$lastStatus statusReason=$lastReason; CE desiredvCpus=$ceDesired maxvCpus=$ceMax state=$ceState status=$ceStatus"

Write-Host ""
Write-Host "ROOT CAUSE: $rootCause"
Write-Host "FIX APPLIED: $fixApplied"
Write-Host "CURRENT STATUS: $currentStatus"
