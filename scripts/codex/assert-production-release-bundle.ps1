[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$BackendSha,
    [Parameter(Mandatory = $true)][ValidateRange(1, [long]::MaxValue)][long]$BackendRunId,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$FrontendSha,
    [Parameter(Mandatory = $true)][ValidateRange(1, [long]::MaxValue)][long]$FrontendRunId,
    [string]$BackendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$FrontendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\frontend")).Path,
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$BackendRepository = "guswls3028-art/academy-backend",
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$FrontendRepository = "guswls3028-art/academy-frontend",
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$AwsProfile = "",
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$AwsRegion = "ap-northeast-2",
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$LockTable = "academy-v1-video-job-lock",
    [ValidatePattern('^https://')][string[]]$FrontendVersionUrls = @("https://hakwonplus.com/version.json")
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "stability-contract.ps1")

function Invoke-ExternalJson {
    param([string]$File, [string[]]$Arguments)
    $output = @(& $File @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "$File $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    try { return ($output -join "`n") | ConvertFrom-Json }
    catch { throw "$File returned invalid JSON." }
}

function Assert-GitRootAndRemote {
    param([string]$Root, [string]$Repository)
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $top = @(& git -C $resolved rev-parse --show-toplevel 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Not a Git repository: $resolved" }
    $topPath = (Resolve-Path -LiteralPath ([string]$top[0])).Path
    if (-not [string]::Equals($resolved, $topPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Expected repository root. expected=$resolved actual=$topPath"
    }
    $remote = [string](@(& git -C $resolved remote get-url origin 2>&1)[0])
    if ($LASTEXITCODE -ne 0 -or $remote -notmatch ("github\.com[:/]" + [regex]::Escape($Repository) + "(?:\.git)?$")) {
        throw "Repository origin does not match $Repository"
    }
    @(& git -C $resolved fetch --no-tags --prune origin "+refs/heads/main:refs/remotes/origin/main" 2>&1) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to fetch $Repository origin/main" }
    return $resolved
}

function Test-GitAncestorOf {
    param([string]$Root, [string]$Ancestor, [string]$Descendant)
    & git -C $Root merge-base --is-ancestor $Ancestor $Descendant 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Test-GitAncestorOfOriginMain {
    param([string]$Root, [string]$Sha)
    return (Test-GitAncestorOf -Root $Root -Ancestor $Sha -Descendant "origin/main")
}

$BackendRoot = Assert-GitRootAndRemote -Root $BackendRoot -Repository $BackendRepository
$FrontendRoot = Assert-GitRootAndRemote -Root $FrontendRoot -Repository $FrontendRepository
$backendRun = Invoke-ExternalJson "gh" @(
    "run", "view", [string]$BackendRunId, "-R", $BackendRepository,
    "--json", "databaseId,status,conclusion,event,headBranch,headSha,workflowName,url,jobs"
)
$frontendRun = Invoke-ExternalJson "gh" @(
    "run", "view", [string]$FrontendRunId, "-R", $FrontendRepository,
    "--json", "databaseId,status,conclusion,event,headBranch,headSha,workflowName,url,jobs"
)
$backendPending = @(Invoke-ExternalJson "gh" @("api", "repos/$BackendRepository/actions/runs/$BackendRunId/pending_deployments"))
$frontendPending = @(Invoke-ExternalJson "gh" @("api", "repos/$FrontendRepository/actions/runs/$FrontendRunId/pending_deployments"))

$manifestJson = @(& git -C $BackendRoot show "origin/main:docs/reports/release-manifest.latest.json" 2>&1)
if ($LASTEXITCODE -ne 0) { throw "Failed to read the backend release manifest from origin/main." }
try { $manifest = ($manifestJson -join "`n") | ConvertFrom-Json }
catch { throw "Backend release manifest on origin/main is invalid JSON." }

$lockArgs = @(
    "dynamodb", "get-item",
    "--table-name", $LockTable,
    "--key", '{"videoId":{"S":"__deployment_control_v2__"}}',
    "--consistent-read",
    "--region", $AwsRegion,
    "--output", "json"
)
if ($AwsProfile) { $lockArgs += @("--profile", $AwsProfile) }
$lockReadback = Invoke-ExternalJson "aws" $lockArgs
$lock = Get-AcademyDeploymentLockState -LockReadback $lockReadback

$liveVersions = foreach ($url in $FrontendVersionUrls) {
    $separator = if ($url.Contains("?")) { "&" } else { "?" }
    $probeUrl = "$url${separator}release_bundle=$FrontendRunId-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"
    try { $body = Invoke-RestMethod -Uri $probeUrl -Method Get -TimeoutSec 20 }
    catch { throw "Frontend live version readback failed for $url`: $($_.Exception.Message)" }
    $liveVersion = [string]$body.version
    [pscustomobject]@{
        Url = $url
        Version = $liveVersion
        IncludesExpectedSha = (
            $liveVersion -match '^[0-9a-f]{40}$' -and
            (Test-GitAncestorOf -Root $FrontendRoot -Ancestor $FrontendSha -Descendant $liveVersion)
        )
        IsAncestorOfOriginMain = (
            $liveVersion -match '^[0-9a-f]{40}$' -and
            (Test-GitAncestorOfOriginMain -Root $FrontendRoot -Sha $liveVersion)
        )
    }
}

$manifestSha = [string]$manifest.gitSha

$evidence = [pscustomobject]@{
    SchemaVersion = 1
    Backend = [pscustomobject]@{
        ExpectedSha = $BackendSha
        IsAncestorOfOriginMain = Test-GitAncestorOfOriginMain -Root $BackendRoot -Sha $BackendSha
        PendingDeploymentsCount = $backendPending.Count
        ManifestContainsExpectedSha = (
            $manifestSha -match '^[0-9a-f]{40}$' -and
            (Test-GitAncestorOf -Root $BackendRoot -Ancestor $BackendSha -Descendant $manifestSha)
        )
        ManifestShaIsAncestorOfOriginMain = (
            $manifestSha -match '^[0-9a-f]{40}$' -and
            (Test-GitAncestorOfOriginMain -Root $BackendRoot -Sha $manifestSha)
        )
        Manifest = $manifest
        Lock = $lock
        Run = $backendRun
    }
    Frontend = [pscustomobject]@{
        ExpectedSha = $FrontendSha
        IsAncestorOfOriginMain = Test-GitAncestorOfOriginMain -Root $FrontendRoot -Sha $FrontendSha
        PendingDeploymentsCount = $frontendPending.Count
        LiveVersions = @($liveVersions)
        Run = $frontendRun
    }
}

$result = Assert-AcademyProductionReleaseBundle -Evidence $evidence
Write-Host (
    "ACADEMY_PRODUCTION_RELEASE_BUNDLE_PASS backend={0} backendRun={1} frontend={2} frontendRun={3}" -f
    $result.BackendSha,
    $result.BackendRunId,
    $result.FrontendSha,
    $result.FrontendRunId
) -ForegroundColor Green
$result | ConvertTo-Json -Depth 6
