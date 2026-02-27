# ==============================================================================
# AWS Batch Video Encoding Setup (idempotent)
# SSOT: full_redeploy.ps1, deploy_worker_asg.ps1 변수 사용
# Usage: .\scripts\infra\batch_video_setup.ps1 -Region ap-northeast-2 -VpcId vpc-xxx -SubnetIds @("subnet-1","subnet-2") -SecurityGroupId sg-xxx -EcrRepoUri 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$AccountId = "",
    [Parameter(Mandatory=$true)][string]$VpcId,
    [Parameter(Mandatory=$true)][string[]]$SubnetIds,
    [Parameter(Mandatory=$true)][string]$SecurityGroupId,
    [Parameter(Mandatory=$true)][string]$EcrRepoUri,
    [int]$MaxVcpus = 32,
    [string]$InstanceType = "c6g.large",
    [string]$ComputeEnvName = "academy-video-batch-ce",
    [string]$JobQueueName = "academy-video-batch-queue",
    [string]$JobDefName = "academy-video-batch-jobdef",
    [string]$LogsGroup = "/aws/batch/academy-video-worker"
)
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"

# EcrRepoUri 엄격 검증: placeholder(<acct> 등) 및 잘못된 형식이 JobDefinition에 등록되지 않도록
$EcrRepoUri = $EcrRepoUri.Trim()
if ($EcrRepoUri -match '[<>]') {
    Write-Host "Invalid ECR URI. Example: 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/repo:tag" -ForegroundColor Red
    exit 1
}
if ($EcrRepoUri -match '\s') {
    Write-Host "Invalid ECR URI. Example: 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/repo:tag" -ForegroundColor Red
    exit 1
}
if ($EcrRepoUri -notmatch '^\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9\-_]+:[a-zA-Z0-9\.\-_]+$') {
    Write-Host "Invalid ECR URI. Example: 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/repo:tag" -ForegroundColor Red
    exit 1
}

function Get-ComputeEnvironmentArn {
    param([string]$Name)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = aws batch describe-compute-environments --compute-environments $Name --region $Region --output json 2>&1
    $err = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($err -ne 0) { return $null }
    $o = $null
    try { $o = $out | ConvertFrom-Json } catch { return $null }
    $ce = $o.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $Name } | Select-Object -First 1
    if (-not $ce) { return $null }
    return $ce.computeEnvironmentArn
}

function Get-JobQueueArn {
    param([string]$Name)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = aws batch describe-job-queues --job-queues $Name --region $Region --output json 2>&1
    $err = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($err -ne 0) { return $null }
    $o = $null
    try { $o = $out | ConvertFrom-Json } catch { return $null }
    $q = $o.jobQueues | Where-Object { $_.jobQueueName -eq $Name } | Select-Object -First 1
    if (-not $q) { return $null }
    return $q.jobQueueArn
}

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

if (-not $AccountId) {
    $AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
    if ($LASTEXITCODE -ne 0) { Write-Host "AWS identity check failed" -ForegroundColor Red; exit 1 }
}

Write-Host "== Video Batch Setup ==" -ForegroundColor Cyan
Write-Host "Region=$Region AccountId=$AccountId VpcId=$VpcId" -ForegroundColor Gray

# 0) Preconditions
Write-Host "`n[0] Preconditions" -ForegroundColor Cyan
aws --version | Out-Host
aws sts get-caller-identity --region $Region | Out-Host

# 1) CloudWatch Log Group
Write-Host "`n[1] Ensure Log Group: $LogsGroup" -ForegroundColor Cyan
$existingLg = ExecJson "aws logs describe-log-groups --log-group-name-prefix `"$LogsGroup`" --region $Region --output json 2>&1"
if (-not ($existingLg.logGroups | Where-Object { $_.logGroupName -eq $LogsGroup })) {
    aws logs create-log-group --log-group-name $LogsGroup --region $Region
} else {
    Write-Host "  Log group exists" -ForegroundColor Gray
}
$OpsLogsGroup = "/aws/batch/academy-video-ops"
if (-not (ExecJson "aws logs describe-log-groups --log-group-name-prefix `"$OpsLogsGroup`" --region $Region --output json 2>&1" | ForEach-Object { $_.logGroups } | Where-Object { $_.logGroupName -eq $OpsLogsGroup })) {
    aws logs create-log-group --log-group-name $OpsLogsGroup --region $Region
}

# 2) IAM Roles
Write-Host "`n[2] Ensure IAM Roles" -ForegroundColor Cyan
$BatchServiceRoleName = "academy-batch-service-role"
$EcsInstanceRoleName = "academy-batch-ecs-instance-role"
$InstanceProfileName = "academy-batch-ecs-instance-profile"
$JobRoleName = "academy-video-batch-job-role"
$ExecutionRoleName = "academy-batch-ecs-task-execution-role"

$trustBatch = Join-Path $InfraPath "iam\trust_batch_service.json"
$trustEc2 = Join-Path $InfraPath "iam\trust_ec2.json"
$trustEcsTasks = Join-Path $InfraPath "iam\trust_ecs_tasks.json"
$policyJob = Join-Path $InfraPath "iam\policy_video_job_role.json"
$policyBatchService = Join-Path $InfraPath "iam\policy_batch_service_role.json"
$policyEcsExecution = Join-Path $InfraPath "iam\policy_ecs_task_execution_role.json"

# Batch service role
$role = $null
try { $role = ExecJson "aws iam get-role --role-name $BatchServiceRoleName --output json 2>&1" } catch {}
if (-not $role) {
    Write-Host "  Creating $BatchServiceRoleName" -ForegroundColor Yellow
    aws iam create-role --role-name $BatchServiceRoleName --assume-role-policy-document "file://$($trustBatch -replace '\\','/')" | Out-Null
}
aws iam attach-role-policy --role-name $BatchServiceRoleName --policy-arn "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole" 2>$null | Out-Null
if (Test-Path $policyBatchService) {
    aws iam put-role-policy --role-name $BatchServiceRoleName --policy-name "academy-batch-service-inline" --policy-document "file://$($policyBatchService -replace '\\','/')" | Out-Null
}

# ECS instance role
$role = $null
try { $role = ExecJson "aws iam get-role --role-name $EcsInstanceRoleName --output json 2>&1" } catch {}
if (-not $role) {
    Write-Host "  Creating $EcsInstanceRoleName" -ForegroundColor Yellow
    aws iam create-role --role-name $EcsInstanceRoleName --assume-role-policy-document "file://$($trustEc2 -replace '\\','/')" | Out-Null
}
aws iam attach-role-policy --role-name $EcsInstanceRoleName --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role" 2>$null | Out-Null

# Instance profile
$ip = $null
try { $ip = ExecJson "aws iam get-instance-profile --instance-profile-name $InstanceProfileName --output json 2>&1" } catch {}
if (-not $ip) {
    aws iam create-instance-profile --instance-profile-name $InstanceProfileName | Out-Null
    aws iam add-role-to-instance-profile --instance-profile-name $InstanceProfileName --role-name $EcsInstanceRoleName | Out-Null
} else {
    $hasRole = $ip.InstanceProfile.Roles | Where-Object { $_.RoleName -eq $EcsInstanceRoleName }
    if (-not $hasRole) { aws iam add-role-to-instance-profile --instance-profile-name $InstanceProfileName --role-name $EcsInstanceRoleName | Out-Null }
}

# ECS Task Execution role (pull image, logs)
$role = $null
try { $role = ExecJson "aws iam get-role --role-name $ExecutionRoleName --output json 2>&1" } catch {}
if (-not $role) {
    Write-Host "  Creating $ExecutionRoleName" -ForegroundColor Yellow
    aws iam create-role --role-name $ExecutionRoleName --assume-role-policy-document "file://$($trustEcsTasks -replace '\\','/')" | Out-Null
}
aws iam attach-role-policy --role-name $ExecutionRoleName --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" 2>$null | Out-Null
if (Test-Path $policyEcsExecution) {
    aws iam put-role-policy --role-name $ExecutionRoleName --policy-name "academy-batch-execution-inline" --policy-document "file://$($policyEcsExecution -replace '\\','/')" | Out-Null
}

# Job role (DB/SSM/ECR/Logs)
$role = $null
try { $role = ExecJson "aws iam get-role --role-name $JobRoleName --output json 2>&1" } catch {}
if (-not $role) {
    Write-Host "  Creating $JobRoleName" -ForegroundColor Yellow
    aws iam create-role --role-name $JobRoleName --assume-role-policy-document "file://$($trustEcsTasks -replace '\\','/')" | Out-Null
}
aws iam put-role-policy --role-name $JobRoleName --policy-name "academy-video-batch-job-inline" --policy-document "file://$($policyJob -replace '\\','/')" | Out-Null

# Get ARNs
$serviceRoleArn = (ExecJson "aws iam get-role --role-name $BatchServiceRoleName --output json").Role.Arn
$instanceProfileArn = (ExecJson "aws iam get-instance-profile --instance-profile-name $InstanceProfileName --output json").InstanceProfile.Arn
$jobRoleArn = (ExecJson "aws iam get-role --role-name $JobRoleName --output json").Role.Arn
$executionRoleArn = (ExecJson "aws iam get-role --role-name $ExecutionRoleName --output json").Role.Arn

# 3) Compute Environment
Write-Host "`n[3] Ensure Compute Environment: $ComputeEnvName" -ForegroundColor Cyan
$subnetList = ($SubnetIds -join '","')
$ceJsonPath = Join-Path $InfraPath "batch\video_compute_env.json"
$ceContent = Get-Content $ceJsonPath -Raw
$ceContent = $ceContent -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $ComputeEnvName
$ceContent = $ceContent -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $serviceRoleArn
$ceContent = $ceContent -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $instanceProfileArn
$ceContent = $ceContent -replace "PLACEHOLDER_SECURITY_GROUP_ID", $SecurityGroupId
$subnetArr = ($SubnetIds | ForEach-Object { "`"$_`"" }) -join ","
$ceContent = $ceContent -replace '"PLACEHOLDER_SUBNET_1"', $subnetArr
$ceContent = $ceContent -replace "32", $MaxVcpus
$ceFile = Join-Path $RepoRoot "batch_ce_temp.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($ceFile, $ceContent, $utf8NoBom)
$ceFileUri = "file://" + ($ceFile -replace '\\', '/')

$ce = ExecJson "aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output json 2>&1"
$ceObj = $ce.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName }
if (-not $ceObj) {
    Write-Host "  Creating compute environment" -ForegroundColor Yellow
    aws batch create-compute-environment --cli-input-json $ceFileUri --region $Region
} elseif ($ceObj.status -eq "INVALID") {
    Write-Host "  Compute environment exists but INVALID; disabling, detaching queue, deleting, recreating (c6g.large)." -ForegroundColor Yellow
    $errPrev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $jqRaw = aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json 2>&1
        $jq = $null; if ($LASTEXITCODE -eq 0 -and $jqRaw) { try { $jq = $jqRaw | ConvertFrom-Json } catch {} }
        $qObj = $jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName } | Select-Object -First 1
        if ($qObj -and $qObj.state -eq "ENABLED") {
            aws batch update-job-queue --job-queue $JobQueueName --state DISABLED --region $Region 2>&1 | Out-Null
            $waitQ = 0; while ($waitQ -lt 90) { Start-Sleep -Seconds 5; $waitQ += 5; $jq2 = (aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json 2>&1) | ConvertFrom-Json; $s = ($jq2.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName }).state; if ($s -eq "DISABLED") { break } }
        }
        aws batch update-compute-environment --compute-environment $ComputeEnvName --state DISABLED --region $Region 2>&1 | Out-Null
        $waitCe = 0; while ($waitCe -lt 120) { Start-Sleep -Seconds 10; $waitCe += 10; $ceD = ExecJson "aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output json 2>&1"; $ceO = $ceD.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName }; if ($ceO.state -eq "DISABLED") { break } }
        aws batch delete-compute-environment --compute-environment $ComputeEnvName --region $Region 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "  WARN: delete-compute-environment exit $LASTEXITCODE (CE may still be draining). Waiting..." -ForegroundColor Yellow }
        $waitDel = 0; while ($waitDel -lt 120) { Start-Sleep -Seconds 10; $waitDel += 10; $ceL = ExecJson "aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output json 2>&1"; if (-not $ceL.computeEnvironments -or ($ceL.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName }).Count -eq 0) { break } }
        $ceContent = Get-Content $ceJsonPath -Raw
        $ceContent = $ceContent -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $ComputeEnvName
        $ceContent = $ceContent -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $serviceRoleArn
        $ceContent = $ceContent -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $instanceProfileArn
        $ceContent = $ceContent -replace "PLACEHOLDER_SECURITY_GROUP_ID", $SecurityGroupId
        $ceContent = $ceContent -replace '"PLACEHOLDER_SUBNET_1"', $subnetArr
        $ceContent = $ceContent -replace "32", $MaxVcpus
        if ($ceContent -notmatch '"instanceTypes"\s*:\s*\[.*c6g\.large') { $ceContent = $ceContent -replace '"instanceTypes"\s*:\s*\[[^\]]*\]', '"instanceTypes":["c6g.large"]' }
        [System.IO.File]::WriteAllText($ceFile, $ceContent, $utf8NoBom)
        aws batch create-compute-environment --cli-input-json $ceFileUri --region $Region 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL: create-compute-environment after INVALID recreate." -ForegroundColor Red; Remove-Item $ceFile -Force -ErrorAction SilentlyContinue; exit 1 }
    } finally {
        $ErrorActionPreference = $errPrev
    }
} else {
    Write-Host "  Compute environment exists; skipping update (use console if instanceTypes must change)." -ForegroundColor Gray
}
Remove-Item $ceFile -Force -ErrorAction SilentlyContinue

# Wait for compute env
Write-Host "  Waiting for compute environment VALID..." -ForegroundColor Gray
$wait = 0
while ($wait -lt 120) {
    $ce2 = ExecJson "aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output json 2>&1"
    $state = ($ce2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName }).status
    if ($state -eq "VALID") { break }
    Start-Sleep -Seconds 5
    $wait += 5
}

# 4) Job Queue (CE ARN is source of truth; fallback to new queue if update fails)
Write-Host "`n[4] Ensure Job Queue: $JobQueueName" -ForegroundColor Cyan
$ceArn = Get-ComputeEnvironmentArn -Name $ComputeEnvName
if (-not $ceArn) {
    Write-Host "  FAIL: Compute environment $ComputeEnvName not found or not VALID. Get CE ARN failed." -ForegroundColor Red
    exit 1
}
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$jqRaw = aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json 2>&1
$jqExit = $LASTEXITCODE
$ErrorActionPreference = $prevErr
$jq = $null
if ($jqExit -eq 0 -and $jqRaw) { try { $jq = $jqRaw | ConvertFrom-Json } catch {} }
$queueExists = $jq -and ($jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName })
$FinalJobQueueName = $JobQueueName
$FinalJobQueueArn = $null

if (-not $queueExists) {
    Write-Host "  Creating job queue $JobQueueName" -ForegroundColor Yellow
    $jqPath = Join-Path $InfraPath "batch\video_job_queue.json"
    $jqContent = Get-Content $jqPath -Raw
    $jqContent = $jqContent -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $ceArn
    $jqTempFile = Join-Path $RepoRoot "batch_jq_temp.json"
    [System.IO.File]::WriteAllText($jqTempFile, $jqContent, (New-Object System.Text.UTF8Encoding $false))
    $jqTempUri = "file://" + ($jqTempFile -replace '\\', '/')
    aws batch create-job-queue --cli-input-json $jqTempUri --region $Region 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL: create-job-queue failed." -ForegroundColor Red; Remove-Item $jqTempFile -Force -ErrorAction SilentlyContinue; exit 1 }
    Remove-Item $jqTempFile -Force -ErrorAction SilentlyContinue
    $FinalJobQueueArn = Get-JobQueueArn -Name $JobQueueName
    if (-not $FinalJobQueueArn) { Write-Host "  FAIL: Job queue created but get ARN failed." -ForegroundColor Red; exit 1 }
    Write-Host "  Queue created: $FinalJobQueueArn" -ForegroundColor Green
} else {
    $qObj = $jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName } | Select-Object -First 1
    $currentCeArn = ($qObj.computeEnvironmentOrder | Where-Object { $_.order -eq 1 }).computeEnvironment
    if ($currentCeArn -eq $ceArn) {
        $FinalJobQueueArn = $qObj.jobQueueArn
        Write-Host "  Job queue exists and points to CE (ARN match)." -ForegroundColor Gray
    } else {
        Write-Host "  Job queue points to different CE; updating to $ComputeEnvName (ARN)." -ForegroundColor Yellow
        $qState = $qObj.state
        if ($qState -eq "ENABLED") {
            aws batch update-job-queue --job-queue $JobQueueName --state DISABLED --region $Region 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL: Could not disable job queue." -ForegroundColor Red; exit 1 }
            $waitQ = 0
            while ($waitQ -lt 60) {
                Start-Sleep -Seconds 5
                $waitQ += 5
                $jq2Raw = aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json 2>&1
                if ($LASTEXITCODE -ne 0) { break }
                $jq2 = $jq2Raw | ConvertFrom-Json
                $s = ($jq2.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName } | Select-Object -First 1).state
                if ($s -eq "DISABLED") { break }
            }
        }
        $orderObj = @(@{ order = 1; computeEnvironment = $ceArn })
        $updatePayload = @{ jobQueue = $JobQueueName; computeEnvironmentOrder = $orderObj }
        $updateFile = Join-Path $RepoRoot "batch_update_queue_temp.json"
        $updateJson = $updatePayload | ConvertTo-Json -Depth 5
        [System.IO.File]::WriteAllText($updateFile, $updateJson, (New-Object System.Text.UTF8Encoding $false))
        $updateUri = "file://" + (([System.IO.Path]::GetFullPath($updateFile)) -replace '\\', '/')
        aws batch update-job-queue --cli-input-json $updateUri --region $Region 2>&1 | Out-Null
        Remove-Item $updateFile -Force -ErrorAction SilentlyContinue
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  FAIL: update-job-queue failed. Fix queue/CE manually; do not create extra queue." -ForegroundColor Red
            exit 1
        }
        aws batch update-job-queue --job-queue $JobQueueName --state ENABLED --region $Region 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL: Could not re-enable job queue." -ForegroundColor Red; exit 1 }
        $FinalJobQueueArn = Get-JobQueueArn -Name $JobQueueName
        Write-Host "  Queue updated to CE (ARN)." -ForegroundColor Green
    }
}
if (-not $FinalJobQueueArn) { $FinalJobQueueArn = Get-JobQueueArn -Name $FinalJobQueueName }

# 5) Job Definition
Write-Host "`n[5] Register Job Definition: $JobDefName" -ForegroundColor Cyan
$jdPath = Join-Path $InfraPath "batch\video_job_definition.json"
$jdContent = Get-Content $jdPath -Raw
$jdContent = $jdContent -replace "PLACEHOLDER_ECR_URI", $EcrRepoUri
$jdContent = $jdContent -replace "PLACEHOLDER_JOB_ROLE_ARN", $jobRoleArn
$jdContent = $jdContent -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $executionRoleArn
$jdContent = $jdContent -replace "PLACEHOLDER_REGION", $Region
$jdFile = Join-Path $RepoRoot "batch_jd_temp.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($jdFile, $jdContent, $utf8NoBom)
$fileUri = "file://" + ($jdFile -replace '\\', '/')
& aws @('batch', 'register-job-definition', '--cli-input-json', $fileUri, '--region', $Region) | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: register-job-definition $JobDefName" -ForegroundColor Red; Remove-Item $jdFile -Force -ErrorAction SilentlyContinue; exit 1 }
Remove-Item $jdFile -Force -ErrorAction SilentlyContinue
Write-Host "  JobDef image URI: $EcrRepoUri" -ForegroundColor Gray
# ECR image digest 출력 (repo:tag 파싱)
if ($EcrRepoUri -match '\.amazonaws\.com/([^:]+):(.+)$') {
    $ecrRepoName = $Matches[1]
    $ecrTag = $Matches[2]
    $digestOut = & aws @('ecr', 'describe-images', '--repository-name', $ecrRepoName, '--image-ids', "imageTag=$ecrTag", '--query', 'imageDetails[0].imageDigest', '--output', 'text', '--region', $Region) 2>&1
    if ($LASTEXITCODE -eq 0 -and $digestOut) { Write-Host "  ECR image digest: $($digestOut.Trim())" -ForegroundColor Gray }
}

# 5b) Ops Job Definitions (reconcile, scan_stuck, netprobe) — same image as worker, log group /aws/batch/academy-video-ops
Write-Host "`n[5b] Register Ops Job Definitions: academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe" -ForegroundColor Cyan
$opsJobDefs = @(
    @{ jobDefinitionName = "academy-video-ops-reconcile"; command = @("python", "manage.py", "reconcile_batch_video_jobs"); memory = 2048; timeoutSec = 900; streamPrefix = "ops" },
    @{ jobDefinitionName = "academy-video-ops-scanstuck"; command = @("python", "manage.py", "scan_stuck_video_jobs"); memory = 2048; timeoutSec = 900; streamPrefix = "ops" },
    @{ jobDefinitionName = "academy-video-ops-netprobe"; command = @("python", "manage.py", "netprobe"); memory = 512; timeoutSec = 120; streamPrefix = "netprobe" }
)
foreach ($ops in $opsJobDefs) {
    $containerProps = @{
        image = $EcrRepoUri
        vcpus = 1
        memory = $ops.memory
        command = $ops.command
        jobRoleArn = $jobRoleArn
        executionRoleArn = $executionRoleArn
        resourceRequirements = @()
        logConfiguration = @{
            logDriver = "awslogs"
            options = @{
                "awslogs-group" = "/aws/batch/academy-video-ops"
                "awslogs-region" = $Region
                "awslogs-stream-prefix" = $ops.streamPrefix
            }
        }
        environment = @()
        secrets = @()
        mountPoints = @()
        volumes = @()
    }
    $jobDef = @{
        jobDefinitionName = $ops.jobDefinitionName
        type = "container"
        containerProperties = $containerProps
        platformCapabilities = @("EC2")
        retryStrategy = @{ attempts = 1 }
        timeout = @{ attemptDurationSeconds = $ops.timeoutSec }
    }
    $tmpFile = Join-Path $RepoRoot "batch_ops_jd_$($ops.jobDefinitionName)_temp.json"
    $jobDefJson = $jobDef | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($tmpFile, $jobDefJson, $utf8NoBom)
    $tmpUri = "file://" + ($tmpFile -replace '\\', '/')
    & aws @('batch', 'register-job-definition', '--cli-input-json', $tmpUri, '--region', $Region) | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL: register-job-definition $($ops.jobDefinitionName)" -ForegroundColor Red; Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue; exit 1 }
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    Write-Host "  Registered $($ops.jobDefinitionName) (image: $EcrRepoUri)" -ForegroundColor Gray
}
foreach ($opsName in @("academy-video-ops-reconcile", "academy-video-ops-scanstuck", "academy-video-ops-netprobe")) {
    $prevO = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $jdOut = aws batch describe-job-definitions --job-definition-name $opsName --status ACTIVE --region $Region --output json 2>&1
    $ErrorActionPreference = $prevO
    if ($LASTEXITCODE -ne 0) { Write-Host "  WARN: $opsName describe ACTIVE failed." -ForegroundColor Yellow; continue }
    $jdObj = $jdOut | ConvertFrom-Json
    $defs = $jdObj.jobDefinitions | Where-Object { $_.jobDefinitionName -eq $opsName }
    if ($defs -and $defs.Count -gt 0) {
        $latest = $defs | Sort-Object -Property revision -Descending | Select-Object -First 1
        Write-Host "  ACTIVE $opsName revision $($latest.revision)" -ForegroundColor Gray
    }
}

# 6) Validation and final state
Write-Host "`n[6] Validation" -ForegroundColor Cyan
$FinalComputeEnvArn = $ceArn
$FinalComputeEnvName = $ComputeEnvName
$OpsJobDefNames = @("academy-video-ops-reconcile", "academy-video-ops-scanstuck", "academy-video-ops-netprobe")
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$finalState = @{
    FinalComputeEnvName = $FinalComputeEnvName
    FinalComputeEnvArn = $FinalComputeEnvArn
    FinalJobQueueName = $FinalJobQueueName
    FinalJobQueueArn = $FinalJobQueueArn
    WorkerJobDefName = $JobDefName
    OpsJobDefNames = $OpsJobDefNames
}
$finalStatePath = Join-Path $OutDir "batch_final_state.json"
$finalState | ConvertTo-Json | Set-Content -Path $finalStatePath -Encoding UTF8
Write-Host "  Wrote $finalStatePath (FinalJobQueueName=$FinalJobQueueName)" -ForegroundColor Gray

aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output table 2>&1 | Out-Null
aws batch describe-job-queues --job-queues $FinalJobQueueName --region $Region --output table 2>&1 | Out-Null
aws batch describe-job-definitions --job-definition-name $JobDefName --status ACTIVE --region $Region --output table 2>&1 | Out-Null

Write-Host "`nSubmitting test job (dry-run, job_id=TEST_DRYRUN)..." -ForegroundColor Yellow
$testJobName = "academy-video-batch-test-" + (Get-Date -Format "yyyyMMddHHmmss")
$submitOut = ExecJson "aws batch submit-job --job-name $testJobName --job-queue $FinalJobQueueName --job-definition $JobDefName --parameters job_id=TEST_DRYRUN --region $Region --output json"
if (-not $submitOut -or -not $submitOut.jobId) { Write-Host "  WARN: Test submit failed." -ForegroundColor Yellow } else {
    Write-Host "Submitted AWS Batch JobId=$($submitOut.jobId)" -ForegroundColor Green
}
Write-Host "`nDONE. Batch infra ready. Use JobQueueName=$FinalJobQueueName for EventBridge and run_netprobe." -ForegroundColor Green
