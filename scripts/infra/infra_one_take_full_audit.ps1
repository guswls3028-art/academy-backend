# ==============================================================================
# One-take full production integrity audit: AI Worker (ASG), Messaging Worker (ASG), Video Worker (Batch).
# Production Audit + Fix: Discovery-based Video Batch Reconcile (DescribeJobs, EventBridge, concurrency).
# Usage: .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 [-Verbose] [-FixMode] [-KillExtraReconcile]
# Exit: 0 = PASS, 1 = FAIL (any critical check failed)
#
# Usage example:
#   .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2
#   .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -Verbose
#   .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -FixMode
#   .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -FixMode -KillExtraReconcile
#
# Required IAM permissions (account/region scoped as appropriate):
#   - sts:GetCallerIdentity
#   - ssm:GetParameter (GetParametersByPath optional), ssm:SendCommand, ssm:GetCommandInvocation
#   - autoscaling:DescribeAutoScalingGroups, autoscaling:DescribeScalingActivities, autoscaling:DescribeLaunchConfigurations
#   - ec2:DescribeLaunchTemplates, ec2:DescribeInstances, ec2:DescribeSecurityGroups, ec2:DescribeSubnets, ec2:DescribeVpcs
#   - batch:DescribeComputeEnvironments, batch:DescribeJobQueues, batch:DescribeJobDefinitions, batch:SubmitJob, batch:ListJobs, batch:DescribeJobs, batch:TerminateJob
#   - ecr:DescribeRepositories, ecr:DescribeImages
#   - cloudwatch:DescribeAlarms
#   - logs:GetLogEvents, logs:DescribeLogStreams (for netprobe log fetch)
#   - iam:PassRole (for Batch job role / execution role if submitting netprobe)
#   - iam:ListAttachedRolePolicies, iam:GetPolicy, iam:GetPolicyVersion, iam:ListRolePolicies, iam:GetRolePolicy (Reconcile audit)
#   - iam:CreatePolicy, iam:AttachRolePolicy (Reconcile FixMode only)
#   - iam:SimulatePrincipalPolicy (Reconcile audit optional)
#   - events:DescribeRule, events:ListTargetsByRule, events:PutRule, events:PutTargets (Reconcile audit/FixMode)
# ==============================================================================

param(
    [Parameter(Mandatory = $true)]
    [string]$Region,
    [switch]$FixMode,
    [switch]$KillExtraReconcile
)

$ErrorActionPreference = "Stop"
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)

# Resource names (from repo SSOT)
$AsgAiName = "academy-ai-worker-asg"
$AsgMessagingName = "academy-messaging-worker-asg"
$LtAiName = "academy-ai-worker-lt"
$LtMessagingName = "academy-messaging-worker-lt"
$SsmWorkersEnv = "/academy/workers/env"
$ComputeEnvName = "academy-video-batch-ce"
$ComputeEnvFallback = "academy-video-batch-ce-v3"
$JobQueueName = "academy-video-batch-queue"
$VideoAlarmNames = @(
    "academy-video-DeadJobs",
    "academy-video-UploadFailures",
    "academy-video-FailedJobs",
    "academy-video-BatchJobFailures",
    "academy-video-QueueRunnable"
)
$EcrAi = "academy-ai-worker-cpu"
$EcrMessaging = "academy-messaging-worker"
$EcrVideo = "academy-video-worker"
$ReconcileJobDefName = "academy-video-ops-reconcile"
$ReconcileRuleName = "academy-reconcile-video-jobs"
$ManagedPolicyNameDescribeJobs = "academy-video-batch-job-describe-jobs"
$ManagedPolicyNameDescribeJobsProduction = "AcademyAllowBatchDescribeJobs"

# Resolve JobQueueName / ComputeEnvName from actual_state if present
$batchStatePath = Join-Path $RepoRoot "docs\deploy\actual_state\batch_final_state.json"
if (Test-Path -LiteralPath $batchStatePath) {
    try {
        $batchState = Get-Content $batchStatePath -Raw | ConvertFrom-Json
        if ($batchState.FinalJobQueueName) { $JobQueueName = $batchState.FinalJobQueueName }
        if ($batchState.FinalComputeEnvName) { $ComputeEnvName = $batchState.FinalComputeEnvName }
    } catch {}
}

$global:AuditFailures = @()
$global:OverallPass = $true

function Write-AuditVerbose { param([string]$Message) if ($VerbosePreference -eq 'Continue') { Write-Host $Message -ForegroundColor Gray } }
function Write-Section { param([string]$Title) Write-Host "`n--- $Title ---" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "  [WARN] $Message" -ForegroundColor Yellow }
function Write-Blocker { param([string]$Message) Write-Host "  [BLOCKER] $Message" -ForegroundColor Red }
function Add-Failure { param([string]$Worker, [string]$Area, [string]$Resource, [string]$Message)
    $global:AuditFailures += @{ Worker = $Worker; Area = $Area; Resource = $Resource; Message = $Message }
    $global:OverallPass = $false
}

function ExecJson {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    $str = ($out | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($str)) { return $null }
    try { return ($str | ConvertFrom-Json) } catch { return $null }
}

function ExecJsonThrow {
    param([string[]]$ArgsArray)
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $str = ($out | Out-String).Trim()
    if ($exit -ne 0) {
        if ($str.Length -gt 500) { $str = $str.Substring(0, 497) + "..." }
        throw "AWS CLI failed (exit $exit): $str"
    }
    if ([string]::IsNullOrWhiteSpace($str)) { return $null }
    try { return ($str | ConvertFrom-Json) } catch { throw "AWS CLI output is not valid JSON: $str" }
}

# --- [1] SSM ---
function Test-SsmAudit {
    $aiOk = $true; $msgOk = $true; $videoOk = $true

    # AI / Messaging: same path /academy/workers/env exists and get-parameter succeeds
    $paramOut = ExecJson @("ssm", "get-parameter", "--name", $SsmWorkersEnv, "--region", $Region, "--with-decryption", "--output", "json")
    if (-not $paramOut -or -not $paramOut.Parameter -or $null -eq $paramOut.Parameter.Value) {
        Add-Failure -Worker "AI Worker" -Area "SSM" -Resource $SsmWorkersEnv -Message "Parameter missing or empty"
        $aiOk = $false; $msgOk = $false
    } else {
        Write-AuditVerbose "  AI/Messaging SSM path $SsmWorkersEnv exists."
    }

    # Video: full JSON validity, required keys, DJANGO_SETTINGS_MODULE, API_BASE_URL public warning
    if ($paramOut -and $paramOut.Parameter.Value) {
        $valueStr = $paramOut.Parameter.Value
        $payload = $null
        try { $payload = $valueStr | ConvertFrom-Json } catch {
            try {
                $valueBytes = [Convert]::FromBase64String($valueStr)
                $valueStr = [System.Text.Encoding]::UTF8.GetString($valueBytes)
                $payload = $valueStr | ConvertFrom-Json
            } catch {}
        }
        if (-not $payload -or $payload -isnot [System.Management.Automation.PSCustomObject]) {
            Add-Failure -Worker "Video Worker" -Area "SSM" -Resource $SsmWorkersEnv -Message "Value is not valid JSON (or base64 JSON)"
            $videoOk = $false
        } else {
            $required = @("AWS_DEFAULT_REGION", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
                "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT", "R2_VIDEO_BUCKET",
                "API_BASE_URL", "INTERNAL_WORKER_TOKEN", "REDIS_HOST", "REDIS_PORT", "DJANGO_SETTINGS_MODULE")
            $missing = @()
            foreach ($k in $required) {
                $v = $payload.PSObject.Properties[$k]
                if ($null -eq $v -or $null -eq $v.Value -or ([string]$v.Value).Trim() -eq '') { $missing += $k }
            }
            if ($missing.Count -gt 0) {
                Add-Failure -Worker "Video Worker" -Area "SSM" -Resource $SsmWorkersEnv -Message "Missing or empty keys: $($missing -join ', ')"
                $videoOk = $false
            }
            $dsm = ($payload.PSObject.Properties["DJANGO_SETTINGS_MODULE"].Value -as [string]).Trim()
            if ($dsm -ne "apps.api.config.settings.worker") {
                Add-Failure -Worker "Video Worker" -Area "SSM" -Resource $SsmWorkersEnv -Message "DJANGO_SETTINGS_MODULE must be 'apps.api.config.settings.worker' (got '$dsm')"
                $videoOk = $false
            }
            $apiBase = $payload.API_BASE_URL -as [string]
            if ($apiBase -and $apiBase -match '^https?://([^/:]+)') {
                $hostPart = $Matches[1]
                if ($hostPart -notmatch '^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.)') {
                    Write-Host "  WARN: API_BASE_URL appears to be public (not private IP). Video Batch should use private API URL in VPC." -ForegroundColor Yellow
                }
            }
        }
    }

    return @{ AI = $aiOk; Messaging = $msgOk; Video = $videoOk }
}

# --- [2] Network (ASG: LT, SG, VPC, Subnet; Batch: CE, Queue, JobDefs, SG, API private) ---
function Test-NetworkAudit {
    $aiOk = $true; $msgOk = $true; $videoOk = $true

    # ASG: describe ASGs to get LT, VPC, Subnets, SG
    foreach ($asgName in @($AsgAiName, $AsgMessagingName)) {
        $asgJson = ExecJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $asgName, "--region", $Region, "--output", "json")
        $ag = $asgJson.AutoScalingGroups | Where-Object { $_.AutoScalingGroupName -eq $asgName } | Select-Object -First 1
        if (-not $ag) {
            Add-Failure -Worker $(if ($asgName -eq $AsgAiName) { "AI Worker" } else { "Messaging Worker" }) -Area "Network" -Resource $asgName -Message "ASG not found"
            if ($asgName -eq $AsgAiName) { $aiOk = $false } else { $msgOk = $false }
            continue
        }
        $ltName = $ag.LaunchTemplate.LaunchTemplateName
        if (-not $ltName) {
            Add-Failure -Worker $(if ($asgName -eq $AsgAiName) { "AI Worker" } else { "Messaging Worker" }) -Area "Network" -Resource $asgName -Message "Launch Template not set"
            if ($asgName -eq $AsgAiName) { $aiOk = $false } else { $msgOk = $false }
            continue
        }
        $ltDesc = ExecJson @("ec2", "describe-launch-templates", "--launch-template-names", $ltName, "--region", $Region, "--output", "json")
        $lt = $ltDesc.LaunchTemplates | Where-Object { $_.LaunchTemplateName -eq $ltName } | Select-Object -First 1
        if (-not $lt) {
            Add-Failure -Worker $(if ($asgName -eq $AsgAiName) { "AI Worker" } else { "Messaging Worker" }) -Area "Network" -Resource $ltName -Message "Launch Template not found"
            if ($asgName -eq $AsgAiName) { $aiOk = $false } else { $msgOk = $false }
        } else {
            $ltVer = ExecJson @("ec2", "describe-launch-template-versions", "--launch-template-name", $ltName, "--region", $Region, "--output", "json")
            $ltData = $null
            if ($ltVer -and $ltVer.LaunchTemplateVersions -and $ltVer.LaunchTemplateVersions.Count -gt 0) { $ltData = $ltVer.LaunchTemplateVersions[0].LaunchTemplateData }
            if ($ltData -and $ltData.SecurityGroupIds -and $ltData.SecurityGroupIds.Count -gt 0) {
                Write-AuditVerbose "  ASG $asgName SG: $($ltData.SecurityGroupIds -join ', ')"
            }
        }
        if (-not $ag.VpcZoneIdentifier) {
            Add-Failure -Worker $(if ($asgName -eq $AsgAiName) { "AI Worker" } else { "Messaging Worker" }) -Area "Network" -Resource $asgName -Message "VPC/Subnet (VpcZoneIdentifier) not set"
            if ($asgName -eq $AsgAiName) { $aiOk = $false } else { $msgOk = $false }
        }
        Write-AuditVerbose "  ASG $asgName LT=$ltName VpcZoneIdentifier set."
    }

    # Batch: CE ENABLED & VALID, Queue ENABLED, JobDefs ACTIVE, SG consistency
    $ceList = ExecJson @("batch", "describe-compute-environments", "--region", $Region, "--output", "json")
    $ce = $ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
    if (-not $ce) { $ce = $ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvFallback } | Select-Object -First 1 }
    if (-not $ce) {
        Add-Failure -Worker "Video Worker" -Area "Network" -Resource $ComputeEnvName -Message "Compute environment not found"
        $videoOk = $false
    } else {
        if ($ce.state -ne "ENABLED") {
            Add-Failure -Worker "Video Worker" -Area "Network" -Resource $ce.computeEnvironmentName -Message "CE state=$($ce.state) (expected ENABLED)"
            $videoOk = $false
        }
        if ($ce.status -ne "VALID") {
            Add-Failure -Worker "Video Worker" -Area "Network" -Resource $ce.computeEnvironmentName -Message "CE status=$($ce.status) (expected VALID)"
            $videoOk = $false
        }
        if ($ce.computeResources.securityGroupIds -and $ce.computeResources.securityGroupIds.Count -gt 0) {
            Write-AuditVerbose "  Batch CE SG: $($ce.computeResources.securityGroupIds -join ', ')"
        }
    }

    $jq = ExecJson @("batch", "describe-job-queues", "--job-queues", $JobQueueName, "--region", $Region, "--output", "json")
    $q = $jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName } | Select-Object -First 1
    if (-not $q) {
        Add-Failure -Worker "Video Worker" -Area "Network" -Resource $JobQueueName -Message "Job queue not found"
        $videoOk = $false
    } elseif ($q.state -ne "ENABLED") {
        Add-Failure -Worker "Video Worker" -Area "Network" -Resource $JobQueueName -Message "Queue state=$($q.state) (expected ENABLED)"
        $videoOk = $false
    }

    foreach ($jdName in @("academy-video-batch-jobdef", "academy-video-ops-netprobe")) {
        $jd = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $jdName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
        if (-not $jd.jobDefinitions -or $jd.jobDefinitions.Count -eq 0) {
            Add-Failure -Worker "Video Worker" -Area "Network" -Resource $jdName -Message "Job definition not ACTIVE"
            $videoOk = $false
        }
    }

    Write-AuditVerbose "  Video Batch: CE/Queue/JobDefs checked; API private IP checked via SSM."

    return @{ AI = $aiOk; Messaging = $msgOk; Video = $videoOk }
}

# --- [3] Runtime connectivity ---
function Test-RuntimeAudit {
    $aiOk = $true; $msgOk = $true; $videoOk = $true

    $paramOut = ExecJson @("ssm", "get-parameter", "--name", $SsmWorkersEnv, "--region", $Region, "--with-decryption", "--output", "json")
    $apiBaseUrl = $null
    if ($paramOut -and $paramOut.Parameter.Value) {
        $valueStr = $paramOut.Parameter.Value
        try { $payload = $valueStr | ConvertFrom-Json } catch {
            try { $payload = ([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valueStr))) | ConvertFrom-Json } catch { $payload = $null }
        }
        if ($payload) { $apiBaseUrl = $payload.API_BASE_URL -as [string] }
    }
    if (-not $apiBaseUrl) {
        Write-Host "  WARN: Cannot get API_BASE_URL from SSM; skipping ASG runtime curl." -ForegroundColor Yellow
    }

    # AI Worker: one instance, SSM send-command curl API/health
    $asgAi = ExecJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $AsgAiName, "--region", $Region, "--output", "json")
    $instancesAi = @()
    if ($asgAi -and $asgAi.AutoScalingGroups -and $asgAi.AutoScalingGroups.Count -gt 0) {
        $instancesAi = @($asgAi.AutoScalingGroups[0].Instances | Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" } | ForEach-Object { $_.InstanceId })
    }
    if ($instancesAi.Count -eq 0) {
        Add-Failure -Worker "AI Worker" -Area "Runtime" -Resource $AsgAiName -Message "No InService/Healthy instance for SSM command"
        $aiOk = $false
    } elseif ($apiBaseUrl) {
        $healthUrl = $apiBaseUrl.TrimEnd('/') + "/health"
        $payload = @{
            InstanceIds = @($instancesAi[0])
            DocumentName = "AWS-RunShellScript"
            Parameters = @{
                commands = @("curl -sf --connect-timeout 5 `"$healthUrl`" || echo CURL_FAIL")
            }
        }
        $json = $payload | ConvertTo-Json -Depth 5 -Compress
        $prevErr = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $sendRaw = & aws ssm send-command --cli-input-json $json --region $Region --output json 2>&1
        $sendExit = $LASTEXITCODE
        $ErrorActionPreference = $prevErr
        $sendOut = $null
        if ($sendRaw) { $sendStr = ($sendRaw | Out-String).Trim(); if ($sendStr) { try { $sendOut = $sendStr | ConvertFrom-Json } catch {} } }
        if (-not $sendOut -or -not $sendOut.Command.CommandId) {
            $errMsg = "SSM send-command failed"
            if ($sendExit -ne 0 -and $sendRaw) { $errDetail = ($sendRaw | Out-String).Trim(); if ($errDetail.Length -gt 0 -and $errDetail.Length -lt 500) { $errMsg = $errDetail } elseif ($errDetail.Length -ge 500) { $errMsg = $errDetail.Substring(0, 497) + "..." } }
            Add-Failure -Worker "AI Worker" -Area "Runtime" -Resource $instancesAi[0] -Message $errMsg
            $aiOk = $false
        } else {
            $cmdId = $sendOut.Command.CommandId
            Start-Sleep -Seconds 8
            $invOut = ExecJson @("ssm", "get-command-invocation", "--command-id", $cmdId, "--instance-id", $instancesAi[0], "--region", $Region, "--output", "json")
            if ($invOut.Status -ne "Success" -or ($invOut.StandardOutputContent -and $invOut.StandardOutputContent -match "CURL_FAIL")) {
                Add-Failure -Worker "AI Worker" -Area "Runtime" -Resource $instancesAi[0] -Message "API health check failed (SSM command status=$($invOut.Status))"
                $aiOk = $false
            }
        }
    }

    $asgMsg = ExecJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $AsgMessagingName, "--region", $Region, "--output", "json")
    $instancesMsg = @()
    if ($asgMsg -and $asgMsg.AutoScalingGroups -and $asgMsg.AutoScalingGroups.Count -gt 0) {
        $instancesMsg = @($asgMsg.AutoScalingGroups[0].Instances | Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" } | ForEach-Object { $_.InstanceId })
    }
    if ($instancesMsg.Count -eq 0) {
        Add-Failure -Worker "Messaging Worker" -Area "Runtime" -Resource $AsgMessagingName -Message "No InService/Healthy instance for SSM command"
        $msgOk = $false
    } elseif ($apiBaseUrl) {
        $healthUrl = $apiBaseUrl.TrimEnd('/') + "/health"
        $payload = @{
            InstanceIds = @($instancesMsg[0])
            DocumentName = "AWS-RunShellScript"
            Parameters = @{
                commands = @("curl -sf --connect-timeout 5 `"$healthUrl`" || echo CURL_FAIL")
            }
        }
        $json = $payload | ConvertTo-Json -Depth 5 -Compress
        $prevErr = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $sendRaw = & aws ssm send-command --cli-input-json $json --region $Region --output json 2>&1
        $sendExit = $LASTEXITCODE
        $ErrorActionPreference = $prevErr
        $sendOut = $null
        if ($sendRaw) { $sendStr = ($sendRaw | Out-String).Trim(); if ($sendStr) { try { $sendOut = $sendStr | ConvertFrom-Json } catch {} } }
        if (-not $sendOut -or -not $sendOut.Command.CommandId) {
            $errMsg = "SSM send-command failed"
            if ($sendExit -ne 0 -and $sendRaw) { $errDetail = ($sendRaw | Out-String).Trim(); if ($errDetail.Length -gt 0 -and $errDetail.Length -lt 500) { $errMsg = $errDetail } elseif ($errDetail.Length -ge 500) { $errMsg = $errDetail.Substring(0, 497) + "..." } }
            Add-Failure -Worker "Messaging Worker" -Area "Runtime" -Resource $instancesMsg[0] -Message $errMsg
            $msgOk = $false
        } else {
            Start-Sleep -Seconds 8
            $invOut = ExecJson @("ssm", "get-command-invocation", "--command-id", $sendOut.Command.CommandId, "--instance-id", $instancesMsg[0], "--region", $Region, "--output", "json")
            if ($invOut.Status -ne "Success" -or ($invOut.StandardOutputContent -and $invOut.StandardOutputContent -match "CURL_FAIL")) {
                Add-Failure -Worker "Messaging Worker" -Area "Runtime" -Resource $instancesMsg[0] -Message "API health check failed (SSM command status=$($invOut.Status))"
                $msgOk = $false
            }
        }
    }

    $netprobeScript = Join-Path $ScriptRoot "run_netprobe_job.ps1"
    if (-not (Test-Path -LiteralPath $netprobeScript)) {
        Add-Failure -Worker "Video Worker" -Area "Runtime" -Resource "run_netprobe_job.ps1" -Message "Script not found"
        $videoOk = $false
    } else {
        $prevErr = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $netprobeScript -Region $Region -JobQueueName $JobQueueName -JobDefName "academy-video-ops-netprobe" 2>&1 | Out-Null
        $npExit = $LASTEXITCODE
        $ErrorActionPreference = $prevErr
        if ($npExit -ne 0) {
            Add-Failure -Worker "Video Worker" -Area "Runtime" -Resource "netprobe job" -Message "run_netprobe_job.ps1 did not return SUCCEEDED (exit $npExit)"
            $videoOk = $false
        }
    }

    return @{ AI = $aiOk; Messaging = $msgOk; Video = $videoOk }
}

# --- [4] Image integrity ---
function Test-ImageAudit {
    $aiOk = $true; $msgOk = $true; $videoOk = $true
    $accountId = (aws sts get-caller-identity --query Account --output text 2>$null)
    if (-not $accountId) {
        Add-Failure -Worker "All" -Area "Image" -Resource "sts get-caller-identity" -Message "Cannot get account ID"
        return @{ AI = $false; Messaging = $false; Video = $false }
    }

    foreach ($repo in @($EcrAi, $EcrMessaging, $EcrVideo)) {
        $img = ExecJson @("ecr", "describe-images", "--repository-name", $repo, "--image-ids", "imageTag=latest", "--region", $Region, "--output", "json")
        $details = $img.imageDetails | Where-Object { $_.imageTags -contains "latest" } | Select-Object -First 1
        if (-not $details -or -not $details.imageDigest) {
            $w = switch ($repo) { $EcrAi { "AI Worker" } $EcrMessaging { "Messaging Worker" } default { "Video Worker" } }
            Add-Failure -Worker $w -Area "Image" -Resource "${repo}:latest" -Message "ECR image latest not found or no digest"
            if ($repo -eq $EcrAi) { $aiOk = $false } elseif ($repo -eq $EcrMessaging) { $msgOk = $false } else { $videoOk = $false }
        }
    }

    $jd = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", "academy-video-batch-jobdef", "--status", "ACTIVE", "--region", $Region, "--output", "json")
    if ($jd -and $jd.jobDefinitions -and $jd.jobDefinitions.Count -gt 0) {
        $containerImage = $jd.jobDefinitions[0].containerProperties.image
        if ($containerImage -match '@(sha256:[a-fA-F0-9:]+)$') {
            $jobDigest = $Matches[1]
            $ecrImg = ExecJson @("ecr", "describe-images", "--repository-name", $EcrVideo, "--image-ids", "imageTag=latest", "--region", $Region, "--output", "json")
            if ($ecrImg -and $ecrImg.imageDetails -and $ecrImg.imageDetails.Count -gt 0) {
                $ecrDigest = $ecrImg.imageDetails[0].imageDigest
                if ($ecrDigest -and $jobDigest -ne $ecrDigest) {
                    Write-Host "  WARN: Video Job Def image digest differs from ECR latest. Consider registering new job definition revision." -ForegroundColor Yellow
                }
            }
        }
    }

    return @{ AI = $aiOk; Messaging = $msgOk; Video = $videoOk }
}

# --- [5] AutoScaling state ---
function Test-AsgAudit {
    $aiOk = $true; $msgOk = $true

    foreach ($asgName in @($AsgAiName, $AsgMessagingName)) {
        $asgJson = ExecJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $asgName, "--region", $Region, "--output", "json")
        $ag = $null
        if ($asgJson -and $asgJson.AutoScalingGroups -and $asgJson.AutoScalingGroups.Count -gt 0) { $ag = $asgJson.AutoScalingGroups[0] }
        if (-not $ag) {
            Add-Failure -Worker $(if ($asgName -eq $AsgAiName) { "AI Worker" } else { "Messaging Worker" }) -Area "ASG" -Resource $asgName -Message "ASG not found"
            if ($asgName -eq $AsgAiName) { $aiOk = $false } else { $msgOk = $false }
            continue
        }
        $unhealthy = @($ag.Instances | Where-Object { $_.HealthStatus -ne "Healthy" -and $_.LifecycleState -eq "InService" }).Count
        if ($unhealthy -gt 0) {
            Add-Failure -Worker $(if ($asgName -eq $AsgAiName) { "AI Worker" } else { "Messaging Worker" }) -Area "ASG" -Resource $asgName -Message "Unhealthy InService instance count: $unhealthy"
            if ($asgName -eq $AsgAiName) { $aiOk = $false } else { $msgOk = $false }
        }
        $act = ExecJson @("autoscaling", "describe-scaling-activities", "--auto-scaling-group-name", $asgName, "--region", $Region, "--max-items", "5", "--output", "json")
        $failed = @()
        if ($act -and $act.Activities) { $failed = @($act.Activities | Where-Object { $_.StatusCode -match "Failed" }) }
        if ($failed -and $failed.Count -gt 0) {
            Add-Failure -Worker $(if ($asgName -eq $AsgAiName) { "AI Worker" } else { "Messaging Worker" }) -Area "ASG" -Resource $asgName -Message "Recent scaling activity failure: $($failed[0].StatusCode)"
            if ($asgName -eq $AsgAiName) { $aiOk = $false } else { $msgOk = $false }
        }
    }

    return @{ AI = $aiOk; Messaging = $msgOk; Video = $true }
}

# --- [6] CloudWatch alarms ---
function Test-AlarmsAudit {
    $aiOk = $true; $msgOk = $true; $videoOk = $true

    $cw = ExecJson @("cloudwatch", "describe-alarms", "--alarm-names") + @($VideoAlarmNames) + @("--region", $Region, "--output", "json")
    $found = @(if ($cw.MetricAlarms) { $cw.MetricAlarms | ForEach-Object { $_.AlarmName } } else { @() })
    $missing = $VideoAlarmNames | Where-Object { $_ -notin $found }
    if ($missing.Count -gt 0) {
        Add-Failure -Worker "Video Worker" -Area "CloudWatch" -Resource ($missing -join ", ") -Message "Missing alarms. Run cloudwatch_deploy_video_alarms.ps1"
        $videoOk = $false
    }
    if ($VerbosePreference -eq 'Continue' -and $cw.MetricAlarms) {
        foreach ($a in $cw.MetricAlarms) { Write-AuditVerbose "  Alarm $($a.AlarmName) = $($a.StateValue)" }
    }

    return @{ AI = $aiOk; Messaging = $msgOk; Video = $videoOk }
}

# --- [7] Video Batch Production Audit: Discovery-based Reconcile + JSON report ---
function Add-Check { param([string]$Id, [string]$Level, [string]$Details)
    $script:AuditReport.checks += @{ id = $Id; level = $Level; details = $Details }
}
function Add-FixApplied { param([string]$Action, [string]$Details)
    $script:AuditReport.fixesApplied += @{ action = $Action; details = $Details }
}

function Invoke-VideoBatchProductionAudit {
    $timestamp = Get-Date -Format "yyyyMMddHHmmss"
    $script:AuditReport = @{
        meta = @{
            region = $Region
            accountId = $accountId
            timestamp = $timestamp
            fixMode = [bool]$FixMode
            killExtraReconcile = [bool]$KillExtraReconcile
        }
        discovery = @{}
        checks = @()
        fixesApplied = @()
    }
    $summary = [System.Collections.ArrayList]::new()
    $ok = $true

    try {
        # ---- [A] Discovery ----
        Write-Section "Video Batch Production Audit (Discovery)"
        $qList = ExecJson @("batch", "describe-job-queues", "--region", $Region, "--output", "json")
        $enabledQueues = @()
        if ($qList -and $qList.jobQueues) {
            $enabledQueues = @($qList.jobQueues | Where-Object { $_.state -eq "ENABLED" })
        }
        if ($enabledQueues.Count -eq 0) {
            Add-Check -Id "DISCOVERY.NO_ENABLED_QUEUE" -Level "BLOCKER" -Details "No ENABLED job queue found in region."
            Add-Failure -Worker "Video Worker" -Area "Reconcile" -Resource "Batch" -Message "No ENABLED job queue"
            [void]$summary.Add("  Discovery: No ENABLED job queue.")
            return @{ Ok = $false; Summary = $summary }
        }
        $script:AuditReport.discovery.jobQueues = @($enabledQueues | ForEach-Object { $_.jobQueueName })
        $script:AuditReport.discovery.computeEnvironments = @($enabledQueues[0].computeEnvironmentOrder | ForEach-Object { $_.computeEnvironment })
        Write-Ok "JobQueues (ENABLED): $($script:AuditReport.discovery.jobQueues -join ', ')"

        $rulesOut = ExecJson @("events", "list-rules", "--region", $Region, "--output", "json")
        $rulesWithBatch = @()
        $reconcileRuleName = $null
        $reconcileJobDefInTarget = $null
        if ($rulesOut -and $rulesOut.Rules) {
            foreach ($r in $rulesOut.Rules) {
                $tgt = ExecJson @("events", "list-targets-by-rule", "--rule", $r.Name, "--region", $Region, "--output", "json")
                if (-not $tgt -or -not $tgt.Targets) { continue }
                foreach ($t in $tgt.Targets) {
                    if ($t.BatchParameters -and $t.BatchParameters.JobDefinition) {
                        $rulesWithBatch += @{ RuleName = $r.Name; JobDefinition = $t.BatchParameters.JobDefinition; TargetId = $t.Id; Arn = $t.Arn; RoleArn = $t.RoleArn }
                        if (($t.BatchParameters.JobDefinition -as [string]) -match 'reconcile') {
                            $reconcileRuleName = $r.Name
                            $reconcileJobDefInTarget = $t.BatchParameters.JobDefinition -as [string]
                        }
                    }
                }
            }
        }
        $script:AuditReport.discovery.eventBridgeRulesWithBatch = @($rulesWithBatch | ForEach-Object { $_.RuleName } | Select-Object -Unique)
        if (-not $reconcileRuleName) {
            Add-Check -Id "DISCOVERY.NO_RECONCILE_RULE" -Level "BLOCKER" -Details "No EventBridge rule with Batch target for reconcile job definition found."
            Add-Failure -Worker "Video Worker" -Area "Reconcile" -Resource "EventBridge" -Message "Reconcile rule not found (no Batch target with 'reconcile' in JobDefinition)"
            [void]$summary.Add("  Discovery: Reconcile EventBridge rule not found.")
            return @{ Ok = $false; Summary = $summary }
        }
        $script:AuditReport.discovery.reconcileRuleName = $reconcileRuleName
        $script:AuditReport.discovery.reconcileJobDefinitionInTarget = $reconcileJobDefInTarget
        Write-Ok "Reconcile rule: $reconcileRuleName (JobDefinition in target: $reconcileJobDefInTarget)"

        $jdNameBase = $reconcileJobDefInTarget -replace ':\d+$', ''
        $jdList = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $jdNameBase, "--status", "ACTIVE", "--region", $Region, "--output", "json")
        $reconcileJd = $null
        if ($jdList -and $jdList.jobDefinitions -and $jdList.jobDefinitions.Count -gt 0) {
            $reconcileJd = $jdList.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
        }
        if (-not $reconcileJd) {
            Add-Check -Id "DISCOVERY.NO_RECONCILE_JOB_DEF" -Level "BLOCKER" -Details "Job definition '$jdNameBase' not ACTIVE."
            Add-Failure -Worker "Video Worker" -Area "Reconcile" -Resource $jdNameBase -Message "Reconcile job definition not ACTIVE"
            [void]$summary.Add("  Discovery: Reconcile job definition not ACTIVE.")
            return @{ Ok = $false; Summary = $summary }
        }
        $jobRoleArn = $reconcileJd.containerProperties.jobRoleArn
        $jobRoleName = $null
        if ($jobRoleArn -match '/role/([^/]+)$') { $jobRoleName = $Matches[1] }
        if (-not $jobRoleName) {
            Add-Check -Id "DISCOVERY.NO_JOB_ROLE" -Level "BLOCKER" -Details "Reconcile job definition has no jobRoleArn."
            [void]$summary.Add("  Discovery: jobRoleArn not set.")
            return @{ Ok = $false; Summary = $summary }
        }
        $script:AuditReport.discovery.reconcileJobDefinitionArn = $reconcileJd.jobDefinitionArn
        $script:AuditReport.discovery.jobRoleArn = $jobRoleArn
        $script:AuditReport.discovery.jobRoleName = $jobRoleName
        [void]$summary.Add("  Reconcile jobRoleArn: $jobRoleArn")

        $runningReconcileJobs = @()
        foreach ($q in $enabledQueues) {
            $listOut = ExecJson @("batch", "list-jobs", "--job-queue", $q.jobQueueArn, "--job-status", "RUNNING", "--region", $Region, "--output", "json")
            if (-not $listOut -or -not $listOut.jobSummaryList) { continue }
            $ids = @($listOut.jobSummaryList | ForEach-Object { $_.jobId })
            if ($ids.Count -eq 0) { continue }
            $desc = ExecJson @("batch", "describe-jobs", "--jobs", $ids, "--region", $Region, "--output", "json")
            if ($desc -and $desc.jobs) {
                foreach ($j in $desc.jobs) {
                    if (($j.jobDefinition -as [string]) -match 'reconcile') {
                        $runningReconcileJobs += @{ jobId = $j.jobId; jobQueue = $q.jobQueueName; startedAt = $j.startedAt }
                    }
                }
            }
        }
        $script:AuditReport.discovery.runningReconcileJobCount = $runningReconcileJobs.Count
        $script:AuditReport.discovery.runningReconcileJobs = @($runningReconcileJobs | ForEach-Object { $_.jobId })
        [void]$summary.Add("  RUNNING reconcile jobs: $($runningReconcileJobs.Count)")

        # ---- [B] IAM DescribeJobs ----
        Write-Section "IAM (batch:DescribeJobs)"
        $hasDescribeJobs = $false
        $attached = ExecJson @("iam", "list-attached-role-policies", "--role-name", $jobRoleName, "--output", "json")
        if ($attached -and $attached.AttachedPolicies) {
            foreach ($ap in $attached.AttachedPolicies) {
                $policyOut = ExecJson @("iam", "get-policy", "--policy-arn", $ap.PolicyArn, "--output", "json")
                if (-not $policyOut -or -not $policyOut.Policy) { continue }
                $verOut = ExecJson @("iam", "get-policy-version", "--policy-arn", $ap.PolicyArn, "--version-id", $policyOut.Policy.DefaultVersionId, "--output", "json")
                if ($verOut -and $verOut.PolicyVersion.Document) {
                    $docStr = if ($verOut.PolicyVersion.Document -is [string]) { $verOut.PolicyVersion.Document } else { $verOut.PolicyVersion.Document | ConvertTo-Json -Compress }
                    if ($docStr -match 'batch:\*|batch:DescribeJobs') { $hasDescribeJobs = $true; break }
                }
            }
        }
        if (-not $hasDescribeJobs) {
            $inlineList = ExecJson @("iam", "list-role-policies", "--role-name", $jobRoleName, "--output", "json")
            if ($inlineList -and $inlineList.PolicyNames) {
                foreach ($pn in $inlineList.PolicyNames) {
                    $rpOut = ExecJson @("iam", "get-role-policy", "--role-name", $jobRoleName, "--policy-name", $pn, "--output", "json")
                    if ($rpOut -and $rpOut.PolicyDocument) {
                        $docStr = if ($rpOut.PolicyDocument -is [string]) { $rpOut.PolicyDocument } else { $rpOut.PolicyDocument | ConvertTo-Json -Compress }
                        if ($docStr -match 'batch:\*|batch:DescribeJobs') { $hasDescribeJobs = $true; break }
                    }
                }
            }
        }
        if (-not $hasDescribeJobs) {
            Add-Check -Id "IAM.DESCRIBEJOBS" -Level "BLOCKER" -Details "Role $jobRoleName has no batch:DescribeJobs (or batch:*) in attached or inline policies. Reconcile will get AccessDenied and mis-detect RUNNING jobs."
            Write-Blocker "Job role $jobRoleName missing batch:DescribeJobs"
            Add-Failure -Worker "Video Worker" -Area "Reconcile" -Resource $jobRoleName -Message "Job role missing batch:DescribeJobs"
            $ok = $false
            [void]$summary.Add("  Job role batch:DescribeJobs: MISSING")

            if ($FixMode) {
                $policyName = $ManagedPolicyNameDescribeJobsProduction
                $policyArn = "arn:aws:iam::${accountId}:policy/$policyName"
                $policyExists = ExecJson @("iam", "get-policy", "--policy-arn", $policyArn, "--output", "json")
                if ($policyExists -and $policyExists.Policy) {
                    $attachList = ExecJson @("iam", "list-attached-role-policies", "--role-name", $jobRoleName, "--output", "json")
                    $alreadyAttached = $false
                    if ($attachList -and $attachList.AttachedPolicies) {
                        foreach ($a in $attachList.AttachedPolicies) { if ($a.PolicyArn -eq $policyArn) { $alreadyAttached = $true; break } }
                    }
                    if (-not $alreadyAttached) {
                        try {
                            ExecJsonThrow @("iam", "attach-role-policy", "--role-name", $jobRoleName, "--policy-arn", $policyArn)
                            Add-FixApplied -Action "IAM.AttachRolePolicy" -Details "Attached $policyName to $jobRoleName"
                            Write-Ok "Attached existing managed policy $policyName to $jobRoleName"
                            $ok = $true
                        } catch {
                            Add-Check -Id "FIX.IAM_ATTACH" -Level "BLOCKER" -Details $_.Exception.Message
                            Write-Blocker "Attach failed: $($_.Exception.Message)"
                        }
                    } else {
                        Add-FixApplied -Action "IAM.AttachRolePolicy" -Details "Policy $policyName already attached (no change)"
                        Write-Ok "Policy already attached (no change)"
                        $ok = $true
                    }
                } else {
                    $policyDoc = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["batch:DescribeJobs","batch:ListJobs"],"Resource":"*"}]}'
                    $tempFile = Join-Path ([System.IO.Path]::GetTempPath()) "AcademyAllowBatchDescribeJobs-$(Get-Date -Format 'yyyyMMddHHmmss').json"
                    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
                    [System.IO.File]::WriteAllText($tempFile, $policyDoc, $utf8NoBom)
                    try {
                        $createRaw = & aws iam create-policy --policy-name $policyName --policy-document "file://$($tempFile -replace '\\','/')" --description "Allows Batch job role DescribeJobs/ListJobs for reconcile" --output json 2>&1
                        if ($LASTEXITCODE -eq 0 -and $createRaw) {
                            $createJson = $createRaw | ConvertFrom-Json
                            $newArn = $createJson.Policy.Arn
                            ExecJsonThrow @("iam", "attach-role-policy", "--role-name", $jobRoleName, "--policy-arn", $newArn)
                            Add-FixApplied -Action "IAM.CreatePolicyAndAttach" -Details "Created $policyName and attached to $jobRoleName"
                            Write-Ok "Created managed policy $policyName and attached to $jobRoleName"
                            $ok = $true
                        } elseif (($createRaw | Out-String) -match "EntityAlreadyExists") {
                            ExecJsonThrow @("iam", "attach-role-policy", "--role-name", $jobRoleName, "--policy-arn", $policyArn)
                            Add-FixApplied -Action "IAM.AttachRolePolicy" -Details "Attached existing $policyName to $jobRoleName"
                            Write-Ok "Attached existing $policyName to $jobRoleName"
                            $ok = $true
                        } else {
                            Write-Blocker "Create policy failed: $createRaw"
                            $ok = $false
                        }
                    } finally {
                        if (Test-Path -LiteralPath $tempFile) { Remove-Item $tempFile -Force -ErrorAction SilentlyContinue }
                    }
                }
            }
        } else {
            Add-Check -Id "IAM.DESCRIBEJOBS" -Level "OK" -Details "Role $jobRoleName has batch:DescribeJobs (or batch:*)"
            Write-Ok "Job role has batch:DescribeJobs"
            [void]$summary.Add("  Job role batch:DescribeJobs: OK")
        }

        # ---- [C] EventBridge revision pinning ----
        Write-Section "EventBridge Revision Pinning"
        $ruleOut = ExecJson @("events", "describe-rule", "--name", $reconcileRuleName, "--region", $Region, "--output", "json")
        $scheduleExpr = $null
        if ($ruleOut) { $scheduleExpr = $ruleOut.ScheduleExpression -as [string] }
        $script:AuditReport.discovery.scheduleExpression = $scheduleExpr
        $tgtOut = ExecJson @("events", "list-targets-by-rule", "--rule", $reconcileRuleName, "--region", $Region, "--output", "json")
        $jdInTarget = $null
        if ($tgtOut -and $tgtOut.Targets -and $tgtOut.Targets.Count -gt 0) {
            $jdInTarget = $tgtOut.Targets[0].BatchParameters.JobDefinition -as [string]
        }
        $revisionPinned = $jdInTarget -and $jdInTarget -match ':\d+$'
        if ($revisionPinned) {
            Add-Check -Id "EB.REVISION_PIN" -Level "WARN" -Details "EventBridge target uses fixed revision: $jdInTarget. New job definition revisions will not be used until target is updated."
            Write-Warn "Target JobDefinition is pinned to revision: $jdInTarget"
            $ok = $false
            [void]$summary.Add("  EventBridge JobDefinition: $jdInTarget (fixed revision - WARN)")

            if ($FixMode) {
                $latestRev = $reconcileJd.revision
                $jobDefNameOnly = $jdNameBase
                $newJobDefValue = "${jobDefNameOnly}:$latestRev"
                $targets = @($tgtOut.Targets)
                $updated = $false
                for ($i = 0; $i -lt $targets.Count; $i++) {
                    if ($targets[$i].BatchParameters -and $targets[$i].BatchParameters.JobDefinition -eq $jdInTarget) {
                        $targets[$i].BatchParameters.JobDefinition = $newJobDefValue
                        $updated = $true
                        break
                    }
                }
                if ($updated) {
                    $putTargetsJson = @{ Rule = $reconcileRuleName; Targets = @($targets) } | ConvertTo-Json -Depth 10 -Compress
                    $putFile = Join-Path ([System.IO.Path]::GetTempPath()) "eventbridge_put_targets_$(Get-Date -Format 'yyyyMMddHHmmss').json"
                    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
                    [System.IO.File]::WriteAllText($putFile, $putTargetsJson, $utf8NoBom)
                    try {
                        ExecJsonThrow @("events", "put-targets", "--rule", $reconcileRuleName, "--targets", "file://$($putFile -replace '\\','/')", "--region", $Region)
                        Add-FixApplied -Action "EB.UpdateTargetRevision" -Details "Updated target JobDefinition from $jdInTarget to $newJobDefValue (latest ACTIVE revision)"
                        Write-Ok "Updated target to latest revision: $newJobDefValue"
                        $ok = $true
                    } catch {
                        Write-Blocker "put-targets failed: $($_.Exception.Message)"
                    } finally {
                        if (Test-Path -LiteralPath $putFile) { Remove-Item $putFile -Force -ErrorAction SilentlyContinue }
                    }
                }
            }
        } else {
            Add-Check -Id "EB.REVISION_PIN" -Level "OK" -Details "Target uses job definition name only (not pinned to revision): $jdInTarget"
            Write-Ok "Target not pinned to revision: $jdInTarget"
            [void]$summary.Add("  EventBridge JobDefinition: $jdInTarget (name only)")
        }
        [void]$summary.Add("  EventBridge schedule: $scheduleExpr")

        # ---- [D] Reconcile concurrency ----
        Write-Section "Reconcile Concurrency"
        if ($runningReconcileJobs.Count -gt 1) {
            Add-Check -Id "RECONCILE.CONCURRENT" -Level "WARN" -Details "RUNNING reconcile job count is $($runningReconcileJobs.Count). More than one can cause duplicate status updates. JobIds: $($runningReconcileJobs -join ', ')"
            Write-Warn "RUNNING reconcile jobs: $($runningReconcileJobs.Count)"
            $ok = $false
            [void]$summary.Add("  RUNNING reconcile count: $($runningReconcileJobs.Count) (WARN)")

            if ($FixMode -and $KillExtraReconcile) {
                $sorted = @($runningReconcileJobs | Sort-Object { $_.startedAt } -Descending)
                $keepId = $sorted[0].jobId
                $toTerminate = @($sorted[1..($sorted.Count - 1)] | ForEach-Object { $_.jobId })
                foreach ($jid in $toTerminate) {
                    try {
                        ExecJsonThrow @("batch", "terminate-job", "--job-id", $jid, "--reason", "Audit FixMode KillExtraReconcile", "--region", $Region)
                        Add-FixApplied -Action "Batch.TerminateJob" -Details "Terminated reconcile job $jid (kept $keepId)"
                        Write-Ok "Terminated extra reconcile job: $jid"
                    } catch {
                        Write-Blocker "Terminate job $jid failed: $($_.Exception.Message)"
                    }
                }
                $ok = $true
            }
        } else {
            Add-Check -Id "RECONCILE.CONCURRENT" -Level "OK" -Details "RUNNING reconcile job count: $($runningReconcileJobs.Count)"
            Write-Ok "RUNNING reconcile jobs: $($runningReconcileJobs.Count)"
        }
        if ($scheduleExpr -match 'rate\s*\(\s*(\d+)\s*minute') {
            $mins = [int]$Matches[1]
            if ($mins -lt 5) {
                Add-Check -Id "RECONCILE.SCHEDULE_RATE" -Level "WARN" -Details "Schedule is rate($mins minutes). Consider rate(5 minutes) to reduce overlap."
                Write-Warn "Schedule rate($mins minutes) may cause overlap; consider rate(5 minutes)"
            }
            if ($FixMode -and $mins -lt 5) {
                $newSchedule = "rate(5 minutes)"
                try {
                    ExecJsonThrow @("events", "put-rule", "--name", $reconcileRuleName, "--schedule-expression", $newSchedule, "--state", "ENABLED", "--region", $Region)
                    Add-FixApplied -Action "EB.ScheduleRelax" -Details "Updated schedule from $scheduleExpr to $newSchedule"
                    Write-Ok "Updated schedule to $newSchedule"
                } catch {
                    Write-Blocker "PutRule failed: $($_.Exception.Message)"
                }
            }
        }

        # ---- [E] Batch state ----
        Write-Section "Batch State"
        $runningCount = 0; $runnableCount = 0; $submittedCount = 0
        $videoJobNames = 0; $reconcileJobNames = 0
        foreach ($q in $enabledQueues) {
            foreach ($status in @("RUNNING", "RUNNABLE", "SUBMITTED")) {
                $listOut = ExecJson @("batch", "list-jobs", "--job-queue", $q.jobQueueArn, "--job-status", $status, "--region", $Region, "--output", "json")
                $jobIds = @(if ($listOut -and $listOut.jobSummaryList) { $listOut.jobSummaryList | ForEach-Object { $_.jobId } } else { @() })
                if ($status -eq "RUNNING") { $runningCount += $jobIds.Count }
                elseif ($status -eq "RUNNABLE") { $runnableCount += $jobIds.Count }
                else { $submittedCount += $jobIds.Count }
                if ($jobIds.Count -gt 0 -and $jobIds.Count -le 100) {
                    $desc = ExecJson @("batch", "describe-jobs", "--jobs", $jobIds, "--region", $Region, "--output", "json")
                    if ($desc -and $desc.jobs) {
                        foreach ($j in $desc.jobs) {
                            if (($j.jobDefinition -as [string]) -match 'reconcile') { $reconcileJobNames++ } else { $videoJobNames++ }
                        }
                    }
                }
            }
        }
        $script:AuditReport.discovery.batchState = @{
            RUNNING = $runningCount
            RUNNABLE = $runnableCount
            SUBMITTED = $submittedCount
            videoJobCount = $videoJobNames
            reconcileJobCount = $reconcileJobNames
        }
        Add-Check -Id "BATCH.STATE" -Level "INFO" -Details "RUNNING=$runningCount RUNNABLE=$runnableCount SUBMITTED=$submittedCount (video*: $videoJobNames reconcile*: $reconcileJobNames)"
        Write-Ok "RUNNING=$runningCount RUNNABLE=$runnableCount SUBMITTED=$submittedCount (video: $videoJobNames reconcile: $reconcileJobNames)"
        [void]$summary.Add("  Batch: RUNNING=$runningCount RUNNABLE=$runnableCount SUBMITTED=$submittedCount")
    } catch {
        Add-Check -Id "AUDIT.ERROR" -Level "BLOCKER" -Details $_.Exception.Message
        Write-Blocker "Audit error: $($_.Exception.Message)"
        $ok = $false
    }

    return @{ Ok = $ok; Summary = $summary }
}

function Write-AuditReportJson {
    $reportDir = Join-Path $RepoRoot "docs\deploy\audit_reports"
    if (-not (Test-Path -LiteralPath $reportDir)) {
        New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
    }
    $ts = $script:AuditReport.meta.timestamp
    $path = Join-Path $reportDir "infra_audit_$ts.json"
    $json = $script:AuditReport | ConvertTo-Json -Depth 10
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($path, $json, $utf8NoBom)
    Write-Host "`n  Report: $path" -ForegroundColor Gray
    return $path
}

# --- Main ---
$accountId = $null
try {
    $accountId = (aws sts get-caller-identity --query Account --output text 2>&1)
    if (-not $accountId -or $accountId -match "error|Exception") {
        Write-Host "FAIL: sts get-caller-identity failed. Set AWS credentials." -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "FAIL: sts get-caller-identity failed. Set AWS credentials." -ForegroundColor Red
    exit 1
}

Write-Host "`n===== FULL WORKER INFRA AUDIT =====" -ForegroundColor Cyan
Write-Host "Region: $Region | Account: $accountId" -ForegroundColor Gray

$r1 = Test-SsmAudit
$r2 = Test-NetworkAudit
$r3 = Test-RuntimeAudit
$r4 = Test-ImageAudit
$r5 = Test-AsgAudit
$r6 = Test-AlarmsAudit
$r7 = Test-VideoBatchReconcileAudit

function Status { param($ok) if ($ok) { Write-Host "OK" -ForegroundColor Green } else { Write-Host "FAIL" -ForegroundColor Red } }

Write-Host "`nAI Worker:" -ForegroundColor Cyan
Write-Host "  SSM: " -NoNewline; Status $r1.AI
Write-Host "  Network: " -NoNewline; Status $r2.AI
Write-Host "  Runtime: " -NoNewline; Status $r3.AI
Write-Host "  ASG: " -NoNewline; Status $r5.AI
Write-Host "  Image: " -NoNewline; Status $r4.AI

Write-Host "`nMessaging Worker:" -ForegroundColor Cyan
Write-Host "  SSM: " -NoNewline; Status $r1.Messaging
Write-Host "  Network: " -NoNewline; Status $r2.Messaging
Write-Host "  Runtime: " -NoNewline; Status $r3.Messaging
Write-Host "  ASG: " -NoNewline; Status $r5.Messaging
Write-Host "  Image: " -NoNewline; Status $r4.Messaging

Write-Host "`nVideo Worker:" -ForegroundColor Cyan
Write-Host "  SSM: " -NoNewline; Status $r1.Video
Write-Host "  Network: " -NoNewline; Status $r2.Video
Write-Host "  Runtime: " -NoNewline; Status $r3.Video
Write-Host "  Batch: " -NoNewline; Status $r2.Video
Write-Host "  Image: " -NoNewline; Status $r4.Video
Write-Host "  Reconcile (DescribeJobs/EventBridge): " -NoNewline; Status $r7.Ok
if ($r7.Summary -and $r7.Summary.Count -gt 0) {
    Write-Host "`n  Video Batch Reconcile summary:" -ForegroundColor Gray
    foreach ($line in $r7.Summary) { Write-Host $line -ForegroundColor Gray }
}

if ($global:OverallPass) {
    Write-Host "`nOVERALL STATUS: PASS" -ForegroundColor Green
} else {
    Write-Host "`nOVERALL STATUS: FAIL" -ForegroundColor Red
    Write-Host "`nFailures (Worker | Area | Resource | Message):" -ForegroundColor Yellow
    foreach ($f in $global:AuditFailures) {
        Write-Host "  $($f.Worker) | $($f.Area) | $($f.Resource) | $($f.Message)" -ForegroundColor Gray
    }
    if ($FixMode) {
        Write-Host "`nFixMode: no automatic fix implemented for reported failures. Resolve manually." -ForegroundColor Yellow
    }
    exit 1
}

Write-Host "`n--- Usage ---" -ForegroundColor Gray
Write-Host "  .\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 [-Verbose] [-FixMode]" -ForegroundColor Gray
Write-Host "`n--- Required permissions ---" -ForegroundColor Gray
Write-Host "  sts:GetCallerIdentity; ssm:GetParameter, SendCommand, GetCommandInvocation; autoscaling:Describe*; ec2:Describe*; batch:Describe*, SubmitJob, ListJobs; ecr:Describe*; cloudwatch:DescribeAlarms; logs:GetLogEvents; iam:PassRole (Batch)." -ForegroundColor Gray
exit 0
