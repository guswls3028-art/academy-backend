[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet("Inspect", "Start", "Sync", "Close")]
    [string]$Action = "Inspect",
    [string]$Session = "",
    [ValidateSet("backend", "frontend", "both")]
    [string]$Repository = "both",
    [string]$WorkspaceRoot = "",
    [switch]$SkipFetch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $WorkspaceRoot) {
    $scriptRepository = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $commonDirValue = @(& git -C $scriptRepository rev-parse --git-common-dir 2>&1)
    if ($LASTEXITCODE -ne 0 -or $commonDirValue.Count -ne 1) {
        throw "Cannot resolve the canonical backend Git directory from $scriptRepository"
    }
    $commonDir = [string]$commonDirValue[0]
    if (-not [IO.Path]::IsPathRooted($commonDir)) {
        $commonDir = Join-Path $scriptRepository $commonDir
    }
    $commonDir = [IO.Path]::GetFullPath($commonDir)
    if ((Split-Path -Leaf $commonDir) -ne ".git") {
        throw "Expected the backend common Git directory to end in .git: $commonDir"
    }
    $canonicalBackend = Split-Path -Parent $commonDir
    $WorkspaceRoot = Split-Path -Parent $canonicalBackend
} else {
    $WorkspaceRoot = (Resolve-Path -LiteralPath $WorkspaceRoot).Path
}

function Invoke-GitChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = @(& git -C $Root @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git -C $Root $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return $output
}

function Test-GitSuccess {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & git -C $Root @Arguments *> $null
    return $LASTEXITCODE -eq 0
}

function Get-RepositoryNames {
    if ($Repository -eq "both") { return @("backend", "frontend") }
    return @($Repository)
}

function Get-RepositoryRoot([string]$Name) {
    $root = Join-Path $WorkspaceRoot $Name
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "Academy repository root does not exist: $root"
    }
    [void](Invoke-GitChecked -Root $root -Arguments @("rev-parse", "--show-toplevel"))
    return (Resolve-Path -LiteralPath $root).Path
}

function Update-MainReference([string]$Root) {
    if ($SkipFetch) { return }
    [void](Invoke-GitChecked -Root $Root -Arguments @(
        "fetch",
        "--no-tags",
        "--prune",
        "origin",
        "+refs/heads/main:refs/remotes/origin/main"
    ))
}

function Assert-SessionName {
    if ($Session -notmatch '^[a-z0-9][a-z0-9-]{2,47}$') {
        throw "Session must be a 3-48 character lowercase slug using letters, numbers, and hyphens."
    }
}

function Get-BranchName([string]$Root) {
    $branch = @(& git -C $Root symbolic-ref --quiet --short HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and $branch.Count -eq 1) {
        return [string]$branch[0]
    }
    if ($LASTEXITCODE -eq 1) { return "(detached)" }
    throw "Cannot resolve the current branch for worktree: $Root"
}

function Get-WorktreePaths([string]$Root) {
    $paths = [System.Collections.Generic.List[string]]::new()
    foreach ($line in @(Invoke-GitChecked -Root $Root -Arguments @("worktree", "list", "--porcelain"))) {
        if ([string]$line -like "worktree *") {
            [void]$paths.Add(([string]$line).Substring(9))
        }
    }
    return $paths
}

function Get-IntegrationState([string]$Root) {
    if (Test-GitSuccess -Root $Root -Arguments @(
        "merge-base", "--is-ancestor", "HEAD", "origin/main"
    )) {
        return "ancestor"
    }
    $cherry = @(Invoke-GitChecked -Root $Root -Arguments @(
        "cherry", "origin/main", "HEAD"
    ))
    $unique = @($cherry | Where-Object { [string]$_ -like "+ *" })
    if ($cherry.Count -gt 0 -and $unique.Count -eq 0) {
        return "patch-equivalent"
    }
    return "unmerged"
}

function Invoke-Inspect {
    $total = 0
    $dirty = 0
    foreach ($name in Get-RepositoryNames) {
        $root = Get-RepositoryRoot $name
        Update-MainReference $root
        foreach ($path in Get-WorktreePaths $root) {
            $total++
            $status = @(Invoke-GitChecked -Root $path -Arguments @(
                "status", "--porcelain=v1", "--untracked-files=normal"
            ))
            if ($status.Count -gt 0) { $dirty++ }
            $branch = Get-BranchName $path
            $relation = @(
                Invoke-GitChecked -Root $path -Arguments @(
                    "rev-list", "--left-right", "--count", "origin/main...HEAD"
                )
            )[0] -split '\s+'
            $integration = Get-IntegrationState $path
            Write-Output (
                'SESSION_WORKTREE_STATUS repo={0} path="{1}" branch={2} dirty={3} behind={4} ahead={5} integration={6}' -f
                $name,
                $path,
                $branch,
                $status.Count,
                $relation[0],
                $relation[1],
                $integration
            )
        }
    }
    Write-Output "SESSION_WORKTREE_SUMMARY total=$total dirty=$dirty"
}

function Invoke-Start {
    Assert-SessionName
    $names = @(Get-RepositoryNames)
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $sessionRoot = Join-Path $WorkspaceRoot "_worktrees\sessions\$Session"
    $plans = [System.Collections.Generic.List[object]]::new()

    foreach ($name in $names) {
        $root = Get-RepositoryRoot $name
        Update-MainReference $root
        $path = Join-Path $sessionRoot $name
        $branch = "codex/$Session-$name-$stamp"
        if (Test-Path -LiteralPath $path) {
            throw "Session worktree path already exists: $path"
        }
        if (Test-GitSuccess -Root $root -Arguments @(
            "show-ref", "--verify", "--quiet", "refs/heads/$branch"
        )) {
            throw "Session branch already exists: $branch"
        }
        [void]$plans.Add([pscustomobject]@{
            Name = $name
            Root = $root
            Path = $path
            Branch = $branch
        })
    }

    foreach ($plan in $plans) {
        if ($PSCmdlet.ShouldProcess($plan.Path, "create isolated Academy session worktree")) {
            if (-not (Test-Path -LiteralPath $sessionRoot)) {
                [void](New-Item -ItemType Directory -Path $sessionRoot -Force)
            }
            [void](Invoke-GitChecked -Root $plan.Root -Arguments @(
                "worktree", "add", "-b", $plan.Branch, $plan.Path, "origin/main"
            ))
            Write-Output (
                'SESSION_WORKTREE_CREATED repo={0} path="{1}" branch={2} base={3}' -f
                $plan.Name,
                $plan.Path,
                $plan.Branch,
                (@(Invoke-GitChecked -Root $plan.Path -Arguments @("rev-parse", "HEAD"))[0])
            )
        }
    }
}

function Invoke-Sync {
    $plans = [System.Collections.Generic.List[object]]::new()
    foreach ($name in Get-RepositoryNames) {
        $root = Get-RepositoryRoot $name
        Update-MainReference $root
        $status = @(Invoke-GitChecked -Root $root -Arguments @(
            "status", "--porcelain=v1", "--untracked-files=normal"
        ))
        if ($status.Count -gt 0) {
            throw "Canonical $name must be clean before sync. Preserve its changes first."
        }
        $branch = Get-BranchName $root
        if ($branch -ne "main") {
            throw "Canonical $name must be on main before sync; actual=$branch"
        }
        if (-not (Test-GitSuccess -Root $root -Arguments @(
            "merge-base", "--is-ancestor", "HEAD", "origin/main"
        ))) {
            throw "Canonical $name diverged from origin/main; refusing a non-fast-forward sync."
        }
        [void]$plans.Add([pscustomobject]@{ Name = $name; Root = $root })
    }

    foreach ($plan in $plans) {
        if ($PSCmdlet.ShouldProcess($plan.Root, "fast-forward canonical main to origin/main")) {
            [void](Invoke-GitChecked -Root $plan.Root -Arguments @(
                "merge", "--ff-only", "origin/main"
            ))
            Write-Output (
                'SESSION_CANONICAL_SYNCED repo={0} sha={1}' -f
                $plan.Name,
                (@(Invoke-GitChecked -Root $plan.Root -Arguments @("rev-parse", "HEAD"))[0])
            )
        }
    }
}

function Invoke-Close {
    Assert-SessionName
    $sessionRoot = Join-Path $WorkspaceRoot "_worktrees\sessions\$Session"
    $plans = [System.Collections.Generic.List[object]]::new()

    foreach ($name in Get-RepositoryNames) {
        $root = Get-RepositoryRoot $name
        Update-MainReference $root
        $path = Join-Path $sessionRoot $name
        if (-not (Test-Path -LiteralPath $path -PathType Container)) {
            throw "Session worktree does not exist: $path"
        }
        $registered = @(@(Get-WorktreePaths $root) | Where-Object {
            [string]::Equals(
                [IO.Path]::GetFullPath($_),
                [IO.Path]::GetFullPath($path),
                [StringComparison]::OrdinalIgnoreCase
            )
        })
        if ($registered.Count -ne 1) {
            throw "Session path is not the exact registered $name worktree: $path"
        }
        $branch = Get-BranchName $path
        if ($branch -notlike "codex/$Session-$name-*") {
            throw "Refusing to close a worktree owned by another session: $path ($branch)"
        }
        $status = @(Invoke-GitChecked -Root $path -Arguments @(
            "status", "--porcelain=v1", "--untracked-files=normal"
        ))
        if ($status.Count -gt 0) {
            throw "Session worktree is dirty and must be committed or explicitly handed off: $path"
        }
        $integration = Get-IntegrationState $path
        if ($integration -eq "unmerged") {
            throw "Session branch is not merged into origin/main and will be preserved: $branch"
        }
        [void]$plans.Add([pscustomobject]@{
            Name = $name
            Root = $root
            Path = $path
            Branch = $branch
            Integration = $integration
        })
    }

    foreach ($plan in $plans) {
        if ($PSCmdlet.ShouldProcess($plan.Path, "remove merged clean Academy session worktree")) {
            [void](Invoke-GitChecked -Root $plan.Root -Arguments @(
                "worktree", "remove", $plan.Path
            ))
            $deleteMode = if ($plan.Integration -eq "ancestor") { "-d" } else { "-D" }
            [void](Invoke-GitChecked -Root $plan.Root -Arguments @(
                "branch", $deleteMode, $plan.Branch
            ))
            Write-Output (
                "SESSION_WORKTREE_CLOSED repo=$($plan.Name) branch=$($plan.Branch) integration=$($plan.Integration)"
            )
        }
    }

    if (
        (Test-Path -LiteralPath $sessionRoot -PathType Container) -and
        @(Get-ChildItem -LiteralPath $sessionRoot -Force).Count -eq 0
    ) {
        Remove-Item -LiteralPath $sessionRoot
    }
}

switch ($Action) {
    "Inspect" { Invoke-Inspect }
    "Start" { Invoke-Start }
    "Sync" { Invoke-Sync }
    "Close" { Invoke-Close }
}
