# IAM: Batch roles + instance profile. Uses v1/templates/iam.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"
$IamDir = $PSScriptRoot
$V4Root = (Resolve-Path (Join-Path $IamDir "..")).Path
$TemplatesPath = Join-Path $V4Root "templates\iam"

$BatchServiceRoleName = "academy-batch-service-role"
$EcsInstanceRoleName = "academy-batch-ecs-instance-role"
$InstanceProfileName = "academy-batch-ecs-instance-profile"
$JobRoleName = "academy-video-batch-job-role"
$ExecutionRoleName = "academy-batch-ecs-task-execution-role"

function Ensure-BatchIAM {
    if ($script:PlanMode) { return @{ ServiceRoleArn = ""; InstanceProfileArn = ""; JobRoleArn = ""; ExecutionRoleArn = "" } }
    Write-Step "Ensure Batch IAM"
    $trustBatch = Join-Path $TemplatesPath "trust_batch_service.json"
    $trustEc2 = Join-Path $TemplatesPath "trust_ec2.json"
    $trustEcsTasks = Join-Path $TemplatesPath "trust_ecs_tasks.json"
    $policyJob = Join-Path $TemplatesPath "policy_video_job_role.json"
    $policyBatchService = Join-Path $TemplatesPath "policy_batch_service_role.json"
    $policyEcsExecution = Join-Path $TemplatesPath "policy_ecs_task_execution_role.json"
    if (-not (Test-Path $trustBatch) -or -not (Test-Path $trustEc2)) {
        throw "IAM template not found under $TemplatesPath"
    }
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $BatchServiceRoleName, "--output", "json")
    if (-not $role) {
        Write-Host "  Creating $BatchServiceRoleName" -ForegroundColor Yellow
        $script:ChangesMade = $true
        Invoke-Aws @("iam", "create-role", "--role-name", $BatchServiceRoleName, "--assume-role-policy-document", "file://$($trustBatch -replace '\\','/')") -ErrorMessage "iam create-role BatchService" | Out-Null
    }
    Invoke-Aws @("iam", "attach-role-policy", "--role-name", $BatchServiceRoleName, "--policy-arn", "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole") -ErrorMessage "attach BatchServiceRole" 2>$null | Out-Null
    if (Test-Path $policyBatchService) {
        Invoke-Aws @("iam", "put-role-policy", "--role-name", $BatchServiceRoleName, "--policy-name", "academy-batch-service-inline", "--policy-document", "file://$($policyBatchService -replace '\\','/')") -ErrorMessage "put-role-policy" 2>$null | Out-Null
    }
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $EcsInstanceRoleName, "--output", "json")
    if (-not $role) {
        Write-Host "  Creating $EcsInstanceRoleName" -ForegroundColor Yellow
        $script:ChangesMade = $true
        Invoke-Aws @("iam", "create-role", "--role-name", $EcsInstanceRoleName, "--assume-role-policy-document", "file://$($trustEc2 -replace '\\','/')") -ErrorMessage "iam create-role ECS instance" | Out-Null
    }
    Invoke-Aws @("iam", "attach-role-policy", "--role-name", $EcsInstanceRoleName, "--policy-arn", "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role") -ErrorMessage "attach ECS instance" 2>$null | Out-Null
    $ip = Invoke-AwsJson @("iam", "get-instance-profile", "--instance-profile-name", $InstanceProfileName, "--output", "json")
    if (-not $ip) {
        $script:ChangesMade = $true
        Invoke-Aws @("iam", "create-instance-profile", "--instance-profile-name", $InstanceProfileName) -ErrorMessage "create instance profile" | Out-Null
        Invoke-Aws @("iam", "add-role-to-instance-profile", "--instance-profile-name", $InstanceProfileName, "--role-name", $EcsInstanceRoleName) -ErrorMessage "add role to profile" | Out-Null
    } else {
        $hasRole = $ip.InstanceProfile.Roles | Where-Object { $_.RoleName -eq $EcsInstanceRoleName }
        if (-not $hasRole) {
            $script:ChangesMade = $true
            Invoke-Aws @("iam", "add-role-to-instance-profile", "--instance-profile-name", $InstanceProfileName, "--role-name", $EcsInstanceRoleName) -ErrorMessage "add role to profile" | Out-Null
        }
    }
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $ExecutionRoleName, "--output", "json")
    if (-not $role) {
        Write-Host "  Creating $ExecutionRoleName" -ForegroundColor Yellow
        $script:ChangesMade = $true
        Invoke-Aws @("iam", "create-role", "--role-name", $ExecutionRoleName, "--assume-role-policy-document", "file://$($trustEcsTasks -replace '\\','/')") -ErrorMessage "iam create-role execution" | Out-Null
    }
    Invoke-Aws @("iam", "attach-role-policy", "--role-name", $ExecutionRoleName, "--policy-arn", "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy") -ErrorMessage "attach execution" 2>$null | Out-Null
    if (Test-Path $policyEcsExecution) {
        Invoke-Aws @("iam", "put-role-policy", "--role-name", $ExecutionRoleName, "--policy-name", "academy-batch-execution-inline", "--policy-document", "file://$($policyEcsExecution -replace '\\','/')") -ErrorMessage "put execution inline" 2>$null | Out-Null
    }
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $JobRoleName, "--output", "json")
    if (-not $role) {
        Write-Host "  Creating $JobRoleName" -ForegroundColor Yellow
        $script:ChangesMade = $true
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

# API/Build EC2 인스턴스가 SSM에 등록되고 ECR에서 이미지를 Pull할 수 있도록 instance profile 역할에 정책 부여
function Ensure-EC2InstanceProfileSSM {
    if ($script:PlanMode) { return }
    $profileName = $script:ApiInstanceProfile
    if (-not $profileName) { $profileName = $script:BuildInstanceProfile }
    if (-not $profileName) { return }
    $ip = Invoke-AwsJson @("iam", "get-instance-profile", "--instance-profile-name", $profileName, "--output", "json")
    if (-not $ip -or -not $ip.InstanceProfile -or -not $ip.InstanceProfile.Roles -or $ip.InstanceProfile.Roles.Count -eq 0) {
        Write-Warn "Instance profile $profileName not found; SSM policy not attached."
        return
    }
    $roleName = $ip.InstanceProfile.Roles[0].RoleName
    $policies = Invoke-AwsJson @("iam", "list-attached-role-policies", "--role-name", $roleName, "--output", "json")
    $hasSsm = $policies.AttachedPolicies | Where-Object { $_.PolicyArn -eq "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" }
    if (-not $hasSsm) {
        Invoke-Aws @("iam", "attach-role-policy", "--role-name", $roleName, "--policy-arn", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore") -ErrorMessage "attach SSM to EC2 role" | Out-Null
        Write-Ok "Attached AmazonSSMManagedInstanceCore to $roleName (SSM agent can register)"
        $script:ChangesMade = $true
    } else {
        Write-Ok "EC2 role $roleName already has AmazonSSMManagedInstanceCore"
    }
    $hasEcr = $policies.AttachedPolicies | Where-Object { $_.PolicyArn -eq "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly" }
    if (-not $hasEcr) {
        Invoke-Aws @("iam", "attach-role-policy", "--role-name", $roleName, "--policy-arn", "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly") -ErrorMessage "attach ECR read to EC2 role" | Out-Null
        Write-Ok "Attached AmazonEC2ContainerRegistryReadOnly to $roleName (API/Build can pull ECR images)"
        $script:ChangesMade = $true
    } else {
        Write-Ok "EC2 role $roleName already has AmazonEC2ContainerRegistryReadOnly"
    }
    $hasEcrPush = $policies.AttachedPolicies | Where-Object { $_.PolicyArn -eq "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser" }
    if (-not $hasEcrPush) {
        Invoke-Aws @("iam", "attach-role-policy", "--role-name", $roleName, "--policy-arn", "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser") -ErrorMessage "attach ECR PowerUser to EC2 role" | Out-Null
        Write-Ok "Attached AmazonEC2ContainerRegistryPowerUser to $roleName (Build can push ECR images)"
        $script:ChangesMade = $true
    } else {
        Write-Ok "EC2 role $roleName already has AmazonEC2ContainerRegistryPowerUser"
    }
}
