Set-StrictMode -Version Latest

function Get-NormalizedAcademyPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $normalized = $Path.Trim().Replace("\", "/")
    while ($normalized.StartsWith("./", [StringComparison]::Ordinal)) {
        $normalized = $normalized.Substring(2)
    }
    return $normalized.TrimStart("/").ToLowerInvariant()
}

function Add-UniqueValue {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][Collections.Generic.List[string]]$Target,
        [Parameter(Mandatory = $true)][string]$Value
    )
    if (-not $Target.Contains($Value)) { [void]$Target.Add($Value) }
}

function Test-AnyPath {
    param(
        [string[]]$Paths,
        [Parameter(Mandatory = $true)][string]$Pattern
    )
    return [bool](@($Paths | Where-Object { $_ -match $Pattern }).Count)
}

function Get-AcademyGitPathLines {
    param([AllowEmptyCollection()][string[]]$Lines = @())
    return @($Lines | Where-Object {
        $_ -and $_ -notmatch '^(warning|hint):\s'
    })
}

function Get-AcademyChangeRiskPlan {
    [CmdletBinding()]
    param(
        [string[]]$BackendPaths = @(),
        [string[]]$FrontendPaths = @()
    )

    $backend = @($BackendPaths | Where-Object { $_ } | ForEach-Object { Get-NormalizedAcademyPath $_ })
    $frontend = @($FrontendPaths | Where-Object { $_ } | ForEach-Object { Get-NormalizedAcademyPath $_ })
    $all = @($backend) + @($frontend)
    if (-not $all.Count) { throw "At least one changed backend or frontend path is required." }

    $docsPattern = '^(docs/)|(^|/)(agents|readme(?:[-_.][^/]*)?|conventions|contributing|security|code_of_conduct|changelog|license)\.md$'
    $backendTestPattern = '(^|/)__tests__(/|$)|(^|/)tests?(/|\.py$)|(^|/)test_[^/]+\.py$|_test\.py$'
    $frontendTestPattern = '^e2e/|(^|/)(__tests__|tests?)(/|$)|\.(spec|test)\.[^/]+$|(^|/)test\.[^/]+$'
    $backendProductPattern = '^(apps/|academy/|schema/|scripts/lint/|manage\.py$)'
    $backendRuntimeBuildPattern = '^(libs/|docker/|requirements/)'
    $frontendRuntimePattern = '^(src/|public/|functions/)'
    $frontendRuntimeBuildPattern = '^(package\.json$|pnpm-lock\.yaml$|vite\.config\.[^/]+$|tsconfig(?:\.[^/]+)?\.json$|eslint\.config\.[^/]+$|index\.html$)'
    $backendGovernancePattern = '^((\.github/workflows/)|(scripts/(v1|codex|post_deploy_smoke)/)|(docs/(operations|infrastructure)/))'
    $frontendGovernancePattern = '^((\.github/workflows/)|(scripts/guard-deployment-governance\.mjs$)|(scripts/guard-runtime)|(scripts/tests/(visual-audit-workflow|workspace-deployment-contract))|(docs/deployment-operations\.md$))'
    $docsOnly = -not [bool](@($all | Where-Object { $_ -notmatch $docsPattern }).Count)
    $backendProduct = Test-AnyPath $backend $backendProductPattern
    $backendRuntimePaths = @($backend | Where-Object {
        (
            $_ -match $backendRuntimeBuildPattern -or
            $_ -match '^(apps/|academy/)'
        ) -and
        $_ -notmatch $docsPattern -and
        $_ -notmatch $backendTestPattern
    })
    $backendRuntimeBuild = Test-AnyPath $backendRuntimePaths $backendRuntimeBuildPattern
    $backendRuntime = [bool]$backendRuntimePaths.Count
    $frontendRuntimePaths = @($frontend | Where-Object {
        (
            $_ -match $frontendRuntimePattern -or
            $_ -match $frontendRuntimeBuildPattern
        ) -and
        $_ -notmatch $docsPattern -and
        $_ -notmatch $frontendTestPattern
    })
    $frontendRuntimeBuild = Test-AnyPath $frontendRuntimePaths $frontendRuntimeBuildPattern
    $frontendRuntime = [bool]$frontendRuntimePaths.Count
    $frontendE2e = Test-AnyPath $frontend '^e2e/'
    $backendMigration = Test-AnyPath $backend '(^|/)migrations/'
    $asyncWorker = Test-AnyPath $backendRuntimePaths '(^|/)(messaging|video|ai|tools|queues?|workers?)(/|$)'
    $backendGovernance = Test-AnyPath $backend $backendGovernancePattern
    $frontendGovernance = Test-AnyPath $frontend $frontendGovernancePattern
    $crossRepositoryProduct = $backendRuntime -and $frontendRuntime

    $unknownBackend = @($backend | Where-Object {
        $_ -notmatch $docsPattern -and
        $_ -notmatch $backendTestPattern -and
        $_ -notmatch $backendProductPattern -and
        $_ -notmatch $backendRuntimeBuildPattern -and
        $_ -notmatch $backendGovernancePattern
    })
    $unknownFrontend = @($frontend | Where-Object {
        $_ -notmatch $docsPattern -and
        $_ -notmatch $frontendTestPattern -and
        $_ -notmatch $frontendRuntimePattern -and
        $_ -notmatch $frontendRuntimeBuildPattern -and
        $_ -notmatch $frontendGovernancePattern
    })
    if ($unknownBackend.Count -or $unknownFrontend.Count) {
        $unknownPaths = @($unknownBackend | ForEach-Object { "backend:$_" }) +
            @($unknownFrontend | ForEach-Object { "frontend:$_" })
        throw "Unclassified non-documentation path(s): $($unknownPaths -join ', ')"
    }

    $risks = [Collections.Generic.List[string]]::new()
    $requirements = [Collections.Generic.List[string]]::new()
    $gates = [Collections.Generic.List[string]]::new()

    Add-UniqueValue $requirements "owning-docs-current"
    if ($backend.Count) { Add-UniqueValue $gates "backend-diff-check" }
    if ($frontend.Count) { Add-UniqueValue $gates "frontend-diff-check" }

    if ($backendProduct) {
        Add-UniqueValue $risks "tenant-data"
        Add-UniqueValue $requirements "failure-first-regression"
        Add-UniqueValue $requirements "postgresql-tenant-ci"
        Add-UniqueValue $gates "backend-core"
    }
    if ($backendRuntimeBuild) {
        Add-UniqueValue $risks "backend-runtime-build"
        Add-UniqueValue $requirements "failure-first-regression"
        Add-UniqueValue $requirements "postgresql-tenant-ci"
        Add-UniqueValue $gates "backend-core"
        Add-UniqueValue $gates "backend-deployment-contracts"
    }
    if ($backendMigration) {
        Add-UniqueValue $risks "migration-compatibility"
        Add-UniqueValue $requirements "expand-contract-migration"
    }
    if ($asyncWorker) {
        Add-UniqueValue $risks "async-worker"
        Add-UniqueValue $requirements "worker-queue-runtime-readback"
    }
    if ($frontendRuntime) {
        Add-UniqueValue $risks "user-visible-ui"
        Add-UniqueValue $requirements "failure-first-regression"
        Add-UniqueValue $requirements "desktop-390-live-readback"
        Add-UniqueValue $gates "frontend-core"
    }
    if ($frontendRuntimeBuild) {
        Add-UniqueValue $risks "frontend-runtime-build"
        Add-UniqueValue $gates "frontend-deployment-contracts"
    }
    if ($frontendRuntime -or $frontendE2e) {
        Add-UniqueValue $gates "frontend-e2e"
    }
    if ($backendGovernance -or $frontendGovernance) {
        Add-UniqueValue $risks "deployment-governance"
    }
    if ($backendGovernance) { Add-UniqueValue $gates "backend-deployment-contracts" }
    if ($frontendGovernance) { Add-UniqueValue $gates "frontend-deployment-contracts" }
    if ($crossRepositoryProduct) {
        Add-UniqueValue $risks "cross-repository-contract"
        Add-UniqueValue $requirements "backward-compatible-api-window"
        Add-UniqueValue $requirements "production-release-bundle-readback"
    }
    if ($docsOnly) { Add-UniqueValue $risks "documentation-only" }

    return [pscustomobject]@{
        SchemaVersion = 1
        DocsOnly = $docsOnly
        BackendPaths = $backend
        FrontendPaths = $frontend
        Risks = @($risks)
        Requirements = @($requirements)
        Gates = @($gates)
        RequiresProductionReleaseBundle = $crossRepositoryProduct
    }
}

function Assert-ReleaseBundleCondition {
    param([bool]$Condition, [Parameter(Mandatory = $true)][string]$Message)
    if (-not $Condition) { throw "Production release bundle rejected: $Message" }
}

function Get-AcademyDeploymentLockState {
    [CmdletBinding()]
    param(
        [Parameter()][AllowNull()][object]$LockReadback,
        [long]$Now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    )

    if ($null -eq $LockReadback) {
        return [pscustomobject]@{ Active = $false; Owner = $null; ExpiresAt = 0L }
    }
    if ($LockReadback -isnot [pscustomobject]) {
        throw "Deployment lock readback rejected: malformed DynamoDB lock readback"
    }
    $itemProperty = $LockReadback.PSObject.Properties['Item']
    if ($null -eq $itemProperty) {
        return [pscustomobject]@{ Active = $false; Owner = $null; ExpiresAt = 0L }
    }

    $lockItem = $itemProperty.Value
    if ($null -eq $lockItem -or $lockItem -isnot [pscustomobject]) {
        throw "Deployment lock readback rejected: malformed DynamoDB lock Item"
    }
    $ownerProperty = $lockItem.PSObject.Properties['owner']
    $ttlProperty = $lockItem.PSObject.Properties['ttl']
    if ($null -eq $ownerProperty -or $null -eq $ttlProperty) {
        throw "Deployment lock readback rejected: malformed DynamoDB lock Item"
    }
    $ownerAttribute = $ownerProperty.Value
    $ttlAttribute = $ttlProperty.Value
    if ($null -eq $ownerAttribute -or $ownerAttribute -isnot [pscustomobject] -or
        $null -eq $ttlAttribute -or $ttlAttribute -isnot [pscustomobject]) {
        throw "Deployment lock readback rejected: malformed DynamoDB lock Item"
    }
    $ownerSProperty = $ownerAttribute.PSObject.Properties['S']
    $ttlNProperty = $ttlAttribute.PSObject.Properties['N']
    $ownerAttributeProperties = @($ownerAttribute.PSObject.Properties | ForEach-Object { $_.Name })
    $ttlAttributeProperties = @($ttlAttribute.PSObject.Properties | ForEach-Object { $_.Name })
    if ($ownerAttributeProperties.Count -ne 1 -or $ownerAttributeProperties[0] -cne 'S' -or
        $ttlAttributeProperties.Count -ne 1 -or $ttlAttributeProperties[0] -cne 'N' -or
        $null -eq $ownerSProperty -or $ownerSProperty.Value -isnot [string] -or
        [string]::IsNullOrWhiteSpace([string]$ownerSProperty.Value) -or
        $null -eq $ttlNProperty -or $ttlNProperty.Value -isnot [string] -or
        [string]$ttlNProperty.Value -notmatch '^[0-9]+$') {
        throw "Deployment lock readback rejected: malformed DynamoDB lock Item"
    }
    $lockTtl = 0L
    if (-not [long]::TryParse([string]$ttlNProperty.Value, [ref]$lockTtl)) {
        throw "Deployment lock readback rejected: malformed DynamoDB lock Item"
    }
    $lockOwner = [string]$ownerSProperty.Value
    return [pscustomobject]@{
        Active = [bool]($lockOwner -and $lockTtl -ge $Now)
        Owner = $lockOwner
        ExpiresAt = $lockTtl
    }
}

function Assert-ReleaseRun {
    param(
        [Parameter(Mandatory = $true)][object]$Run,
        [Parameter(Mandatory = $true)][string]$ExpectedSha,
        [Parameter(Mandatory = $true)][string]$ExpectedWorkflow,
        [Parameter(Mandatory = $true)][string[]]$AllowedEvents,
        [Parameter(Mandatory = $true)][string[]]$RequiredJobs,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Assert-ReleaseBundleCondition ([string]$Run.status -eq "completed") "$Label release run is not completed"
    Assert-ReleaseBundleCondition ([string]$Run.conclusion -eq "success") "$Label release run did not succeed"
    Assert-ReleaseBundleCondition ([string]$Run.headBranch -eq "main") "$Label release run branch is not main"
    Assert-ReleaseBundleCondition ([string]$Run.headSha -eq $ExpectedSha) "$Label release run SHA does not match the expected SHA"
    Assert-ReleaseBundleCondition ([string]$Run.workflowName -eq $ExpectedWorkflow) "$Label release workflow is not '$ExpectedWorkflow'"
    Assert-ReleaseBundleCondition ([string]$Run.event -in $AllowedEvents) "$Label release run event is not an allowed production event"

    foreach ($requiredJob in $RequiredJobs) {
        $matches = @($Run.jobs | Where-Object { [string]$_.name -eq $requiredJob })
        Assert-ReleaseBundleCondition ($matches.Count -eq 1) "$Label release run must contain exactly one '$requiredJob' job"
        Assert-ReleaseBundleCondition (
            [string]$matches[0].status -eq "completed" -and
            [string]$matches[0].conclusion -eq "success"
        ) "$Label release job '$requiredJob' did not complete successfully"
    }
}

function Assert-AcademyProductionReleaseBundle {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][object]$Evidence)

    Assert-ReleaseBundleCondition ([int]$Evidence.SchemaVersion -eq 1) "schemaVersion must be 1"
    $backend = $Evidence.Backend
    $frontend = $Evidence.Frontend
    $backendSha = [string]$backend.ExpectedSha
    $frontendSha = [string]$frontend.ExpectedSha
    Assert-ReleaseBundleCondition ($backendSha -match '^[0-9a-f]{40}$') "backend expected SHA must be a full lowercase commit SHA"
    Assert-ReleaseBundleCondition ($frontendSha -match '^[0-9a-f]{40}$') "frontend expected SHA must be a full lowercase commit SHA"

    Assert-ReleaseBundleCondition ([bool]$backend.IsAncestorOfOriginMain) "backend SHA is not contained in current origin/main"
    Assert-ReleaseBundleCondition ([bool]$frontend.IsAncestorOfOriginMain) "frontend SHA is not contained in current origin/main"
    Assert-ReleaseBundleCondition ([int]$backend.PendingDeploymentsCount -eq 0) "backend release run still has a pending deployment"
    Assert-ReleaseBundleCondition ([int]$frontend.PendingDeploymentsCount -eq 0) "frontend release run still has a pending deployment"

    Assert-ReleaseRun `
        -Run $backend.Run `
        -ExpectedSha $backendSha `
        -ExpectedWorkflow "V1 Build and Push latest (OIDC)" `
        -AllowedEvents @("push", "workflow_dispatch") `
        -RequiredJobs @("Verify deployment", "Release shared production mutation lock") `
        -Label "backend"
    Assert-ReleaseRun `
        -Run $frontend.Run `
        -ExpectedSha $frontendSha `
        -ExpectedWorkflow "Frontend Quality Gate" `
        -AllowedEvents @("push") `
        -RequiredJobs @("Deploy to Cloudflare Pages", "E2E 왕복 테스트 + tenant availability") `
        -Label "frontend"

    $manifest = $backend.Manifest
    Assert-ReleaseBundleCondition ([int]$manifest.schemaVersion -eq 1) "backend manifest schemaVersion must be 1"
    Assert-ReleaseBundleCondition ([string]$manifest.status -eq "successful") "backend manifest status is not successful"
    $manifestCompleteProperty = $manifest.PSObject.Properties['complete']
    Assert-ReleaseBundleCondition (
        $null -ne $manifestCompleteProperty -and
        $manifestCompleteProperty.Value -is [bool] -and
        $manifestCompleteProperty.Value -eq $true
    ) "backend manifest complete must be the Boolean true"
    Assert-ReleaseBundleCondition ([string]$manifest.gitSha -match '^[0-9a-f]{40}$') "backend manifest SHA is not a full lowercase commit SHA"
    Assert-ReleaseBundleCondition ([bool]$backend.ManifestShaIsAncestorOfOriginMain) "backend manifest SHA is not contained in current origin/main"
    Assert-ReleaseBundleCondition ([bool]$backend.ManifestContainsExpectedSha) "backend manifest SHA does not contain the expected SHA"
    Assert-ReleaseBundleCondition (-not [bool]$backend.Lock.Active) "backend deployment lock is active for owner '$($backend.Lock.Owner)'"

    $liveVersions = @($frontend.LiveVersions)
    Assert-ReleaseBundleCondition ($liveVersions.Count -gt 0) "frontend live version evidence is missing"
    foreach ($liveVersion in $liveVersions) {
        Assert-ReleaseBundleCondition ([string]$liveVersion.Version -match '^[0-9a-f]{40}$') "frontend live version at '$($liveVersion.Url)' is not a full commit SHA"
        Assert-ReleaseBundleCondition ([bool]$liveVersion.IsAncestorOfOriginMain) "frontend live version at '$($liveVersion.Url)' is not contained in current origin/main"
        Assert-ReleaseBundleCondition ([bool]$liveVersion.IncludesExpectedSha) "frontend live version at '$($liveVersion.Url)' does not contain the expected SHA"
    }

    return [pscustomobject]@{
        Passed = $true
        BackendSha = $backendSha
        BackendRunId = [long]$backend.Run.databaseId
        FrontendSha = $frontendSha
        FrontendRunId = [long]$frontend.Run.databaseId
        FrontendVersionUrls = @($liveVersions | ForEach-Object { [string]$_.Url })
    }
}
