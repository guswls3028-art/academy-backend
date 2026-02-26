# Ensure Batch IAM roles and instance profile. Uses scripts/infra/iam/*.json (read-only). Returns ARNs for CE/JobDef.
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$IamPath = Join-Path $InfraPath "iam"

$BatchServiceRoleName = "academy-batch-service-role"
$EcsInstanceRoleName = "academy-batch-ecs-instance-role"
$InstanceProfileName = "academy-batch-ecs-instance-profile"
$JobRoleName = "academy-video-batch-job-role"
$ExecutionRoleName = "academy-batch-ecs-task-execution-role"

function Ensure-BatchIAM {
    Write-Step "Ensure Batch IAM (roles + instance profile)"
    $trustBatch = Join-Path $IamPath "trust_batch_service.json"
    $trustEc2 = Join-Path $IamPath "trust_ec2.json"
    $trustEcsTasks = Join-Path $IamPath "trust_ecs_tasks.json"
    $policyJob = Join-Path $IamPath "policy_video_job_role.json"
    $policyBatchService = Join-Path $IamPath "policy_batch_service_role.json"
    $policyEcsExecution = Join-Path $IamPath "policy_ecs_task_execution_role.json"
    if (-not (Test-Path $trustBatch) -or -not (Test-Path $trustEc2)) {
        throw "IAM template not found under $IamPath. Ensure scripts/infra/iam/*.json exists."
    }
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $BatchServiceRoleName, "--output", "json")
    if (-not $role) {
        Write-Host "  Creating $BatchServiceRoleName" -ForegroundColor Yellow
        Invoke-Aws @("iam", "create-role", "--role-name", $BatchServiceRoleName, "--assume-role-policy-document", "file://$($trustBatch -replace '\\','/')") -ErrorMessage "iam create-role BatchService" | Out-Null
    }
    Invoke-Aws @("iam", "attach-role-policy", "--role-name", $BatchServiceRoleName, "--policy-arn", "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole") -ErrorMessage "attach BatchServiceRole" 2>$null | Out-Null
    if (Test-Path $policyBatchService) {
        Invoke-Aws @("iam", "put-role-policy", "--role-name", $BatchServiceRoleName, "--policy-name", "academy-batch-service-inline", "--policy-document", "file://$($policyBatchService -replace '\\','/')") -ErrorMessage "put-role-policy" 2>$null | Out-Null
    }
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $EcsInstanceRoleName, "--output", "json")
    if (-not $role) {
        Write-Host "  Creating $EcsInstanceRoleName" -ForegroundColor Yellow
        Invoke-Aws @("iam", "create-role", "--role-name", $EcsInstanceRoleName, "--assume-role-policy-document", "file://$($trustEc2 -replace '\\','/')") -ErrorMessage "iam create-role ECS instance" | Out-Null
    }
    Invoke-Aws @("iam", "attach-role-policy", "--role-name", $EcsInstanceRoleName, "--policy-arn", "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role") -ErrorMessage "attach ECS instance" 2>$null | Out-Null
    $ip = Invoke-AwsJson @("iam", "get-instance-profile", "--instance-profile-name", $InstanceProfileName, "--output", "json")
    if (-not $ip) {
        Invoke-Aws @("iam", "create-instance-profile", "--instance-profile-name", $InstanceProfileName) -ErrorMessage "create instance profile" | Out-Null
        Invoke-Aws @("iam", "add-role-to-instance-profile", "--instance-profile-name", $InstanceProfileName, "--role-name", $EcsInstanceRoleName) -ErrorMessage "add role to profile" | Out-Null
    } else {
        $hasRole = $ip.InstanceProfile.Roles | Where-Object { $_.RoleName -eq $EcsInstanceRoleName }
        if (-not $hasRole) {
            Invoke-Aws @("iam", "add-role-to-instance-profile", "--instance-profile-name", $InstanceProfileName, "--role-name", $EcsInstanceRoleName) -ErrorMessage "add role to profile" | Out-Null
        }
    }
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $ExecutionRoleName, "--output", "json")
    if (-not $role) {
        Write-Host "  Creating $ExecutionRoleName" -ForegroundColor Yellow
        Invoke-Aws @("iam", "create-role", "--role-name", $ExecutionRoleName, "--assume-role-policy-document", "file://$($trustEcsTasks -replace '\\','/')") -ErrorMessage "iam create-role execution" | Out-Null
    }
    Invoke-Aws @("iam", "attach-role-policy", "--role-name", $ExecutionRoleName, "--policy-arn", "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy") -ErrorMessage "attach execution" 2>$null | Out-Null
    if (Test-Path $policyEcsExecution) {
        Invoke-Aws @("iam", "put-role-policy", "--role-name", $ExecutionRoleName, "--policy-name", "academy-batch-execution-inline", "--policy-document", "file://$($policyEcsExecution -replace '\\','/')") -ErrorMessage "put execution inline" 2>$null | Out-Null
    }
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $JobRoleName, "--output", "json")
    if (-not $role) {
        Write-Host "  Creating $JobRoleName" -ForegroundColor Yellow
        Invoke-Aws @("iam", "create-role", "--role-name", $JobRoleName, "--assume-role-policy-document", "file://$($trustEcsTasks -replace '\\','/')") -ErrorMessage "iam create-role job" | Out-Null
    }
    if (Test-Path $policyJob) {
        Invoke-Aws @("iam", "put-role-policy", "--role-name", $JobRoleName, "--policy-name", "academy-video-batch-job-inline", "--policy-document", "file://$($policyJob -replace '\\','/')") -ErrorMessage "put job inline" | Out-Null
    }
    $serviceRoleArn = (Invoke-AwsJson @("iam", "get-role", "--role-name", $BatchServiceRoleName, "--output", "json")).Role.Arn
    $instanceProfileArn = (Invoke-AwsJson @("iam", "get-instance-profile", "--instance-profile-name", $InstanceProfileName, "--output", "json")).InstanceProfile.Arn
    $jobRoleArn = (Invoke-AwsJson @("iam", "get-role", "--role-name", $JobRoleName, "--output", "json")).Role.Arn
    $executionRoleArn = (Invoke-AwsJson @("iam", "get-role", "--role-name", $ExecutionRoleName, "--output", "json")).Role.Arn
    Write-Ok "Batch IAM ready"
    return @{
        ServiceRoleArn = $serviceRoleArn
        InstanceProfileArn = $instanceProfileArn
        JobRoleArn = $jobRoleArn
        ExecutionRoleArn = $executionRoleArn
    }
}
