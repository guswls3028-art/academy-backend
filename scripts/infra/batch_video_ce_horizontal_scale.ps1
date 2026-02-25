# ==============================================================================
# Video Batch CE Horizontal Scale: c6g.large only, maxvCpus=32.
# - Creates academy-video-batch-ce-v2 with instanceTypes=["c6g.large"], min=0, max=32.
# - Updates academy-video-batch-queue to use v2 only; disables academy-video-batch-ce.
# - Ops CE/Queue untouched. Idempotent (v2 exists then skip create; queue already v2 then skip).
# Usage: .\scripts\infra\batch_video_ce_horizontal_scale.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$CurrentVideoCEName = "academy-video-batch-ce",
    [string]$NewVideoCEName = "academy-video-batch-ce-v2",
    [string]$VideoQueueName = "academy-video-batch-queue",
    [string[]]$InstanceTypes = @("c6g.large"),
    [int]$MinvCpus = 0,
    [int]$MaxvCpus = 32
)

$ErrorActionPreference = "Stop"
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

$script:ChangesApplied = [System.Collections.ArrayList]::new()
$script:BeforeCe = $null
$script:AfterCe = $null

function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

function Invoke-Aws {
    param([string[]]$ArgsArray, [string]$ErrorMessage = "AWS CLI failed")
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & aws @ArgsArray 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErr
    if ($exitCode -ne 0) {
        $text = ($out | Out-String).Trim()
        throw "${ErrorMessage}. ExitCode=$exitCode. Output: $text"
    }
    return $out
}

Write-Host "== Video Batch CE Horizontal Scale (c6g.large, maxvCpus=$MaxvCpus) ==" -ForegroundColor Cyan
Write-Host "Region=$Region | Current CE=$CurrentVideoCEName | New CE=$NewVideoCEName | Queue=$VideoQueueName" -ForegroundColor Gray

# ----- 1) Describe current Video CE (before state) -----
$ceList = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $CurrentVideoCEName, "--region", $Region, "--output", "json")
$currentCe = $ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $CurrentVideoCEName } | Select-Object -First 1
if (-not $currentCe) {
    Write-Host "FAIL: $CurrentVideoCEName not found. Create Video Batch first (batch_video_setup or recreate_batch_in_api_vpc)." -ForegroundColor Red
    exit 1
}

$script:BeforeCe = @{
    name   = $currentCe.computeEnvironmentName
    status = $currentCe.status
    state  = $currentCe.state
    types  = ($currentCe.computeResources.instanceTypes -join ", ")
    min    = $currentCe.computeResources.minvCpus
    max    = $currentCe.computeResources.maxvCpus
}

Write-Host "`n[1] Current Video CE (before)" -ForegroundColor Cyan
Write-Host "  Name=$($script:BeforeCe.name) Status=$($script:BeforeCe.status) State=$($script:BeforeCe.state)" -ForegroundColor Gray
Write-Host "  instanceTypes=$($script:BeforeCe.types) minvCpus=$($script:BeforeCe.min) maxvCpus=$($script:BeforeCe.max)" -ForegroundColor Gray

$cr = $currentCe.computeResources
$serviceRoleArn = $currentCe.serviceRole
$instanceProfileArn = $cr.instanceRole
$subnets = @($cr.subnets)
$securityGroupIds = @($cr.securityGroupIds)
if (-not $serviceRoleArn -or -not $instanceProfileArn -or $subnets.Count -eq 0 -or $securityGroupIds.Count -eq 0) {
    Write-Host "FAIL: Could not read serviceRole/instanceRole/subnets/securityGroupIds from current CE." -ForegroundColor Red
    exit 1
}

# ----- 2) Create New CE (v2) or use existing -----
$existingV2 = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $NewVideoCEName, "--region", $Region, "--output", "json")
$v2Obj = $existingV2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $NewVideoCEName } | Select-Object -First 1

if (-not $v2Obj) {
    Write-Host "`n[2] Creating $NewVideoCEName (c6g.large only, maxvCpus=$MaxvCpus)" -ForegroundColor Cyan
    $subnetArr = ($subnets | ForEach-Object { "`"$_`"" }) -join ","
    $sgArr = ($securityGroupIds | ForEach-Object { "`"$_`"" }) -join ","
    $typesArr = ($InstanceTypes | ForEach-Object { "`"$_`"" }) -join ","
    $cePayload = @{
        computeEnvironmentName = $NewVideoCEName
        type                   = "MANAGED"
        state                  = "ENABLED"
        serviceRole            = $serviceRoleArn
        computeResources       = @{
            type                 = "EC2"
            allocationStrategy   = "BEST_FIT_PROGRESSIVE"
            minvCpus             = $MinvCpus
            maxvCpus             = $MaxvCpus
            desiredvCpus         = 0
            instanceTypes        = @($InstanceTypes)
            subnets              = @($subnets)
            securityGroupIds     = @($securityGroupIds)
            instanceRole         = $instanceProfileArn
        }
    }
    $ceFile = Join-Path $RepoRoot "batch_video_ce_v2_temp.json"
    $ceJson = $cePayload | ConvertTo-Json -Depth 6 -Compress:$false
    [System.IO.File]::WriteAllText($ceFile, $ceJson, $utf8NoBom)
    $ceFileAbs = [System.IO.Path]::GetFullPath($ceFile)
    try {
        Invoke-Aws -ArgsArray @("batch", "create-compute-environment", "--cli-input-json", $ceFileAbs, "--region", $Region) -ErrorMessage "create-compute-environment $NewVideoCEName failed"
        [void]$script:ChangesApplied.Add("Created compute environment: $NewVideoCEName")
    } finally {
        Remove-Item $ceFile -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "`n[2] $NewVideoCEName already exists; skip create." -ForegroundColor Gray
}

# Wait for new CE VALID
Write-Host "  Waiting for $NewVideoCEName VALID..." -ForegroundColor Gray
$wait = 0
while ($wait -lt 180) {
    $v2List = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $NewVideoCEName, "--region", $Region, "--output", "json")
    $v2Obj = $v2List.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $NewVideoCEName } | Select-Object -First 1
    $v2Status = $v2Obj.status
    if ($v2Status -eq "VALID") { break }
    if ($v2Status -eq "INVALID") {
        Write-Host "FAIL: $NewVideoCEName state INVALID." -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Seconds 5
    $wait += 5
}
$newCeArn = $v2Obj.computeEnvironmentArn
if (-not $newCeArn) {
    Write-Host "FAIL: Could not get $NewVideoCEName ARN." -ForegroundColor Red
    exit 1
}
$script:AfterCe = @{
    name   = $v2Obj.computeEnvironmentName
    status = $v2Obj.status
    state  = $v2Obj.state
    types  = ($v2Obj.computeResources.instanceTypes -join ", ")
    min    = $v2Obj.computeResources.minvCpus
    max    = $v2Obj.computeResources.maxvCpus
}
Write-Host "  CE ARN: $newCeArn" -ForegroundColor Green

# ----- 3) Job Queue: verify only Video CE, then point to v2 -----
$jqList = ExecJson @("batch", "describe-job-queues", "--job-queues", $VideoQueueName, "--region", $Region, "--output", "json")
if (-not $jqList -or -not $jqList.jobQueues) {
    Write-Host "FAIL: Job queue $VideoQueueName not found." -ForegroundColor Red
    exit 1
}
$qObj = $jqList.jobQueues | Where-Object { $_.jobQueueName -eq $VideoQueueName } | Select-Object -First 1
$currentOrder = $qObj.computeEnvironmentOrder
$ceArnsInQueue = @($currentOrder | ForEach-Object { $_.computeEnvironment })
$opsCeName = "academy-video-ops-ce"
$opsCeList = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $opsCeName, "--region", $Region, "--output", "json")
$opsCeArn = $null
if ($opsCeList -and $opsCeList.computeEnvironments) {
    $opsCeObj = $opsCeList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $opsCeName } | Select-Object -First 1
    if ($opsCeObj) { $opsCeArn = $opsCeObj.computeEnvironmentArn }
}
foreach ($arn in $ceArnsInQueue) {
    if ($opsCeArn -and $arn -eq $opsCeArn) {
        Write-Host "FAIL: Queue $VideoQueueName has Ops CE in computeEnvironmentOrder. Remove Ops CE from Video queue (use only Video CE)." -ForegroundColor Red
        exit 1
    }
}
Write-Host "`n[3] Job Queue ${VideoQueueName}: ENABLED=$($qObj.state -eq 'ENABLED'), computeEnvironmentOrder has Video CE only (no Ops)." -ForegroundColor Green

$firstCeInQueue = ($currentOrder | Where-Object { $_.order -eq 1 } | Select-Object -First 1).computeEnvironment
if ($firstCeInQueue -ne $newCeArn) {
    Write-Host "  Updating queue to use $NewVideoCEName..." -ForegroundColor Yellow
    if ($qObj.state -eq "ENABLED") {
        Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--job-queue", $VideoQueueName, "--state", "DISABLED", "--region", $Region) -ErrorMessage "disable job queue failed"
        $waitQ = 0
        while ($waitQ -lt 60) {
            Start-Sleep -Seconds 3
            $waitQ += 3
            $jq2 = ExecJson @("batch", "describe-job-queues", "--job-queues", $VideoQueueName, "--region", $Region, "--output", "json")
            $s = ($jq2.jobQueues | Where-Object { $_.jobQueueName -eq $VideoQueueName } | Select-Object -First 1).state
            if ($s -eq "DISABLED") { break }
        }
    }
    $orderObj = @(@{ order = 1; computeEnvironment = $newCeArn })
    $updatePayload = @{ jobQueue = $VideoQueueName; computeEnvironmentOrder = $orderObj }
    $updateFile = Join-Path $RepoRoot "batch_update_queue_temp.json"
    [System.IO.File]::WriteAllText($updateFile, ($updatePayload | ConvertTo-Json -Depth 5), $utf8NoBom)
    $updateFileAbs = [System.IO.Path]::GetFullPath($updateFile)
    Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--cli-input-json", $updateFileAbs, "--region", $Region) -ErrorMessage "update-job-queue computeEnvironmentOrder failed"
    Remove-Item $updateFile -Force -ErrorAction SilentlyContinue
    Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--job-queue", $VideoQueueName, "--state", "ENABLED", "--region", $Region) -ErrorMessage "re-enable job queue failed"
    [void]$script:ChangesApplied.Add("Updated $VideoQueueName to use $NewVideoCEName")
} else {
    Write-Host "  Queue already uses $NewVideoCEName; skip update." -ForegroundColor Gray
}

# ----- 4) Disable old Video CE -----
if ($currentCe.state -eq "ENABLED") {
    Write-Host "`n[4] Disabling old $CurrentVideoCEName" -ForegroundColor Cyan
    Invoke-Aws -ArgsArray @("batch", "update-compute-environment", "--compute-environment", $CurrentVideoCEName, "--state", "DISABLED", "--region", $Region) -ErrorMessage "disable $CurrentVideoCEName failed"
    [void]$script:ChangesApplied.Add("Disabled compute environment: $CurrentVideoCEName")
} else {
    Write-Host "`n[4] $CurrentVideoCEName already DISABLED." -ForegroundColor Gray
}

# ----- 5) Write state -----
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$finalState = @{
    FinalComputeEnvName = $NewVideoCEName
    FinalComputeEnvArn  = $newCeArn
    FinalJobQueueName   = $VideoQueueName
    FinalJobQueueArn    = $qObj.jobQueueArn
    WorkerJobDefName    = "academy-video-batch-jobdef"
    OpsJobDefNames      = @("academy-video-ops-reconcile", "academy-video-ops-scanstuck", "academy-video-ops-netprobe")
}
$finalStatePath = Join-Path $OutDir "batch_final_state.json"
[System.IO.File]::WriteAllText($finalStatePath, ($finalState | ConvertTo-Json), $utf8NoBom)
Write-Host "`n[5] Wrote $finalStatePath (FinalComputeEnvName=$NewVideoCEName)" -ForegroundColor Gray

# ----- 6) Output: before/after, changed resources, throughput -----
Write-Host "`n========== Changes ==========" -ForegroundColor Cyan
if ($script:ChangesApplied.Count -gt 0) {
    foreach ($c in $script:ChangesApplied) { Write-Host "  - $c" -ForegroundColor Yellow }
} else {
    Write-Host "  (no new changes; already in desired state)" -ForegroundColor Gray
}

Write-Host "`n========== Before / After ==========" -ForegroundColor Cyan
$beforeRow = [PSCustomObject]@{
    Resource = "Video CE"
    Name     = $script:BeforeCe.name
    instanceTypes = $script:BeforeCe.types
    minvCpus = $script:BeforeCe.min
    maxvCpus = $script:BeforeCe.max
    State    = $script:BeforeCe.state
}
$afterRow = [PSCustomObject]@{
    Resource = "Video CE"
    Name     = $script:AfterCe.name
    instanceTypes = $script:AfterCe.types
    minvCpus = $script:AfterCe.min
    maxvCpus = $script:AfterCe.max
    State    = $script:AfterCe.state
}
@($beforeRow, $afterRow) | Format-Table -AutoSize

# c6g.large = 2 vCPUs per instance -> max 32/2 = 16 instances
$vcpusPerLarge = 2
$concurrentLarge = [math]::Floor($MaxvCpus / $vcpusPerLarge)
Write-Host "Concurrent c6g.large instances (max): $concurrentLarge" -ForegroundColor Cyan
Write-Host "Expected concurrent video jobs (1 job per instance): up to $concurrentLarge" -ForegroundColor Cyan

# ----- 7) Audit -----
Write-Host "`n========== Audit ==========" -ForegroundColor Cyan
$auditScript = Join-Path $ScriptRoot "infra_one_take_full_audit.ps1"
if (Test-Path -LiteralPath $auditScript) {
    & $auditScript -Region $Region
    $auditExit = $LASTEXITCODE
    if ($auditExit -ne 0) {
        Write-Host "`nAudit returned non-PASS; running FixMode..." -ForegroundColor Yellow
        & $auditScript -Region $Region -FixMode
    }
} else {
    Write-Host "  (infra_one_take_full_audit.ps1 not found; skip audit)" -ForegroundColor Gray
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "Video Batch Throughput Optimized – LARGE Horizontal Scaling Active" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
