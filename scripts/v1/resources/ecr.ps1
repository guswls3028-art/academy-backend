# ECR: Ensure repos exist. No delete. Apply lifecycle policy for cost optimization (no unnecessary images).
$ErrorActionPreference = "Stop"

function Get-ECRLifecyclePolicyPath {
    $resRoot = (Get-Item $PSScriptRoot).Parent.Parent
    $repoRoot = (Get-Item $resRoot).Parent.Parent
    $path = Join-Path $repoRoot "docs\00-SSOT\v1\scripts\ecr-lifecycle-policy.json"
    if (Test-Path -LiteralPath $path) { return $path }
    return $null
}

function Set-ECRLifecyclePolicy {
    param([string]$RepositoryName)
    $policyPath = Get-ECRLifecyclePolicyPath
    if (-not $policyPath) { Write-Warn "ECR lifecycle policy file not found; skip apply for $RepositoryName"; return }
    if ($script:PlanMode) { return }
    $pathArg = "file://$($policyPath -replace '\\','/')"
    try {
        Invoke-Aws @("ecr", "put-lifecycle-policy", "--repository-name", $RepositoryName, "--lifecycle-policy-text", $pathArg, "--region", $script:Region) -ErrorMessage "put-lifecycle-policy $RepositoryName" | Out-Null
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
            Invoke-Aws @("ecr", "create-repository", "--repository-name", $repo, "--region", $script:Region) -ErrorMessage "create-repository $repo" | Out-Null
        }
        Write-Ok $repo
    }
    foreach ($repo in $script:SSOT_ECR) {
        Set-ECRLifecyclePolicy -RepositoryName $repo
    }
}
