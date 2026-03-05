# Preflight: AWS identity, region, VPC (optional), SSM, ECR. Fail fast.
# .env 는 deploy.ps1 등에서 이미 로드됨. 여기서는 검증만.
function Invoke-PreflightCheck {
    Write-Step "Preflight"

    # 1) AWS 자격 증명 검증 (get-caller-identity)
    $id = $null
    try {
        $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
        $id = Assert-AwsCredentials -RepoRoot $repoRoot
    } catch {
        throw "Preflight FAIL: $_"
    }
    if (-not $id -or -not $id.Account) {
        throw "Preflight FAIL: AWS 자격 증명 검증 실패. .env 확인 또는 -AwsProfile 사용."
    }
    Write-Ok "Account: $($id.Account)"
    if ($id.Account -ne $script:AccountId) {
        Write-Warn "Account $($id.Account) != SSOT accountId $($script:AccountId)"
    }

    # 2) Region 확인 (SSOT 기준)
    if (-not $script:Region -or $script:Region.Trim() -eq "") {
        throw "Preflight FAIL: SSOT region not set (check params.yaml global.region)"
    }
    Write-Ok "Region: $($script:Region)"

    # 3) VPC 존재 확인 (기존 VPC 모드만). 신규 리빌드 모드(VpcId 비어 있음)에서는 Ensure-Network가 생성.
    if ($script:VpcId -and $script:VpcId.Trim() -ne "") {
        $vpc = Invoke-AwsJson @("ec2", "describe-vpcs", "--vpc-ids", $script:VpcId, "--region", $script:Region, "--output", "json")
        if (-not $vpc -or -not $vpc.Vpcs -or $vpc.Vpcs.Count -eq 0) {
            throw "Preflight FAIL: VPC $($script:VpcId) not found"
        }
        Write-Ok "VPC: $($script:VpcId)"
    } else {
        Write-Ok "INFO: VpcId not set. New VPC will be created by Ensure-Network."
    }

    # 4) SSM / ECR 등 기타 기본 의존성 확인
    $ssm = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmWorkersEnv, "--region", $script:Region, "--output", "json")
    if (-not $ssm -or -not $ssm.Parameter -or -not $ssm.Parameter.Name) {
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
