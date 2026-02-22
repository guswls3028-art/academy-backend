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

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"

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

# Batch service role
$role = $null
try { $role = ExecJson "aws iam get-role --role-name $BatchServiceRoleName --output json 2>&1" } catch {}
if (-not $role) {
    Write-Host "  Creating $BatchServiceRoleName" -ForegroundColor Yellow
    aws iam create-role --role-name $BatchServiceRoleName --assume-role-policy-document "file://$($trustBatch -replace '\\','/')" | Out-Null
}
aws iam attach-role-policy --role-name $BatchServiceRoleName --policy-arn "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole" 2>$null | Out-Null

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
} else {
    Write-Host "  Compute environment exists; updating instanceTypes to c6g.large,c6g.xlarge,c6g.2xlarge" -ForegroundColor Yellow
    $cr = $ceObj.computeResources
    $updateInput = @{
        computeEnvironment = $ComputeEnvName
        computeResources   = @{
            minvCpus         = [int]$cr.minvCpus
            maxvCpus         = [int]$cr.maxvCpus
            subnets          = @($cr.subnets)
            securityGroupIds = @($cr.securityGroupIds)
            instanceTypes    = @("c6g.large", "c6g.xlarge", "c6g.2xlarge")
            instanceRole     = $cr.instanceRole
        }
    }
    $updateFile = Join-Path $RepoRoot "batch_ce_update_temp.json"
    $updateJson = $updateInput | ConvertTo-Json -Depth 6 -Compress
    [System.IO.File]::WriteAllText($updateFile, $updateJson, (New-Object System.Text.UTF8Encoding $false))
    $updateUri = "file:///" + (Resolve-Path -LiteralPath $updateFile).Path.Replace('\', '/')
    aws batch update-compute-environment --cli-input-json $updateUri --region $Region
    Remove-Item $updateFile -Force -ErrorAction SilentlyContinue
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

# 4) Job Queue
Write-Host "`n[4] Ensure Job Queue: $JobQueueName" -ForegroundColor Cyan
$jqPath = Join-Path $InfraPath "batch\video_job_queue.json"
$jq = ExecJson "aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json 2>&1"
if (-not ($jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName })) {
    aws batch create-job-queue --cli-input-json "file://$($jqPath -replace '\\','/')" --region $Region
} else {
    Write-Host "  Job queue exists" -ForegroundColor Gray
}

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
aws batch register-job-definition --cli-input-json $fileUri --region $Region
Remove-Item $jdFile -Force -ErrorAction SilentlyContinue

# 6) Validation
Write-Host "`n[6] Validation" -ForegroundColor Cyan
aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output table
aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output table
aws batch describe-job-definitions --job-definition-name $JobDefName --status ACTIVE --region $Region --output table

Write-Host "`nSubmitting test job (dry-run, job_id=TEST_DRYRUN)..." -ForegroundColor Yellow
$testJobName = "academy-video-batch-test-" + (Get-Date -Format "yyyyMMddHHmmss")
$submitOut = ExecJson "aws batch submit-job --job-name $testJobName --job-queue $JobQueueName --job-definition $JobDefName --parameters job_id=TEST_DRYRUN --region $Region --output json"
$awsJobId = $submitOut.jobId
Write-Host "Submitted AWS Batch JobId=$awsJobId" -ForegroundColor Green
Write-Host "`nTrack: aws batch describe-jobs --jobs $awsJobId --region $Region" -ForegroundColor Gray

Write-Host "`nDONE. Batch infra is ready." -ForegroundColor Green
