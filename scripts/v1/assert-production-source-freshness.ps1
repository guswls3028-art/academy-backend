# Refuse a manual production mutation from a dirty, detached, stale, or
# divergent checkout. The release manifest may trail HEAD, but it must describe
# a complete successful release whose commit is an ancestor of current main.
[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$RemoteName = "origin",
    [ValidatePattern('^[A-Za-z0-9._/-]+$')]
    [string]$BranchName = "main",
    [switch]$SkipFetch = $false
)

$ErrorActionPreference = "Stop"

function Invoke-GitChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = @(& git -C $RepoRoot @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return $output
}

if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
    throw "Production source root does not exist: $RepoRoot"
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$gitTop = [string](Invoke-GitChecked -Arguments @("rev-parse", "--show-toplevel") | Select-Object -First 1)
$gitTop = (Resolve-Path -LiteralPath $gitTop).Path
if (-not [string]::Equals($gitTop, $RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Production source must be the backend Git root. expected=$RepoRoot actual=$gitTop"
}

$statusLines = @(Invoke-GitChecked -Arguments @(
    "status",
    "--porcelain=v1",
    "--untracked-files=normal"
))
$status = ($statusLines -join "`n").Trim()
if ($status) {
    throw "Production source must be clean. Preserve or commit all changes before deployment."
}

$branch = [string](Invoke-GitChecked -Arguments @("symbolic-ref", "--quiet", "--short", "HEAD") | Select-Object -First 1)
if ($branch -ne $BranchName) {
    throw "Production source must be branch '$BranchName'; actual='$branch'."
}

[void](Invoke-GitChecked -Arguments @("remote", "get-url", $RemoteName))
if (-not $SkipFetch) {
    [void](Invoke-GitChecked -Arguments @(
        "fetch",
        "--no-tags",
        "--prune",
        $RemoteName,
        "+refs/heads/${BranchName}:refs/remotes/${RemoteName}/${BranchName}"
    ))
}

$headSha = [string](Invoke-GitChecked -Arguments @("rev-parse", "HEAD") | Select-Object -First 1)
$remoteSha = [string](Invoke-GitChecked -Arguments @("rev-parse", "refs/remotes/${RemoteName}/${BranchName}") | Select-Object -First 1)
if ($headSha -ne $remoteSha) {
    throw "Production source is not the exact latest ${RemoteName}/${BranchName}. local=$headSha remote=$remoteSha"
}

$manifestPath = Join-Path $RepoRoot "docs\reports\release-manifest.latest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Successful release manifest is missing: $manifestPath"
}
try {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    throw "Successful release manifest is not valid JSON: $manifestPath"
}
$manifestSha = [string]$manifest.gitSha
if (
    [int]$manifest.schemaVersion -ne 1 -or
    [string]$manifest.status -ne "successful" -or
    -not [bool]$manifest.complete -or
    $manifestSha -notmatch '^[0-9a-f]{40}$'
) {
    throw "Release manifest must be complete, successful, schemaVersion=1, and contain a full git SHA."
}
[void](Invoke-GitChecked -Arguments @("merge-base", "--is-ancestor", $manifestSha, $headSha))

Write-Host (
    "PRODUCTION_SOURCE_FRESHNESS_PASS branch={0} head={1} release={2}" -f
    $BranchName,
    $headSha,
    $manifestSha
) -ForegroundColor Green
