# ECR: Ensure repos exist. No delete. Apply lifecycle policy for cost optimization (no unnecessary images).
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Get-ECRLifecyclePolicyPath {
    $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    $paths = @(
        (Join-Path $repoRoot "docs\ssot\ecr-lifecycle-policy.json"),
        (Join-Path $repoRoot "docs\scripts\ecr-lifecycle-policy.json")
    )
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return $null
}

function Set-ECRLifecyclePolicy {
    param([string]$RepositoryName)
    $policyPath = Get-ECRLifecyclePolicyPath
    if (-not $policyPath) { Write-Warn "ECR lifecycle policy file not found; skip apply for $RepositoryName"; return }
    if ($script:PlanMode) { return }
    $policyJson = Get-Content -LiteralPath $policyPath -Raw -Encoding UTF8
    if (-not $policyJson -or $policyJson.Trim() -eq '') { Write-Warn "ECR lifecycle policy empty; skip $RepositoryName"; return }
    try {
        $policyText = ($policyJson | ConvertFrom-Json | ConvertTo-Json -Depth 32 -Compress)
        Invoke-Aws @("ecr", "put-lifecycle-policy", "--repository-name", $RepositoryName, "--lifecycle-policy-text", $policyText, "--region", $script:Region) -ErrorMessage "put-lifecycle-policy $RepositoryName" | Out-Null
        Write-Ok "ECR lifecycle policy applied: $RepositoryName"
    } catch {
        Write-Warn "ECR lifecycle policy apply failed for $RepositoryName : $_"
    }
}

function Ensure-ECRRepos {
    Write-Step "ECR repos"
    if ($script:PlanMode) { Write-Ok "ECR check skipped (Plan)"; return }
    foreach ($repo in $script:SSOT_ECR) {
        $r = Invoke-AwsJson @("ecr", "describe-repositories", "--repository-names", $repo, "--region", $script:Region, "--output", "json")
        if (-not $r -or -not $r.repositories) {
            Write-Host "  Creating $repo" -ForegroundColor Yellow
            $script:ChangesMade = $true
            Invoke-Aws @(
                "ecr", "create-repository",
                "--repository-name", $repo,
                "--image-tag-mutability", "IMMUTABLE_WITH_EXCLUSION",
                "--image-tag-mutability-exclusion-filters", "filterType=WILDCARD,filter=latest",
                "--region", $script:Region
            ) -ErrorMessage "create-repository $repo" | Out-Null
        } else {
            $current = @($r.repositories)[0]
            $filters = @($current.imageTagMutabilityExclusionFilters)
            $isExact = (
                [string]$current.imageTagMutability -eq "IMMUTABLE_WITH_EXCLUSION" -and
                $filters.Count -eq 1 -and
                [string]$filters[0].filterType -eq "WILDCARD" -and
                [string]$filters[0].filter -eq "latest"
            )
            if (-not $isExact) {
                Invoke-Aws @(
                    "ecr", "put-image-tag-mutability",
                    "--repository-name", $repo,
                    "--image-tag-mutability", "IMMUTABLE_WITH_EXCLUSION",
                    "--image-tag-mutability-exclusion-filters", "filterType=WILDCARD,filter=latest",
                    "--region", $script:Region
                ) -ErrorMessage "put-image-tag-mutability $repo" | Out-Null
                $script:ChangesMade = $true
            }
        }
        $verified = Invoke-AwsJson @("ecr", "describe-repositories", "--repository-names", $repo, "--region", $script:Region, "--output", "json")
        $verifiedRepo = @($verified.repositories)[0]
        $verifiedFilters = @($verifiedRepo.imageTagMutabilityExclusionFilters)
        if (
            -not $verifiedRepo -or
            [string]$verifiedRepo.imageTagMutability -ne "IMMUTABLE_WITH_EXCLUSION" -or
            $verifiedFilters.Count -ne 1 -or
            [string]$verifiedFilters[0].filterType -ne "WILDCARD" -or
            [string]$verifiedFilters[0].filter -ne "latest"
        ) {
            throw "ECR repository $repo must be immutable with a single latest-only exclusion."
        }
        Write-Ok "$repo (sha-* immutable; latest mutable compatibility alias)"
    }
    foreach ($repo in $script:SSOT_ECR) {
        Set-ECRLifecyclePolicy -RepositoryName $repo
    }
}
