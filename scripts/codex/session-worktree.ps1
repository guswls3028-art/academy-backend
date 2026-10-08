[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet("Inspect", "Start", "Sync", "Close")]
    [string]$Action = "Inspect",
    [string]$Session = "",
    [ValidateSet("backend", "frontend", "both")]
    [string]$Repository = "both",
    [string]$WorkspaceRoot = "",
    [switch]$SkipFetch,
    [switch]$AllowLowDisk,
    [string]$ExpectedBranch = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7 is required for safe directory-link cleanup."
}

if ($ExpectedBranch -and ($Action -ne "Close" -or $Repository -eq "both")) {
    throw "ExpectedBranch requires Close and one exact repository."
}

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

function Get-IntegrationState([string]$Root, [string]$MainSha = "", [string]$HeadSha = "") {
    if (-not $MainSha) {
        $MainSha = @(Invoke-GitChecked -Root $Root -Arguments @(
            "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"
        ))[0]
    }
    if (-not $HeadSha) {
        $HeadSha = @(Invoke-GitChecked -Root $Root -Arguments @(
            "rev-parse", "--verify", "HEAD^{commit}"
        ))[0]
    }
    if (Test-GitSuccess -Root $Root -Arguments @(
        "merge-base", "--is-ancestor", $HeadSha, $MainSha
    )) {
        return "ancestor"
    }
    # git cherry omits merges, including their potentially unique resolutions.
    $exclusiveMerges = @(Invoke-GitChecked -Root $Root -Arguments @(
        "rev-list", "--merges", "--max-count=1", "$MainSha..$HeadSha"
    ))
    if ($exclusiveMerges.Count -gt 0) { return "unmerged" }
    $cherry = @(Invoke-GitChecked -Root $Root -Arguments @(
        "cherry", $MainSha, $HeadSha
    ))
    $unique = @($cherry | Where-Object { [string]$_ -like "+ *" })
    if ($cherry.Count -gt 0 -and $unique.Count -eq 0) {
        return "patch-equivalent"
    }
    # An aggregate squash can preserve the full tree without matching each patch.
    $trees = @(Invoke-GitChecked -Root $Root -Arguments @(
        "rev-parse", "$HeadSha^{tree}", "$MainSha^{tree}"
    ))
    if ($trees.Count -eq 2 -and $trees[0] -ceq $trees[1]) {
        return "tree-equivalent"
    }
    return "unmerged"
}

function Invoke-Inspect {
    if ($Session) { Assert-SessionName }
    $total = 0
    $dirty = 0
    foreach ($name in Get-RepositoryNames) {
        $root = Get-RepositoryRoot $name
        Update-MainReference $root
        $mainSha = @(Invoke-GitChecked -Root $root -Arguments @(
            "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"
        ))[0]
        foreach ($path in Get-WorktreePaths $root) {
            if ($Session) {
                $expected = Join-Path $WorkspaceRoot "_worktrees\sessions\$Session\$name"
                if (-not [string]::Equals(
                    [IO.Path]::GetFullPath($path),
                    [IO.Path]::GetFullPath($expected),
                    [StringComparison]::OrdinalIgnoreCase
                )) { continue }
            }
            $total++
            $status = @(Invoke-GitChecked -Root $path -Arguments @(
                "status", "--porcelain=v1", "--untracked-files=normal"
            ))
            if ($status.Count -gt 0) { $dirty++ }
            $branch = Get-BranchName $path
            $relation = @(
                Invoke-GitChecked -Root $path -Arguments @(
                    "rev-list", "--left-right", "--count", "$mainSha...HEAD"
                )
            )[0] -split '\s+'
            # No exclusive HEAD commits already proves ancestry. Reuse that
            # graph result for the common merged case instead of spawning
            # three more Git processes for every old worktree.
            $integration = if ([int]$relation[1] -eq 0) {
                "ancestor"
            } else {
                Get-IntegrationState -Root $path -MainSha $mainSha
            }
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
    $volume = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($WorkspaceRoot))
    if ($volume.AvailableFreeSpace -lt 10GB) {
        $message = 'Academy workspace disk has {0:N1} GB free; new local sessions require 10 GB. Use a Codespace or close completed sessions.' -f ($volume.AvailableFreeSpace / 1GB)
        if (-not $AllowLowDisk) { throw $message }
        Write-Warning "$message AllowLowDisk permits only lightweight recovery work; keep installs and builds remote."
    } elseif ($volume.AvailableFreeSpace -lt 20GB) {
        Write-Warning "Academy workspace has less than 20 GB free. Use the existing Codespace for dependency installs, browser tests and builds; close completed sessions before starting more local work."
    }
    $names = @(Get-RepositoryNames)
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $sessionRoot = Join-Path $WorkspaceRoot "_worktrees\sessions\$Session"
    $artifactRoot = Join-Path $WorkspaceRoot "_artifacts\sessions\$Session"
    $scratchRoot = Join-Path $artifactRoot "scratch"
    Assert-StoragePath -Path $scratchRoot -Parent $WorkspaceRoot
    if (Test-Path -LiteralPath $scratchRoot) {
        Assert-ScratchOwner -Path $scratchRoot
    }
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
            Assert-StoragePath -Path $scratchRoot -Parent $WorkspaceRoot
            [void](New-Item -ItemType Directory -Path $scratchRoot -Force)
            if (-not (Test-Path -LiteralPath (Join-Path $scratchRoot ".academy-scratch.json"))) {
                @{ version = 1; session = $Session; workspace = [IO.Path]::GetFullPath($WorkspaceRoot) } |
                    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $scratchRoot ".academy-scratch.json") -Encoding utf8
            }
            Write-Output ('SESSION_ARTIFACTS durable="{0}" scratch="{1}" scratchRetention=until-session-close' -f $artifactRoot, $scratchRoot)
        }
    }
}

function Assert-StoragePath {
    param([string]$Path, [string]$Parent)
    $full = [IO.Path]::GetFullPath($Path)
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Storage path escapes the intended workspace: $Path"
    }
    $check = $full
    while ($check -and $check.Length -ge $parentFull.TrimEnd('\', '/').Length) {
        if (Test-Path -LiteralPath $check) {
            if ((Get-Item -LiteralPath $check -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Storage path contains a link; preserving it: $Path"
            }
        }
        $check = Split-Path -Parent $check
    }
}

function Assert-ScratchOwner {
    param([string]$Path)
    $marker = Join-Path $Path ".academy-scratch.json"
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        throw "Unmanaged scratch data is preserved; missing ownership marker: $Path"
    }
    Assert-StoragePath -Path $marker -Parent $WorkspaceRoot
    $owner = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if ($owner.version -ne 1 -or $owner.session -cne $Session -or
        $owner.workspace -cne [IO.Path]::GetFullPath($WorkspaceRoot)) {
        throw "Scratch ownership mismatch; preserving data: $Path"
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

function Assert-ClosePlanUnchanged($Plan, [switch]$CachesRemoved) {
    $registered = @(@(Get-WorktreePaths $Plan.Root) | Where-Object {
        [string]::Equals(
            [IO.Path]::GetFullPath($_),
            [IO.Path]::GetFullPath($Plan.Path),
            [StringComparison]::OrdinalIgnoreCase
        )
    })
    if ($registered.Count -ne 1 -or -not (Test-Path -LiteralPath $Plan.Path -PathType Container)) {
        throw "Session registration changed after close preflight; preserving worktree: $($Plan.Path)"
    }
    $branch = Get-BranchName $Plan.Path
    $headSha = @(Invoke-GitChecked -Root $Plan.Path -Arguments @(
        "rev-parse", "--verify", "HEAD^{commit}"
    ))[0]
    if ($branch -cne $Plan.Branch -or $headSha -cne $Plan.HeadSha) {
        throw "Session branch or HEAD changed after close preflight; preserving worktree: $($Plan.Path)"
    }
    $status = @(Invoke-GitChecked -Root $Plan.Path -Arguments @(
        "status", "--ignored", "--porcelain=v1", "--untracked-files=normal"
    ))
    $expectedStatus = @()
    if (-not $CachesRemoved) {
        $expectedStatus = @($Plan.IgnoredPaths | ForEach-Object { "!! $_" })
    }
    if (($status -join "`n") -cne ($expectedStatus -join "`n")) {
        throw "Session clean/ignored state changed after close preflight; preserving worktree: $($Plan.Path)"
    }
}

function Invoke-Close {
    Assert-SessionName
    $sessionRoot = Join-Path $WorkspaceRoot "_worktrees\sessions\$Session"
    $scratchRoot = Join-Path $WorkspaceRoot "_artifacts\sessions\$Session\scratch"
    Assert-StoragePath -Path $scratchRoot -Parent $WorkspaceRoot
    if (Test-Path -LiteralPath $scratchRoot) { Assert-ScratchOwner -Path $scratchRoot }
    $plans = [System.Collections.Generic.List[object]]::new()

    foreach ($name in Get-RepositoryNames) {
        $root = Get-RepositoryRoot $name
        Update-MainReference $root
        $mainSha = @(Invoke-GitChecked -Root $root -Arguments @(
            "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"
        ))[0]
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
        if ($ExpectedBranch) {
            if ($branch -cne $ExpectedBranch -or $branch -cnotlike "codex/*") {
                throw "ExpectedBranch does not match the exact owned Codex branch: $path ($branch)"
            }
        } elseif ($branch -notlike "codex/$Session-$name-*") {
            throw "Refusing to close a worktree owned by another session: $path ($branch)"
        }
        $status = @(Invoke-GitChecked -Root $path -Arguments @(
            "status", "--porcelain=v1", "--untracked-files=normal"
        ))
        if ($status.Count -gt 0) {
            throw "Session worktree is dirty and must be committed or explicitly handed off: $path"
        }
        $headSha = @(Invoke-GitChecked -Root $path -Arguments @(
            "rev-parse", "--verify", "HEAD^{commit}"
        ))[0]
        $integration = Get-IntegrationState -Root $path -MainSha $mainSha -HeadSha $headSha
        if ($integration -eq "unmerged") {
            throw "Session branch is not merged into origin/main and will be preserved: $branch"
        }
        $ignored = @(Invoke-GitChecked -Root $path -Arguments @(
            "status", "--ignored", "--porcelain=v1", "--untracked-files=normal"
        ) | Where-Object { [string]$_ -like "!! *" })
        $ignoredPaths = @($ignored | ForEach-Object { ([string]$_).Substring(3) })
        $unexpected = @($ignoredPaths | Where-Object {
            if ($name -eq "frontend") {
                $_ -notin @("node_modules/", "dist/", ".vite/")
            } else {
                $_ -notin @(".pytest_cache/", ".ruff_cache/") -and
                $_ -notmatch '(^|/)__pycache__/$'
            }
        })
        if ($unexpected.Count -gt 0) {
            throw "Session has ignored local data and will be preserved: $path ($($unexpected -join ', '))"
        }
        [void]$plans.Add([pscustomobject]@{
            Name = $name
            Root = $root
            Path = $path
            Branch = $branch
            MainSha = $mainSha
            HeadSha = $headSha
            Integration = $integration
            IgnoredPaths = $ignoredPaths
        })
    }

    foreach ($plan in $plans) {
        if ($PSCmdlet.ShouldProcess($plan.Path, "remove merged clean Academy session worktree")) {
            Assert-ClosePlanUnchanged $plan
            if ($plan.IgnoredPaths.Count -gt 0) {
                $cleanArguments = @("clean", "-fdX", "--") + @($plan.IgnoredPaths)
                [void](Invoke-GitChecked -Root $plan.Path -Arguments $cleanArguments)
                Assert-ClosePlanUnchanged $plan -CachesRemoved
            }
            [void](Invoke-GitChecked -Root $plan.Root -Arguments @(
                "worktree", "remove", $plan.Path
            ))
            # Compare and delete atomically: preserve a tip advanced after the
            # last check, without depending on a possibly stale canonical HEAD.
            [void](Invoke-GitChecked -Root $plan.Root -Arguments @(
                "update-ref", "--no-deref", "-d", "refs/heads/$($plan.Branch)", $plan.HeadSha
            ))
            Write-Output (
                "SESSION_WORKTREE_CLOSED repo=$($plan.Name) branch=$($plan.Branch) integration=$($plan.Integration) head=$($plan.HeadSha) main=$($plan.MainSha)"
            )
        }
    }

    if (
        (Test-Path -LiteralPath $sessionRoot -PathType Container) -and
        @(Get-ChildItem -LiteralPath $sessionRoot -Force).Count -eq 0
    ) {
        Remove-Item -LiteralPath $sessionRoot
        Assert-StoragePath -Path $scratchRoot -Parent $WorkspaceRoot
        if (Test-Path -LiteralPath $scratchRoot -PathType Container) {
            Assert-ScratchOwner -Path $scratchRoot
            if ($PSCmdlet.ShouldProcess($scratchRoot, "remove this closed session's disposable scratch outputs")) {
                # PowerShell 7 unlinks child reparse points without traversing their targets.
                Remove-Item -LiteralPath $scratchRoot -Recurse -Force -ErrorAction Stop
                if (Test-Path -LiteralPath $scratchRoot) {
                    throw "Closed-session scratch cleanup is incomplete: $scratchRoot"
                }
                Write-Output ('SESSION_SCRATCH_REMOVED path="{0}"' -f $scratchRoot)
            }
        }
    }
}

switch ($Action) {
    "Inspect" { Invoke-Inspect }
    "Start" { Invoke-Start }
    "Sync" { Invoke-Sync }
    "Close" { Invoke-Close }
}
