# Preflight: AWS identity, region, VPC (optional), SSM, ECR. Fail fast.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
# .env 는 deploy.ps1 등 호출 전 에이전트가 환경변수로 넣음. 여기서는 검증만.
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

# PHASE 4: -DeployFront 시 SSOT 필수값 공란이면 FAIL. 프론트 배포/검증 불가 방지.
function Assert-SSOTFrontR2Required {
    $missing = [System.Collections.ArrayList]::new()
    if (-not $script:FrontDomainApp -or $script:FrontDomainApp.Trim() -eq "") { [void]$missing.Add("front.domains.app") }
    if (-not $script:FrontDomainApi -or $script:FrontDomainApi.Trim() -eq "") { [void]$missing.Add("front.domains.api") }
    if (-not $script:FrontR2StaticBucket -or $script:FrontR2StaticBucket.Trim() -eq "") { [void]$missing.Add("front.r2StaticBucket") }
    if (-not $script:R2Bucket -or $script:R2Bucket.Trim() -eq "") { [void]$missing.Add("r2.bucket") }
    if (-not $script:R2PublicBaseUrl -or $script:R2PublicBaseUrl.Trim() -eq "") { [void]$missing.Add("r2.publicBaseUrl") }
    if ($missing.Count -gt 0) {
        $msg = "SSOT 필수값 공란. params.yaml에서 다음을 채운 뒤 재실행: " + ($missing -join ", ") + ". (front.cors.allowedOrigins는 CORS 사용 시 PHASE 4에서 채우면 됨)"
        throw "Preflight FAIL (DeployFront): $msg"
    }
    if (-not $script:FrontCorsAllowedOrigins -or $script:FrontCorsAllowedOrigins.Count -eq 0) {
        $co = $script:FrontCorsAllowedOrigins
        if ($co -is [string] -and $co -eq "[]") { }
        else { Write-Host "  [DeployFront] front.cors.allowedOrigins 비어 있음. CORS 검증 시 params에 origin 추가 권장." -ForegroundColor Yellow }
    }
    Write-Ok "SSOT front/r2 필수값 확인됨"
}
