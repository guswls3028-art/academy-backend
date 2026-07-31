# Audit or explicitly converge the two GitHub repositories' merge and Actions
# control plane. Default is read-only. -Apply is required for external changes.
[CmdletBinding()]
param(
    [string]$Owner = "guswls3028-art",
    [string]$BackendRepository = "academy-backend",
    [string]$FrontendRepository = "academy-frontend",
    [switch]$Apply = $false
)

$ErrorActionPreference = "Stop"
$rulesetName = "academy-main-governance"

function Invoke-GhJson {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [object]$Body = $null
    )
    $tempPath = $null
    try {
        $allArguments = @("api") + $Arguments
        if ($null -ne $Body) {
            $tempPath = [IO.Path]::GetTempFileName()
            $Body | ConvertTo-Json -Depth 30 -Compress |
                Set-Content -LiteralPath $tempPath -Encoding utf8NoBOM
            $allArguments += @("--input", $tempPath)
        }
        $output = @(& gh @allArguments 2>&1)
        if ($LASTEXITCODE -ne 0) {
            throw "gh $($allArguments -join ' ') failed: $($output -join [Environment]::NewLine)"
        }
        $text = ($output -join "`n").Trim()
        if (-not $text) { return $null }
        return $text | ConvertFrom-Json
    } finally {
        if ($tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-RepositoryRuleset {
    param([string]$Repository)
    $rulesets = @(Invoke-GhJson -Arguments @("repos/$Owner/$Repository/rulesets"))
    return @($rulesets | Where-Object { [string]$_.name -eq $rulesetName }) |
        Select-Object -First 1
}

function Get-RequiredApprovingReviewCount {
    param([string]$Repository)
    $collaborators = @(
        Invoke-GhJson -Arguments @(
            "repos/$Owner/$Repository/collaborators?affiliation=direct&per_page=100"
        )
    )
    $eligibleReviewers = @(
        $collaborators |
            Where-Object {
                [bool]$_.permissions.push -and
                [string]$_.type -ne "Bot"
            }
    )
    if ($eligibleReviewers.Count -ge 2) {
        return 1
    }
    return 0
}

function Get-RulesetBody {
    param(
        [string[]]$RequiredChecks,
        [ValidateRange(0, 1)]
        [int]$RequiredApprovingReviewCount
    )
    $requiresIndependentApproval = $RequiredApprovingReviewCount -eq 1
    return [ordered]@{
        name = $rulesetName
        target = "branch"
        enforcement = "active"
        bypass_actors = @(
            [ordered]@{
                actor_id = 15368
                actor_type = "Integration"
                bypass_mode = "always"
            }
        )
        conditions = [ordered]@{
            ref_name = [ordered]@{
                include = @("~DEFAULT_BRANCH")
                exclude = @()
            }
        }
        rules = @(
            [ordered]@{ type = "deletion" },
            [ordered]@{ type = "non_fast_forward" },
            [ordered]@{
                type = "pull_request"
                parameters = [ordered]@{
                    dismiss_stale_reviews_on_push = $true
                    require_code_owner_review = $false
                    require_last_push_approval = $requiresIndependentApproval
                    required_approving_review_count = $RequiredApprovingReviewCount
                    required_review_thread_resolution = $true
                }
            },
            [ordered]@{
                type = "required_status_checks"
                parameters = [ordered]@{
                    do_not_enforce_on_create = $false
                    strict_required_status_checks_policy = $true
                    required_status_checks = @(
                        $RequiredChecks | ForEach-Object {
                            [ordered]@{ context = $_ }
                        }
                    )
                }
            }
        )
    }
}

function Set-RepositoryGovernance {
    param(
        [string]$Repository,
        [string[]]$RequiredChecks,
        [bool]$NeedsPreviewEnvironment
    )
    $repoPath = "repos/$Owner/$Repository"
    [void](Invoke-GhJson -Arguments @(
        "-X", "PUT",
        "$repoPath/actions/permissions"
    ) -Body ([ordered]@{
        enabled = $true
        allowed_actions = "all"
        sha_pinning_required = $true
    }))
    [void](Invoke-GhJson -Arguments @(
        "-X", "PUT",
        "$repoPath/actions/permissions/workflow"
    ) -Body ([ordered]@{
        default_workflow_permissions = "read"
        can_approve_pull_request_reviews = $false
    }))
    [void](Invoke-GhJson -Arguments @("-X", "PUT", "$repoPath/automated-security-fixes"))

    $ruleset = Get-RepositoryRuleset -Repository $Repository
    $requiredReviewCount = Get-RequiredApprovingReviewCount `
        -Repository $Repository
    $body = Get-RulesetBody `
        -RequiredChecks $RequiredChecks `
        -RequiredApprovingReviewCount $requiredReviewCount
    if ($ruleset) {
        [void](Invoke-GhJson -Arguments @(
            "-X", "PUT",
            "$repoPath/rulesets/$($ruleset.id)"
        ) -Body $body)
    } else {
        [void](Invoke-GhJson -Arguments @("-X", "POST", "$repoPath/rulesets") -Body $body)
    }

    $ownerUser = Invoke-GhJson -Arguments @("users/$Owner")
    [void](Invoke-GhJson -Arguments @(
        "-X", "PUT",
        "$repoPath/environments/production"
    ) -Body ([ordered]@{
        wait_timer = 0
        prevent_self_review = $false
        reviewers = @(
            [ordered]@{ type = "User"; id = [int64]$ownerUser.id }
        )
        deployment_branch_policy = [ordered]@{
            protected_branches = $true
            custom_branch_policies = $false
        }
    }))
    if ($NeedsPreviewEnvironment) {
        [void](Invoke-GhJson -Arguments @(
            "-X", "PUT",
            "$repoPath/environments/preview"
        ) -Body ([ordered]@{
            wait_timer = 0
            prevent_self_review = $false
            reviewers = @()
            deployment_branch_policy = $null
        }))
        [void](Invoke-GhJson -Arguments @(
            "-X", "PUT",
            "$repoPath/environments/production-rollback"
        ) -Body ([ordered]@{
            wait_timer = 0
            prevent_self_review = $false
            reviewers = @()
            deployment_branch_policy = [ordered]@{
                protected_branches = $true
                custom_branch_policies = $false
            }
        }))
    }
}

function Assert-RepositoryGovernance {
    param([string]$Repository)
    $repoPath = "repos/$Owner/$Repository"
    $drift = [System.Collections.Generic.List[string]]::new()
    $actions = Invoke-GhJson -Arguments @("$repoPath/actions/permissions")
    $workflow = Invoke-GhJson -Arguments @("$repoPath/actions/permissions/workflow")
    $ruleset = Get-RepositoryRuleset -Repository $Repository
    if (
        -not [bool]$actions.enabled -or
        [string]$actions.allowed_actions -ne "all" -or
        -not [bool]$actions.sha_pinning_required
    ) {
        [void]$drift.Add("Actions must be enabled, allow the reviewed action inventory, and require SHA pinning.")
    }
    if (
        [string]$workflow.default_workflow_permissions -ne "read" -or
        [bool]$workflow.can_approve_pull_request_reviews
    ) {
        [void]$drift.Add("Default GITHUB_TOKEN permissions must be read-only and may not approve pull requests.")
    }
    try {
        [void](Invoke-GhJson -Arguments @("$repoPath/automated-security-fixes"))
    } catch {
        [void]$drift.Add("Dependabot security updates are not enabled.")
    }

    $requiredChecks = if ($Repository -eq $BackendRepository) {
        @(
            "Backend static and migration contract",
            "Backend Django smoke and deployment contracts"
        )
    } else {
        @(
            "Hangul companion Windows COM contract",
            "Typecheck + Lint + Build"
        )
    }
    if (-not $ruleset) {
        [void]$drift.Add("The academy-main-governance ruleset is missing.")
    } else {
        $rulesetDetails = Invoke-GhJson -Arguments @(
            "$repoPath/rulesets/$($ruleset.id)"
        )
        $requiredReviewCount = Get-RequiredApprovingReviewCount `
            -Repository $Repository
        $requiresIndependentApproval = $requiredReviewCount -eq 1
        $includes = @($rulesetDetails.conditions.ref_name.include)
        $excludes = @($rulesetDetails.conditions.ref_name.exclude)
        if (
            [string]$rulesetDetails.target -ne "branch" -or
            [string]$rulesetDetails.enforcement -ne "active" -or
            $includes.Count -ne 1 -or
            [string]$includes[0] -ne "~DEFAULT_BRANCH" -or
            $excludes.Count -ne 0
        ) {
            [void]$drift.Add("Ruleset target, enforcement, or default-branch condition does not match.")
        }
        $bypassActors = @($rulesetDetails.bypass_actors)
        if (
            $bypassActors.Count -ne 1 -or
            [int64]$bypassActors[0].actor_id -ne 15368 -or
            [string]$bypassActors[0].actor_type -ne "Integration" -or
            [string]$bypassActors[0].bypass_mode -ne "always"
        ) {
            [void]$drift.Add("Ruleset bypass inventory must contain only the GitHub Actions integration.")
        }
        foreach ($ruleType in @("deletion", "non_fast_forward")) {
            if (@($rulesetDetails.rules | Where-Object { [string]$_.type -eq $ruleType }).Count -ne 1) {
                [void]$drift.Add("Ruleset must contain exactly one '$ruleType' rule.")
            }
        }
        $pullRequestRules = @(
            $rulesetDetails.rules |
                Where-Object { [string]$_.type -eq "pull_request" }
        )
        if (
            $pullRequestRules.Count -ne 1 -or
            -not [bool]$pullRequestRules[0].parameters.dismiss_stale_reviews_on_push -or
            [bool]$pullRequestRules[0].parameters.require_code_owner_review -or
            [bool]$pullRequestRules[0].parameters.require_last_push_approval -ne
                $requiresIndependentApproval -or
            [int]$pullRequestRules[0].parameters.required_approving_review_count -ne
                $requiredReviewCount -or
            -not [bool]$pullRequestRules[0].parameters.required_review_thread_resolution
        ) {
            [void]$drift.Add(
                "Pull-request review policy does not match the available-maintainer contract."
            )
        }
        $statusRules = @(
            $rulesetDetails.rules |
                Where-Object { [string]$_.type -eq "required_status_checks" }
        )
        $actualChecks = if ($statusRules.Count -eq 1) {
            @(
                $statusRules[0].parameters.required_status_checks |
                    ForEach-Object { [string]$_.context } |
                    Sort-Object -Unique
            )
        } else {
            @()
        }
        $expectedChecks = @($requiredChecks | Sort-Object -Unique)
        if (
            $statusRules.Count -ne 1 -or
            [bool]$statusRules[0].parameters.do_not_enforce_on_create -or
            -not [bool]$statusRules[0].parameters.strict_required_status_checks_policy -or
            ($actualChecks -join "`n") -cne ($expectedChecks -join "`n")
        ) {
            [void]$drift.Add("Required status checks do not exactly match the repository contract.")
        }
    }

    $ownerUser = Invoke-GhJson -Arguments @("users/$Owner")
    try {
        $production = Invoke-GhJson -Arguments @(
            "$repoPath/environments/production"
        )
        $reviewRules = @(
            $production.protection_rules |
                Where-Object { [string]$_.type -eq "required_reviewers" }
        )
        $reviewers = if ($reviewRules.Count -eq 1) {
            @($reviewRules[0].reviewers)
        } else {
            @()
        }
        if (
            $reviewRules.Count -ne 1 -or
            $reviewers.Count -ne 1 -or
            [int64]$reviewers[0].reviewer.id -ne [int64]$ownerUser.id -or
            [bool]$reviewRules[0].prevent_self_review -or
            -not [bool]$production.deployment_branch_policy.protected_branches -or
            [bool]$production.deployment_branch_policy.custom_branch_policies
        ) {
            [void]$drift.Add("Production environment reviewer or protected-branch policy does not match.")
        }
    } catch {
        [void]$drift.Add("Production environment is missing or unreadable.")
    }

    if ($Repository -eq $FrontendRepository) {
        try {
            $preview = Invoke-GhJson -Arguments @(
                "$repoPath/environments/preview"
            )
            if (
                @($preview.protection_rules).Count -ne 0 -or
                $null -ne $preview.deployment_branch_policy
            ) {
                [void]$drift.Add("Preview environment must have no reviewers or branch restriction.")
            }
        } catch {
            [void]$drift.Add("Preview environment is missing or unreadable.")
        }
        try {
            $rollback = Invoke-GhJson -Arguments @(
                "$repoPath/environments/production-rollback"
            )
            $rollbackReviewRules = @(
                $rollback.protection_rules |
                    Where-Object { [string]$_.type -eq "required_reviewers" }
            )
            if (
                $rollbackReviewRules.Count -ne 0 -or
                -not [bool]$rollback.deployment_branch_policy.protected_branches -or
                [bool]$rollback.deployment_branch_policy.custom_branch_policies
            ) {
                [void]$drift.Add("Production rollback environment must be reviewer-free and protected-branch only.")
            }
        } catch {
            [void]$drift.Add("Production rollback environment is missing or unreadable.")
        }
    }

    if ($drift.Count -gt 0) {
        throw (
            "GITHUB_GOVERNANCE_DRIFT repository=$Repository`n- " +
            ($drift -join "`n- ")
        )
    }
    Write-Host "GITHUB_GOVERNANCE_PASS repository=$Repository" -ForegroundColor Green
}

& gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI authentication is required."
}

if ($Apply) {
    Set-RepositoryGovernance `
        -Repository $BackendRepository `
        -RequiredChecks @(
            "Backend static and migration contract",
            "Backend Django smoke and deployment contracts"
        ) `
        -NeedsPreviewEnvironment $false
    Set-RepositoryGovernance `
        -Repository $FrontendRepository `
        -RequiredChecks @(
            "Hangul companion Windows COM contract",
            "Typecheck + Lint + Build"
        ) `
        -NeedsPreviewEnvironment $true
}

$auditFailures = [System.Collections.Generic.List[string]]::new()
foreach ($repository in @($BackendRepository, $FrontendRepository)) {
    try {
        Assert-RepositoryGovernance -Repository $repository
    } catch {
        [void]$auditFailures.Add($_.Exception.Message)
    }
}
if ($auditFailures.Count -gt 0) {
    throw (
        "GITHUB_GOVERNANCE_AUDIT_FAILED`n" +
        ($auditFailures -join "`n")
    )
}
