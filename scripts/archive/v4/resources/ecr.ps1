# ECR: Ensure repos exist. No delete.
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
}
