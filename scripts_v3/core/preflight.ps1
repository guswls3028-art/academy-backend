# Preflight: AWS identity, VPC, SSM, ECR. Fail fast.
function Invoke-PreflightCheck {
    Write-Step "Preflight"
    $id = Invoke-AwsJson @("sts", "get-caller-identity", "--output", "json")
    if (-not $id -or -not $id.Account) {
        throw "Preflight FAIL: AWS identity (run aws sts get-caller-identity)"
    }
    Write-Ok "Account: $($id.Account)"
    if ($id.Account -ne $script:AccountId) {
        Write-Warn "Account $($id.Account) != SSOT accountId $($script:AccountId)"
    }
    $vpc = Invoke-AwsJson @("ec2", "describe-vpcs", "--vpc-ids", $script:VpcId, "--region", $script:Region, "--output", "json")
    if (-not $vpc -or -not $vpc.Vpcs -or $vpc.Vpcs.Count -eq 0) {
        throw "Preflight FAIL: VPC $($script:VpcId) not found"
    }
    Write-Ok "VPC: $($script:VpcId)"
    $ssm = aws ssm get-parameter --name $script:SsmWorkersEnv --region $script:Region --query "Parameter.Name" --output text 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $ssm) {
        throw "Preflight FAIL: SSM $($script:SsmWorkersEnv) missing or no permission"
    }
    Write-Ok "SSM $($script:SsmWorkersEnv)"
    $ecr = Invoke-AwsJson @("ecr", "describe-repositories", "--repository-names", $script:VideoWorkerRepo, "--region", $script:Region, "--output", "json")
    if (-not $ecr -or -not $ecr.repositories) {
        Write-Warn "ECR repo $($script:VideoWorkerRepo) not found (push image first)"
    } else {
        Write-Ok "ECR $($script:VideoWorkerRepo)"
    }
    Write-Ok "Preflight done"
}
