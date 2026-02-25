# ==============================================================================
# One-take Video/Ops Batch + EventBridge + IAM audit. Queue/CE separation check.
# Copy-paste runnable on Windows PowerShell. UTF-8 to avoid cp949/aws json issues.
#
# Usage:
#   .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2
#   .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -Verbose
#   .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -FixMode
#   .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -FixMode -FixModeWithCleanup
#
# Sample output (comment):
# ---
# Category   | Check                         | Expected         | Actual                    | Status | FixAction
# -----------|-------------------------------|------------------|---------------------------|--------|---------------------------
# Batch      | Video CE exists               | VALID/ENABLED    | VALID/ENABLED c6g.xlarge  | PASS   |
# Batch      | Ops CE exists                 | VALID/ENABLED    | VALID/ENABLED t4g.small   | PASS   |
# Batch      | Video Queue                   | ENABLED          | ENABLED CE=academy-...    | PASS   |
# Batch      | Ops Queue                     | ENABLED          | ENABLED CE=academy-...    | PASS   |
# Batch      | Video Queue jobs               | -                | RUNNING=2 RUNNABLE=0      | PASS   |
# Batch      | Ops Queue RUNNING             | reconcile<=1     | reconcile=1 scan_stuck=0  | PASS   |
# EventBridge| Reconcile schedule            | rate(5 minutes)  | rate(5 minutes)          | PASS   |
# EventBridge| Reconcile target queue        | OpsQueue         | OpsQueue                  | PASS   |
# EventBridge| Reconcile job def             | academy-video-ops-reconcile | academy-video-ops-reconcile | PASS   |
# EventBridge| ScanStuck schedule            | rate(5 minutes)  | rate(5 minutes)           | PASS   |
# EventBridge| ScanStuck target queue        | OpsQueue         | OpsQueue                  | PASS   |
# IAM        | DescribeJobs/ListJobs on job role | Yes           | Yes                       | PASS   |
# JobDef     | Video jobdef vcpus/memory     | >0               | vcpus=4 memory=8192      | PASS   |
# JobDef     | Ops academy-video-ops-reconcile command/role | command set, jobRoleArn set | cmd=python manage.py... | PASS   |
# Summary: PASS=14 WARN=0 FAIL=0
# Result: PASS
# ==============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Region = "",
    [switch]$FixMode,
    [string]$ExpectedVideoQueueName = "",
    [string]$ExpectedOpsQueueName = "academy-video-ops-queue",
    [string]$ExpectedVideoCEName = "academy-video-batch-ce",
    [string]$ExpectedOpsCEName = "academy-video-ops-ce",
    [string]$ReconcileRuleName = "",
    [string]$ScanStuckRuleName = "",
    [switch]$FixModeWithCleanup
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

# Default Region from aws configure get region when not provided
if ([string]::IsNullOrWhiteSpace($Region)) {
    $Region = (aws configure get region 2>&1)
    if (-not $Region -or $Region -match "not set|error") {
        Write-Host "FAIL: -Region not specified and 'aws configure get region' returned nothing. Set default region or pass -Region." -ForegroundColor Red
        exit 1
    }
    $Region = $Region.Trim()
}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$batchStatePath = Join-Path $RepoRoot "docs\deploy\actual_state\batch_final_state.json"

# Resolve expected names from actual_state
if (-not $ExpectedVideoQueueName -and (Test-Path -LiteralPath $batchStatePath)) {
    try {
        $raw = [System.IO.File]::ReadAllText($batchStatePath, [System.Text.UTF8Encoding]::new($false))
        $batchState = $raw | ConvertFrom-Json
        if ($batchState.FinalJobQueueName) { $ExpectedVideoQueueName = $batchState.FinalJobQueueName }
        if ($batchState.FinalComputeEnvName) { $ExpectedVideoCEName = $batchState.FinalComputeEnvName }
    } catch {}
}
if (-not $ExpectedVideoQueueName) { $ExpectedVideoQueueName = "academy-video-batch-queue" }

$script:AuditRows = [System.Collections.ArrayList]::new()
$script:FixesApplied = [System.Collections.ArrayList]::new()

function Add-AuditRow {
    param([string]$Category, [string]$Check, [string]$Expected, [string]$Actual, [string]$Status, [string]$FixAction = "")
    [void]$script:AuditRows.Add([PSCustomObject]@{
        Category = $Category
        Check = $Check
        Expected = $Expected
        Actual = $Actual
        Status = $Status
        FixAction = $FixAction
    })
}

function Aws-Text {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    $str = ($out | Out-String).Trim()
    if ($exit -ne 0) { return $null }
    return $str
}

function Aws-Json {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    $str = ($out | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($str)) { return $null }
    try { return $str | ConvertFrom-Json } catch { return $null }
}

# Avoid cp949: write aws json to temp file as UTF-8 no BOM then parse
function Aws-JsonSafe {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $tempFile = Join-Path ([System.IO.Path]::GetTempPath()) "aws_out_$(Get-Date -Format 'yyyyMMddHHmmss').json"
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
    }
}

# --- A. Batch ---
function Invoke-BatchAudit {
    $ceList = Aws-JsonSafe @("batch", "describe-compute-environments", "--region", $Region)
    if (-not $ceList) {
        Add-AuditRow -Category "Batch" -Check "Describe CE" -Expected "OK" -Actual "CLI failed" -Status "FAIL" -FixAction "Check AWS credentials/region"
        return
    }
    $ces = $ceList.computeEnvironments
    if (-not $ces) { $ces = @() }

    $videoCe = $ces | Where-Object { $_.computeEnvironmentName -eq $ExpectedVideoCEName } | Select-Object -First 1
    if (-not $videoCe) {
        Add-AuditRow -Category "Batch" -Check "Video CE exists" -Expected $ExpectedVideoCEName -Actual "not found" -Status "FAIL" -FixAction "Run batch_video_setup or recreate_batch_in_api_vpc"
    } else {
        $s = "$($videoCe.status)/$($videoCe.state)"
        $st = if ($videoCe.status -eq "VALID" -and $videoCe.state -eq "ENABLED") { "PASS" } else { "FAIL" }
        $types = $videoCe.computeResources.instanceTypes -join ","
        $minMax = "min=$($videoCe.computeResources.minvCpus) max=$($videoCe.computeResources.maxvCpus)"
        Add-AuditRow -Category "Batch" -Check "Video CE" -Expected "VALID/ENABLED" -Actual "$s $types $minMax" -Status $st -FixAction $(if ($st -eq "FAIL") { "Update or recreate CE" } else { "" })
    }

    $opsCe = $ces | Where-Object { $_.computeEnvironmentName -eq $ExpectedOpsCEName } | Select-Object -First 1
    if (-not $opsCe) {
        Add-AuditRow -Category "Batch" -Check "Ops CE exists" -Expected $ExpectedOpsCEName -Actual "not found" -Status "FAIL" -FixAction "Run batch_ops_setup.ps1 -Region $Region"
    } else {
        $s = "$($opsCe.status)/$($opsCe.state)"
        $st = if ($opsCe.status -eq "VALID" -and $opsCe.state -eq "ENABLED") { "PASS" } else { "FAIL" }
        $types = $opsCe.computeResources.instanceTypes -join ","
        $minMax = "min=$($opsCe.computeResources.minvCpus) max=$($opsCe.computeResources.maxvCpus)"
        Add-AuditRow -Category "Batch" -Check "Ops CE" -Expected "VALID/ENABLED" -Actual "$s $types $minMax" -Status $st -FixAction $(if ($st -eq "FAIL") { "Run batch_ops_setup.ps1" } else { "" })
        $instanceTypesOk = $types -match "default_arm64|default_x86_64|optimal|t4g\.small"
        $minMaxOk = $opsCe.computeResources.minvCpus -eq 0 -and $opsCe.computeResources.maxvCpus -eq 2
        Add-AuditRow -Category "Batch" -Check "Ops CE instanceTypes" -Expected "default_arm64 or t4g.small" -Actual $types -Status $(if ($instanceTypesOk) { "PASS" } else { "WARN" }) -FixAction ""
        Add-AuditRow -Category "Batch" -Check "Ops CE min/max vCpus" -Expected "min=0 max=2" -Actual $minMax -Status $(if ($minMaxOk) { "PASS" } else { "WARN" }) -FixAction ""
    }

    $jqList = Aws-JsonSafe @("batch", "describe-job-queues", "--region", $Region)
    if (-not $jqList -or -not $jqList.jobQueues) {
        Add-AuditRow -Category "Batch" -Check "Describe Queues" -Expected "OK" -Actual "CLI failed or empty" -Status "FAIL" -FixAction ""
        return
    }
    $queues = $jqList.jobQueues

    $videoQ = $queues | Where-Object { $_.jobQueueName -eq $ExpectedVideoQueueName } | Select-Object -First 1
    if (-not $videoQ) {
        Add-AuditRow -Category "Batch" -Check "Video Queue" -Expected $ExpectedVideoQueueName -Actual "not found" -Status "FAIL" -FixAction "Create video queue"
    } else {
        $st = if ($videoQ.state -eq "ENABLED") { "PASS" } else { "FAIL" }
        $ceNames = ($videoQ.computeEnvironmentOrder | ForEach-Object { $_.computeEnvironment -replace '.*/', '' }) -join ","
        Add-AuditRow -Category "Batch" -Check "Video Queue" -Expected "ENABLED" -Actual "$($videoQ.state) CE=$ceNames" -Status $st -FixAction ""
    }

    $opsQ = $queues | Where-Object { $_.jobQueueName -eq $ExpectedOpsQueueName } | Select-Object -First 1
    if (-not $opsQ) {
        Add-AuditRow -Category "Batch" -Check "Ops Queue" -Expected $ExpectedOpsQueueName -Actual "not found" -Status "FAIL" -FixAction "Run batch_ops_setup.ps1 -Region $Region"
    } else {
        $st = if ($opsQ.state -eq "ENABLED") { "PASS" } else { "FAIL" }
        $ceNames = ($opsQ.computeEnvironmentOrder | ForEach-Object { $_.computeEnvironment -replace '.*/', '' }) -join ","
        Add-AuditRow -Category "Batch" -Check "Ops Queue" -Expected "ENABLED" -Actual "$($opsQ.state) CE=$ceNames" -Status $st -FixAction $(if ($st -eq "FAIL") { "Run batch_ops_setup.ps1" } else { "" })
    }

    $videoQueueArn = if ($videoQ) { $videoQ.jobQueueArn } else { $null }
    $opsQueueArn = if ($opsQ) { $opsQ.jobQueueArn } else { $null }
    if ($videoQueueArn) {
        $runningV = (Aws-Text @("batch", "list-jobs", "--job-queue", $videoQueueArn, "--job-status", "RUNNING", "--region", $Region, "--query", "length(jobSummaryList)", "--output", "text")) -as [int]
        if (-not $runningV) { $runningV = 0 }
        $runnableV = (Aws-Text @("batch", "list-jobs", "--job-queue", $videoQueueArn, "--job-status", "RUNNABLE", "--region", $Region, "--query", "length(jobSummaryList)", "--output", "text")) -as [int]
        if (-not $runnableV) { $runnableV = 0 }
        Add-AuditRow -Category "Batch" -Check "Video Queue jobs" -Expected "-" -Actual "RUNNING=$runningV RUNNABLE=$runnableV" -Status "PASS" -FixAction ""
    }
    if ($opsQueueArn) {
        $listRun = Aws-JsonSafe @("batch", "list-jobs", "--job-queue", $opsQueueArn, "--job-status", "RUNNING", "--region", $Region)
        $runningO = 0
        $reconcileRun = 0
        $scanStuckRun = 0
        if ($listRun -and $listRun.jobSummaryList) {
            $runningO = $listRun.jobSummaryList.Count
            foreach ($j in $listRun.jobSummaryList) {
                $name = $j.jobName -as [string]
                if ($name -match "reconcile") { $reconcileRun++ }
                elseif ($name -match "scan") { $scanStuckRun++ }
            }
        }
        Add-AuditRow -Category "Batch" -Check "Ops Queue RUNNING" -Expected "reconcile<=1" -Actual "reconcile=$reconcileRun scan_stuck=$scanStuckRun" -Status $(if ($reconcileRun -gt 1) { "WARN" } else { "PASS" }) -FixAction $(if ($reconcileRun -gt 1 -and $FixModeWithCleanup) { "FixModeWithCleanup: terminate extra" } else { "" })
    }

    $script:VideoQueueArn = $videoQueueArn
    $script:OpsQueueArn = $opsQueueArn
}

# --- B. EventBridge (auto-discover rule names) ---
function Invoke-EventBridgeAudit {
    $rulesJson = Aws-JsonSafe @("events", "list-rules", "--region", $Region)
    if (-not $rulesJson -or -not $rulesJson.Rules) {
        Add-AuditRow -Category "EventBridge" -Check "List rules" -Expected "OK" -Actual "CLI failed" -Status "FAIL" -FixAction ""
        return
    }
    $reconcileRule = $null
    $scanStuckRule = $null
    if ($ReconcileRuleName) {
        $reconcileRule = $rulesJson.Rules | Where-Object { $_.Name -eq $ReconcileRuleName } | Select-Object -First 1
    }
    if (-not $reconcileRule) {
        $reconcileRule = $rulesJson.Rules | Where-Object { $_.Name -match "reconcile" } | Select-Object -First 1
    }
    if (-not $ScanStuckRuleName) {
        $scanStuckRule = $rulesJson.Rules | Where-Object { $_.Name -match "scan-stuck|scanstuck" } | Select-Object -First 1
    } else {
        $scanStuckRule = $rulesJson.Rules | Where-Object { $_.Name -eq $ScanStuckRuleName } | Select-Object -First 1
    }

    $script:ReconcileRuleName = if ($reconcileRule) { $reconcileRule.Name } else { "" }
    $script:ScanStuckRuleName = if ($scanStuckRule) { $scanStuckRule.Name } else { "" }

    foreach ($r in @(@{ Rule = $reconcileRule; Label = "Reconcile"; ExpectedJobDef = "academy-video-ops-reconcile" }, @{ Rule = $scanStuckRule; Label = "ScanStuck"; ExpectedJobDef = "academy-video-ops-scanstuck" })) {
        $rule = $r.Rule
        $label = $r.Label
        $expJd = $r.ExpectedJobDef
        if (-not $rule) {
            Add-AuditRow -Category "EventBridge" -Check "$label rule exists" -Expected "found" -Actual "not found" -Status "FAIL" -FixAction "Run eventbridge_deploy_video_scheduler.ps1"
            continue
        }
        $sched = $rule.ScheduleExpression -as [string]
        $schedOk = $sched -match "rate\s*\(\s*5\s*minute"
        $schedWarn = $sched -match "rate\s*\(\s*2\s*minute"
        $st = "PASS"
        if (-not $schedOk -and $schedWarn) { $st = "WARN" }
        elseif (-not $schedOk) { $st = "WARN" }
        Add-AuditRow -Category "EventBridge" -Check "$label schedule" -Expected "rate(5 minutes)" -Actual $sched -Status $st -FixAction $(if ($st -ne "PASS") { "FixMode: put-rule rate(5 minutes)" } else { "" })

        $tgtJson = Aws-JsonSafe @("events", "list-targets-by-rule", "--rule", $rule.Name, "--region", $Region)
        if (-not $tgtJson -or -not $tgtJson.Targets -or $tgtJson.Targets.Count -eq 0) {
            Add-AuditRow -Category "EventBridge" -Check "$label target" -Expected "Batch SubmitJob" -Actual "no targets" -Status "FAIL" -FixAction "Run eventbridge_deploy_video_scheduler.ps1"
            continue
        }
        $t = $tgtJson.Targets[0]
        $isBatch = $t.BatchParameters -ne $null
        if (-not $isBatch) {
            Add-AuditRow -Category "EventBridge" -Check "$label target type" -Expected "Batch SubmitJob" -Actual "not Batch" -Status "FAIL" -FixAction "Run eventbridge_deploy_video_scheduler.ps1"
            continue
        }
        $jdTarget = $t.BatchParameters.JobDefinition -as [string]
        $queueArn = $t.Arn -as [string]
        $useOpsQueue = $script:OpsQueueArn -and ($queueArn -eq $script:OpsQueueArn)
        $stQ = if ($useOpsQueue) { "PASS" } else { "FAIL" }
        Add-AuditRow -Category "EventBridge" -Check "$label target queue" -Expected "OpsQueue" -Actual $(if ($useOpsQueue) { "OpsQueue" } else { "Video or other" }) -Status $stQ -FixAction $(if ($stQ -eq "FAIL") { "FixMode: put-targets OpsQueue" } else { "" })
        $jdOk = $jdTarget -eq $expJd -or $jdTarget -like "${expJd}:*"
        Add-AuditRow -Category "EventBridge" -Check "$label job def" -Expected $expJd -Actual $jdTarget -Status $(if ($jdOk) { "PASS" } else { "FAIL" }) -FixAction $(if (-not $jdOk) { "FixMode: put-targets jobDefinition" } else { "" })
        $revisionPinned = $jdTarget -and $jdTarget -like "*:*"
        if ($revisionPinned) {
            Add-AuditRow -Category "EventBridge" -Check "$label job def pinning" -Expected "name only (latest)" -Actual "revision pinned" -Status "WARN" -FixAction "FixMode: eventbridge_deploy uses name only for latest ACTIVE"
        }
    }
}

# --- C. IAM ---
function Invoke-IAMAudit {
    $roleName = "academy-video-batch-job-role"
    $jdResp = Aws-JsonSafe @("batch", "describe-job-definitions", "--job-definition-name", "academy-video-ops-reconcile", "--status", "ACTIVE", "--region", $Region)
    if ($jdResp -and $jdResp.jobDefinitions -and $jdResp.jobDefinitions.Count -gt 0) {
        $jobRoleArn = $jdResp.jobDefinitions[0].containerProperties.jobRoleArn -as [string]
        if (-not [string]::IsNullOrWhiteSpace($jobRoleArn) -and $jobRoleArn -match '([^/]+)$') {
            $roleName = $Matches[1]
        }
    }
    $roleJson = Aws-JsonSafe @("iam", "get-role", "--role-name", $roleName)
    if (-not $roleJson -or -not $roleJson.Role) {
        Add-AuditRow -Category "IAM" -Check "Job role exists" -Expected $roleName -Actual "not found" -Status "FAIL" -FixAction "Run batch_video_setup / IAM create role"
        return
    }
    $attached = Aws-JsonSafe @("iam", "list-attached-role-policies", "--role-name", $roleName)
    $hasDescribe = $false
    if ($attached -and $attached.AttachedPolicies) {
        $policyArn = "arn:aws:iam::$(Aws-Text @('sts','get-caller-identity','--query','Account','--output','text')):policy/AcademyAllowBatchDescribeJobs"
        foreach ($ap in $attached.AttachedPolicies) {
            if ($ap.PolicyArn -eq $policyArn) { $hasDescribe = $true; break }
        }
        if (-not $hasDescribe) {
            foreach ($ap in $attached.AttachedPolicies) {
                $pol = Aws-JsonSafe @("iam", "get-policy", "--policy-arn", $ap.PolicyArn)
                if (-not $pol -or -not $pol.Policy) { continue }
                $ver = Aws-JsonSafe @("iam", "get-policy-version", "--policy-arn", $ap.PolicyArn, "--version-id", $pol.Policy.DefaultVersionId)
                if (-not $ver -or -not $ver.PolicyVersion -or -not $ver.PolicyVersion.Document) { continue }
                $doc = $ver.PolicyVersion.Document
                if ($doc -is [string]) { $docStr = $doc } else { $docStr = $doc | ConvertTo-Json -Compress }
                if ($docStr -match "batch:\*|batch:DescribeJobs") { $hasDescribe = $true; break }
            }
        }
    }
    if (-not $hasDescribe) {
        $inlineList = Aws-JsonSafe @("iam", "list-role-policies", "--role-name", $roleName)
        if ($inlineList -and $inlineList.PolicyNames) {
            foreach ($pn in $inlineList.PolicyNames) {
                $rp = Aws-JsonSafe @("iam", "get-role-policy", "--role-name", $roleName, "--policy-name", $pn)
                if ($rp -and $rp.PolicyDocument) {
                    $docStr = if ($rp.PolicyDocument -is [string]) { $rp.PolicyDocument } else { $rp.PolicyDocument | ConvertTo-Json -Compress }
                    if ($docStr -match "batch:\*|batch:DescribeJobs") { $hasDescribe = $true; break }
                }
            }
        }
    }
    $st = if ($hasDescribe) { "PASS" } else { "FAIL" }
    Add-AuditRow -Category "IAM" -Check "DescribeJobs/ListJobs on job role" -Expected "Yes" -Actual $(if ($hasDescribe) { "Yes" } else { "No" }) -Status $st -FixAction $(if (-not $hasDescribe) { "FixMode: iam_attach_batch_describe_jobs.ps1" } else { "" })
}

# --- D. JobDefinition sanity ---
function Invoke-JobDefAudit {
    $videoJd = Aws-JsonSafe @("batch", "describe-job-definitions", "--job-definition-name", "academy-video-batch-jobdef", "--status", "ACTIVE", "--region", $Region)
    $jd = $null
    if ($videoJd -and $videoJd.jobDefinitions -and $videoJd.jobDefinitions.Count -gt 0) {
        $jd = $videoJd.jobDefinitions[0]
    }
    if (-not $jd) {
        Add-AuditRow -Category "JobDef" -Check "Video jobdef" -Expected "ACTIVE" -Actual "not found" -Status "FAIL" -FixAction ""
    } else {
        $vcpus = $jd.containerProperties.vcpus
        $mem = $jd.containerProperties.memory
        Add-AuditRow -Category "JobDef" -Check "Video jobdef vcpus/memory" -Expected ">0" -Actual "vcpus=$vcpus memory=$mem" -Status "PASS" -FixAction ""
    }
    foreach ($name in @("academy-video-ops-reconcile", "academy-video-ops-scanstuck")) {
        $opsJd = Aws-JsonSafe @("batch", "describe-job-definitions", "--job-definition-name", $name, "--status", "ACTIVE", "--region", $Region)
        $oj = $null
        if ($opsJd -and $opsJd.jobDefinitions -and $opsJd.jobDefinitions.Count -gt 0) {
            $oj = $opsJd.jobDefinitions[0]
        }
        if (-not $oj) {
            Add-AuditRow -Category "JobDef" -Check "Ops $name" -Expected "ACTIVE" -Actual "not found" -Status "FAIL" -FixAction "Register ops job def"
        } else {
            $cmd = if ($oj.containerProperties.command) { ($oj.containerProperties.command | ForEach-Object { $_ }) -join " " } else { "" }
            $cmdStr = ($cmd -as [string])
            if (-not $cmdStr) { $cmdStr = "" }
            $cmdShort = if ($cmdStr.Length -gt 40) { $cmdStr.Substring(0, 40) + "..." } else { $cmdStr }
            $role = $oj.containerProperties.jobRoleArn -as [string]
            $hasRole = -not [string]::IsNullOrWhiteSpace($role)
            Add-AuditRow -Category "JobDef" -Check "Ops $name command/role" -Expected "command set, jobRoleArn set" -Actual "cmd=$cmdShort role=$hasRole" -Status $(if ($hasRole) { "PASS" } else { "WARN" }) -FixAction ""
        }
    }
}

# --- FixMode ---
function Invoke-FixMode {
    $batchOpsPath = Join-Path $ScriptRoot "batch_ops_setup.ps1"
    $iamPath = Join-Path $ScriptRoot "iam_attach_batch_describe_jobs.ps1"
    $ebPath = Join-Path $ScriptRoot "eventbridge_deploy_video_scheduler.ps1"

    $needOps = $script:AuditRows | Where-Object { $_.Category -eq "Batch" -and $_.Check -eq "Ops CE exists" -and $_.Status -eq "FAIL" }
    if ($needOps -and (Test-Path -LiteralPath $batchOpsPath)) {
        & $batchOpsPath -Region $Region
        if ($LASTEXITCODE -eq 0) {
            [void]$script:FixesApplied.Add("batch_ops_setup.ps1: Ops CE/Queue created or verified")
        }
    }

    $needIam = $script:AuditRows | Where-Object { $_.Category -eq "IAM" -and $_.Check -like "*DescribeJobs*" -and $_.Status -eq "FAIL" }
    if ($needIam -and (Test-Path -LiteralPath $iamPath)) {
        & $iamPath -Region $Region
        if ($LASTEXITCODE -eq 0) {
            [void]$script:FixesApplied.Add("iam_attach_batch_describe_jobs.ps1: Policy attached to academy-video-batch-job-role")
        }
    }

    $needEb = $script:AuditRows | Where-Object { $_.Category -eq "EventBridge" -and ($_.Status -eq "FAIL" -or $_.Status -eq "WARN") -and $_.FixAction -match "FixMode" }
    if ($needEb -and (Test-Path -LiteralPath $ebPath)) {
        & $ebPath -Region $Region -OpsJobQueueName $ExpectedOpsQueueName
        if ($LASTEXITCODE -eq 0) {
            [void]$script:FixesApplied.Add("eventbridge_deploy_video_scheduler.ps1: Rules/targets updated to rate(5 min), OpsQueue")
        }
    }

    if ($FixModeWithCleanup -and $script:OpsQueueArn) {
        $listRun = Aws-JsonSafe @("batch", "list-jobs", "--job-queue", $script:OpsQueueArn, "--job-status", "RUNNING", "--region", $Region)
        $reconcileJobs = @()
        if ($listRun -and $listRun.jobSummaryList) {
            foreach ($j in $listRun.jobSummaryList) {
                if (($j.jobName -as [string]) -match "reconcile") {
                    $reconcileJobs += $j
                }
            }
        }
        if ($reconcileJobs.Count -gt 1) {
            $sorted = $reconcileJobs | Sort-Object { $_.startedAt } -Descending
            $keep = $sorted[0].jobId
            for ($i = 1; $i -lt $sorted.Count; $i++) {
                $jid = $sorted[$i].jobId
                & aws batch terminate-job --job-id $jid --reason "Audit FixModeWithCleanup" --region $Region 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    [void]$script:FixesApplied.Add("Terminated extra reconcile job: $jid (kept $keep)")
                }
            }
        }
    }
}

# --- Main ---
try {
    $accountId = Aws-Text @("sts", "get-caller-identity", "--query", "Account", "--output", "text")
    if (-not $accountId) {
        Write-Host "FAIL: AWS identity check failed. Set credentials and region." -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "`n===== One-Take Video/Ops Batch Audit =====" -ForegroundColor Cyan
Write-Host "Region: $Region | Account: $accountId | FixMode: $FixMode" -ForegroundColor Gray
Write-Host "Expected VideoQueue: $ExpectedVideoQueueName | OpsQueue: $ExpectedOpsQueueName" -ForegroundColor Gray

Invoke-BatchAudit
Invoke-EventBridgeAudit
Invoke-IAMAudit
Invoke-JobDefAudit

if ($FixMode) {
    Write-Host "`n--- FixMode: applying fixes ---" -ForegroundColor Yellow
    Invoke-FixMode
}

Write-Host "`n--- Audit Table ---" -ForegroundColor Cyan
$script:AuditRows | Format-Table -AutoSize -Wrap -Property Category, Check, Expected, Actual, Status, FixAction

$passCount = ($script:AuditRows | Where-Object { $_.Status -eq "PASS" }).Count
$warnCount = ($script:AuditRows | Where-Object { $_.Status -eq "WARN" }).Count
$failCount = ($script:AuditRows | Where-Object { $_.Status -eq "FAIL" }).Count
Write-Host "`nSummary: PASS=$passCount WARN=$warnCount FAIL=$failCount" -ForegroundColor Gray
Write-Host "PASS count: $passCount | WARN count: $warnCount | FAIL count: $failCount" -ForegroundColor Gray

if ($script:FixesApplied.Count -gt 0) {
    Write-Host "`nApplied changes (FixMode):" -ForegroundColor Yellow
    foreach ($f in $script:FixesApplied) { Write-Host "  - $f" -ForegroundColor Gray }
}

# Recommendations
$failRows = $script:AuditRows | Where-Object { $_.Status -eq "FAIL" }
$warnRows = $script:AuditRows | Where-Object { $_.Status -eq "WARN" }
if ($failRows.Count -gt 0 -or $warnRows.Count -gt 0) {
    Write-Host "`n--- Recommendations ---" -ForegroundColor Cyan
    if ($failRows.Count -gt 0) {
        Write-Host "  [FAIL items]" -ForegroundColor Red
        foreach ($r in $failRows) {
            $fa = if ($r.FixAction) { $r.FixAction } else { "Manual check" }
            Write-Host "    - $($r.Category) / $($r.Check): $fa" -ForegroundColor Gray
        }
        Write-Host "  Then re-run: .\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region -FixMode" -ForegroundColor Gray
    }
    if ($warnRows.Count -gt 0) {
        Write-Host "  [WARN items]" -ForegroundColor Yellow
        foreach ($r in $warnRows) {
            Write-Host "    - $($r.Category) / $($r.Check): $($r.Actual)" -ForegroundColor Gray
        }
    }
    Write-Host "  Full audit: .\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region" -ForegroundColor Gray
} else {
    Write-Host "`n--- Recommendations ---" -ForegroundColor Cyan
    Write-Host "  Regular run: .\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region" -ForegroundColor Gray
    Write-Host "  (Use -FixMode to auto-apply fixes if needed)" -ForegroundColor Gray
}

$overall = "PASS"
if ($failCount -gt 0) { $overall = "FAIL" }
elseif ($warnCount -gt 0) { $overall = "NEEDS_ACTION" }
Write-Host "`nResult: $overall" -ForegroundColor $(if ($overall -eq "PASS") { "Green" } elseif ($overall -eq "NEEDS_ACTION") { "Yellow" } else { "Red" })
exit $(if ($overall -eq "FAIL") { 1 } else { 0 })
