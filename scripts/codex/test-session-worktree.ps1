[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptUnderTest = Join-Path $PSScriptRoot "session-worktree.ps1"
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$fixtureRoot = Join-Path $tempBase ("academy-session-contract-" + [guid]::NewGuid().ToString("N"))

function Invoke-Git {
    param([string]$Root, [string[]]$Arguments)
    $output = @(& git -C $Root @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git -C $Root $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return $output
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Invoke-InterceptedClose {
    param([string]$Session, [hashtable]$State, [scriptblock]$BeforeGit)

    $gitExecutable = (Get-Command git -CommandType Application | Select-Object -First 1).Source
    # Intercept only Git call boundaries; execute the unmodified production script
    # and every Git operation against real, disposable fixture repositories.
    function git {
        $arguments = @($args)
        & $BeforeGit $arguments $State $gitExecutable
        & $gitExecutable @arguments
        Set-Variable -Name LASTEXITCODE -Value $LASTEXITCODE -Scope 1
    }
    & $scriptUnderTest -Action Close -Session $Session -Repository backend -WorkspaceRoot $fixtureRoot
}

try {
    [void](New-Item -ItemType Directory -Path $fixtureRoot)
    [void](New-Item -ItemType Directory -Path (Join-Path $fixtureRoot "remotes"))

    foreach ($name in @("backend", "frontend")) {
        $remote = Join-Path $fixtureRoot "remotes\$name.git"
        $seed = Join-Path $fixtureRoot "seed-$name"
        [void](& git init --bare $remote 2>&1)
        if ($LASTEXITCODE -ne 0) { throw "Failed to initialize bare fixture: $remote" }
        [void](New-Item -ItemType Directory -Path $seed)
        [void](Invoke-Git -Root $seed -Arguments @("init", "-b", "main"))
        [void](Invoke-Git -Root $seed -Arguments @("config", "user.name", "Academy Contract Test"))
        [void](Invoke-Git -Root $seed -Arguments @("config", "user.email", "academy-contract@example.invalid"))
        Set-Content -LiteralPath (Join-Path $seed "README.md") -Value $name -Encoding UTF8
        [void](Invoke-Git -Root $seed -Arguments @("add", "README.md"))
        [void](Invoke-Git -Root $seed -Arguments @("commit", "-m", "fixture initial"))
        [void](Invoke-Git -Root $seed -Arguments @("remote", "add", "origin", $remote))
        [void](Invoke-Git -Root $seed -Arguments @("push", "-u", "origin", "main"))
        [void](& git clone --branch main $remote (Join-Path $fixtureRoot $name) 2>&1)
        if ($LASTEXITCODE -ne 0) { throw "Failed to clone fixture repository: $name" }
        [void](Invoke-Git -Root (Join-Path $fixtureRoot $name) -Arguments @(
            "config", "user.name", "Academy Contract Test"
        ))
        [void](Invoke-Git -Root (Join-Path $fixtureRoot $name) -Arguments @(
            "config", "user.email", "academy-contract@example.invalid"
        ))
    }

    $freeBytes = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($fixtureRoot)).AvailableFreeSpace
    if ($freeBytes -lt 10GB) {
        $lowDiskRefused = $false
        try {
            & $scriptUnderTest `
                -Action Start `
                -Session low-disk-test `
                -Repository both `
                -WorkspaceRoot $fixtureRoot *> $null
        } catch {
            $lowDiskRefused = $_.Exception.Message.Contains("new local sessions require 10 GB")
        }
        Assert-True $lowDiskRefused "Start must refuse a new session below 10 GB."
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot "_worktrees\sessions\low-disk-test"))) "Low-disk refusal must not create a worktree."
    }

    & $scriptUnderTest `
        -Action Start `
        -Session dry-run-test `
        -Repository both `
        -WorkspaceRoot $fixtureRoot `
        -AllowLowDisk `
        -WhatIf *> $null
    Assert-True (
        -not (Test-Path -LiteralPath (Join-Path $fixtureRoot "_worktrees\sessions\dry-run-test"))
    ) "Start -WhatIf must not create a session directory."

    $startOutput = @(& $scriptUnderTest `
        -Action Start `
        -Session contract-test `
        -Repository both `
        -WorkspaceRoot $fixtureRoot `
        -AllowLowDisk)
    Assert-True (@($startOutput -match "SESSION_WORKTREE_CREATED").Count -gt 0) "Start did not create paired worktrees."

    $backendWorktree = Join-Path $fixtureRoot "_worktrees\sessions\contract-test\backend"
    $frontendWorktree = Join-Path $fixtureRoot "_worktrees\sessions\contract-test\frontend"
    Assert-True (Test-Path -LiteralPath $backendWorktree) "Backend session worktree is missing."
    Assert-True (Test-Path -LiteralPath $frontendWorktree) "Frontend session worktree is missing."
    $inspectCounts = @{ Git = 0; MergeBase = 0 }
    $gitExecutable = (Get-Command git -CommandType Application | Select-Object -First 1).Source
    $inspectOutput = @(& {
        function git {
            $arguments = @($args)
            $inspectCounts.Git++
            if ("merge-base" -in $arguments) { $inspectCounts.MergeBase++ }
            & $gitExecutable @arguments
            Set-Variable -Name LASTEXITCODE -Value $LASTEXITCODE -Scope 1
        }
        & $scriptUnderTest -Action Inspect -Session contract-test `
            -Repository both -WorkspaceRoot $fixtureRoot -SkipFetch
    })
    Assert-True (
        @($inspectOutput -match "SESSION_WORKTREE_STATUS").Count -eq 2
    ) "Scoped Inspect must report only the requested paired worktrees."
    Assert-True (@($inspectOutput -match "integration=ancestor").Count -eq 2) "Inspect must classify merged worktrees correctly."
    Assert-True ($inspectCounts.MergeBase -eq 0) "Merged inspection must reuse the ahead count instead of repeating ancestry queries."
    Assert-True ($inspectCounts.Git -le 12) "Paired merged inspection must not regress its Git subprocess budget."

    Set-Content -LiteralPath (Join-Path $backendWorktree "dirty.txt") -Value "dirty" -Encoding UTF8
    $dirtyRefused = $false
    try {
        & $scriptUnderTest `
            -Action Close `
            -Session contract-test `
            -Repository both `
            -WorkspaceRoot $fixtureRoot *> $null
    } catch {
        $dirtyRefused = $_.Exception.Message.Contains("dirty")
        if (-not $dirtyRefused) { Write-Output "UNEXPECTED_DIRTY_CLOSE_ERROR=$($_.Exception.Message)" }
    }
    Assert-True $dirtyRefused "Close must refuse a dirty worktree."
    Assert-True (Test-Path -LiteralPath $frontendWorktree) "Close preflight must preserve every paired worktree."
    Remove-Item -LiteralPath (Join-Path $backendWorktree "dirty.txt")

    Set-Content -LiteralPath (Join-Path $backendWorktree "feature.txt") -Value "feature" -Encoding UTF8
    [void](Invoke-Git -Root $backendWorktree -Arguments @("add", "feature.txt"))
    [void](Invoke-Git -Root $backendWorktree -Arguments @("commit", "-m", "fixture feature"))
    $unmergedInspect = @(& $scriptUnderTest -Action Inspect -Session contract-test `
        -Repository backend -WorkspaceRoot $fixtureRoot -SkipFetch)
    Assert-True (@($unmergedInspect -match "ahead=1 integration=unmerged").Count -eq 1) "Divergent inspection must retain full integration checks."
    $unmergedRefused = $false
    try {
        & $scriptUnderTest `
            -Action Close `
            -Session contract-test `
            -Repository both `
            -WorkspaceRoot $fixtureRoot *> $null
    } catch {
        $unmergedRefused = $_.Exception.Message.Contains("not merged")
        if (-not $unmergedRefused) { Write-Output "UNEXPECTED_UNMERGED_CLOSE_ERROR=$($_.Exception.Message)" }
    }
    Assert-True $unmergedRefused "Close must preserve a clean unmerged branch."
    Assert-True (Test-Path -LiteralPath $frontendWorktree) "Unmerged close must not partially remove paired worktrees."

    $backendRoot = Join-Path $fixtureRoot "backend"
    $backendBranch = @(Invoke-Git -Root $backendWorktree -Arguments @(
        "symbolic-ref", "--short", "HEAD"
    ))[0]
    [void](Invoke-Git -Root $backendRoot -Arguments @("merge", "--ff-only", $backendBranch))
    [void](Invoke-Git -Root $backendRoot -Arguments @("push", "origin", "main"))

    $frontendExclude = Join-Path $fixtureRoot "frontend\.git\info\exclude"
    Add-Content -LiteralPath $frontendExclude -Value "node_modules/`n.env.local" -Encoding UTF8
    $generatedDir = Join-Path $frontendWorktree "node_modules"
    [void](New-Item -ItemType Directory -Path $generatedDir)
    Set-Content -LiteralPath (Join-Path $generatedDir "package.txt") -Value "rebuildable" -Encoding UTF8
    $privateFile = Join-Path $frontendWorktree ".env.local"
    Set-Content -LiteralPath $privateFile -Value "preserve" -Encoding UTF8
    $ignoredRefused = $false
    try {
        & $scriptUnderTest `
            -Action Close `
            -Session contract-test `
            -Repository both `
            -WorkspaceRoot $fixtureRoot *> $null
    } catch {
        $ignoredRefused = $_.Exception.Message.Contains("ignored local data")
    }
    Assert-True $ignoredRefused "Close must refuse ignored local data."
    Assert-True (Test-Path -LiteralPath $backendWorktree) "Ignored-data preflight must preserve paired worktrees."
    Assert-True (Test-Path -LiteralPath $privateFile) "Close must preserve ignored local data."
    Remove-Item -LiteralPath $privateFile
    $generatedRefused = $false
    try {
        & $scriptUnderTest `
            -Action Close `
            -Session contract-test `
            -Repository both `
            -WorkspaceRoot $fixtureRoot *> $null
    } catch {
        $generatedRefused = $_.Exception.Message.Contains("ignored local data")
    }
    Assert-True $generatedRefused "Close must refuse generated ignored data before Git can leave an orphan."
    Assert-True (Test-Path -LiteralPath $backendWorktree) "Generated-data preflight must preserve paired worktrees."
    Remove-Item -LiteralPath $generatedDir -Recurse

    $backendExclude = Join-Path $fixtureRoot "backend\.git\info\exclude"
    Add-Content -LiteralPath $backendExclude -Value ".ruff_cache/" -Encoding UTF8
    $backendCache = Join-Path $backendWorktree ".ruff_cache"
    [void](New-Item -ItemType Directory -Path $backendCache)
    Set-Content -LiteralPath (Join-Path $backendCache "cache.txt") -Value "rebuildable" -Encoding UTF8

    & $scriptUnderTest -Action Close -Session contract-test -Repository both `
        -WorkspaceRoot $fixtureRoot -WhatIf *> $null
    Assert-True (Test-Path -LiteralPath $backendWorktree) "Close -WhatIf removed the backend worktree."
    Assert-True (Test-Path -LiteralPath $frontendWorktree) "Close -WhatIf removed the frontend worktree."
    Assert-True (Test-Path -LiteralPath (Join-Path $backendCache "cache.txt")) "Close -WhatIf removed ignored caches."
    Assert-True (
        @((Invoke-Git -Root $backendRoot -Arguments @("branch", "--list", $backendBranch))).Count -eq 1
    ) "Close -WhatIf removed the session branch."

    $closeOutput = @(& $scriptUnderTest `
        -Action Close `
        -Session contract-test `
        -Repository both `
        -WorkspaceRoot $fixtureRoot)
    Assert-True (@($closeOutput -match "SESSION_WORKTREE_CLOSED").Count -gt 0) "Close did not remove merged worktrees."
    Assert-True (-not (Test-Path -LiteralPath $backendWorktree)) "Backend worktree remains after close."
    Assert-True (-not (Test-Path -LiteralPath $frontendWorktree)) "Frontend worktree remains after close."

    [void](& $scriptUnderTest `
        -Action Start `
        -Session stale-main-test `
        -Repository backend `
        -WorkspaceRoot $fixtureRoot `
        -AllowLowDisk)
    $staleMainWorktree = Join-Path $fixtureRoot "_worktrees\sessions\stale-main-test\backend"
    Set-Content -LiteralPath (Join-Path $staleMainWorktree "remote-only.txt") -Value "remote" -Encoding UTF8
    [void](Invoke-Git -Root $staleMainWorktree -Arguments @("add", "remote-only.txt"))
    [void](Invoke-Git -Root $staleMainWorktree -Arguments @("commit", "-m", "fixture remote-only advance"))
    [void](Invoke-Git -Root $staleMainWorktree -Arguments @("push", "origin", "HEAD:main"))
    $canonicalBeforeClose = @(Invoke-Git -Root $backendRoot -Arguments @("rev-parse", "HEAD"))[0]
    $sessionBeforeClose = @(Invoke-Git -Root $staleMainWorktree -Arguments @("rev-parse", "HEAD"))[0]
    Assert-True (
        $canonicalBeforeClose -ne $sessionBeforeClose
    ) "Stale-main close fixture must leave canonical HEAD behind the merged session."
    $staleCloseOutput = @(& $scriptUnderTest `
        -Action Close `
        -Session stale-main-test `
        -Repository backend `
        -WorkspaceRoot $fixtureRoot)
    Assert-True (
        @($staleCloseOutput -match "integration=ancestor").Count -gt 0
    ) "Close failed when origin/main contained the branch but canonical main was stale."
    Assert-True (-not (Test-Path -LiteralPath $staleMainWorktree)) "Stale-main worktree remains after close."
    Assert-True (
        @((Invoke-Git -Root $backendRoot -Arguments @("branch", "--list", "codex/stale-main-test-backend-*"))).Count -eq 0
    ) "Stale-main local branch remains after close."
    [void](Invoke-Git -Root $backendRoot -Arguments @("merge", "--ff-only", "origin/main"))

    [void](& $scriptUnderTest `
        -Action Start `
        -Session patch-test `
        -Repository backend `
        -WorkspaceRoot $fixtureRoot `
        -AllowLowDisk)
    $patchWorktree = Join-Path $fixtureRoot "_worktrees\sessions\patch-test\backend"
    Set-Content -LiteralPath (Join-Path $patchWorktree "equivalent.txt") -Value "same patch" -Encoding UTF8
    [void](Invoke-Git -Root $patchWorktree -Arguments @("add", "equivalent.txt"))
    [void](Invoke-Git -Root $patchWorktree -Arguments @("commit", "-m", "fixture session patch"))
    Set-Content -LiteralPath (Join-Path $backendRoot "equivalent.txt") -Value "same patch" -Encoding UTF8
    [void](Invoke-Git -Root $backendRoot -Arguments @("add", "equivalent.txt"))
    [void](Invoke-Git -Root $backendRoot -Arguments @("commit", "-m", "fixture main equivalent"))
    [void](Invoke-Git -Root $backendRoot -Arguments @("push", "origin", "main"))
    $patchMainSha = @(Invoke-Git -Root $backendRoot -Arguments @("rev-parse", "HEAD"))[0]
    $patchState = @{
        Root = $backendRoot
        MainSha = $patchMainSha
        PreviousMainSha = @(Invoke-Git -Root $backendRoot -Arguments @("rev-parse", "HEAD^"))[0]
        Shifted = $false
    }
    $patchCloseOutput = @(Invoke-InterceptedClose -Session patch-test -State $patchState -BeforeGit {
        param($Arguments, $State, $GitExecutable)
        if ($Arguments[2] -eq "merge-base" -and -not $State.Shifted) {
            & $GitExecutable -C $State.Root update-ref refs/remotes/origin/main $State.PreviousMainSha $State.MainSha
            if ($LASTEXITCODE -ne 0) { throw "Fixture could not move origin/main after its snapshot." }
            $State.Shifted = $true
        }
    })
    Assert-True $patchState.Shifted "The immutable-main fixture did not move origin/main during integration."
    Assert-True (
        @($patchCloseOutput -match "integration=patch-equivalent").Count -gt 0
    ) "Close did not recognize a fully patch-equivalent branch."
    Assert-True (
        @($patchCloseOutput -match "main=$patchMainSha").Count -eq 1
    ) "Close did not retain its checked main SHA."
    Assert-True (-not (Test-Path -LiteralPath $patchWorktree)) "Patch-equivalent worktree remains after close."
    Write-Output "SESSION_WORKTREE_CASE_PASS immutable-main-patch-equivalence"

    [void](& $scriptUnderTest -Action Start -Session squash-test -Repository backend `
        -WorkspaceRoot $fixtureRoot -AllowLowDisk)
    $squashWorktree = Join-Path $fixtureRoot "_worktrees\sessions\squash-test\backend"
    $squashBranch = @(Invoke-Git -Root $squashWorktree -Arguments @("branch", "--show-current"))[0]
    foreach ($part in @("first", "second")) {
        Set-Content -LiteralPath (Join-Path $squashWorktree "squash-$part.txt") -Value $part -Encoding UTF8
        [void](Invoke-Git -Root $squashWorktree -Arguments @("add", "squash-$part.txt"))
        [void](Invoke-Git -Root $squashWorktree -Arguments @("commit", "-m", "fixture squash $part"))
    }
    $squashHead = @(Invoke-Git -Root $squashWorktree -Arguments @("rev-parse", "HEAD"))[0]
    [void](Invoke-Git -Root $squashWorktree -Arguments @("push", "origin", $squashBranch))
    [void](Invoke-Git -Root $backendRoot -Arguments @("merge", "--squash", $squashBranch))
    [void](Invoke-Git -Root $backendRoot -Arguments @("commit", "-m", "fixture aggregate squash"))
    [void](Invoke-Git -Root $backendRoot -Arguments @("push", "origin", "main"))
    $squashMain = @(Invoke-Git -Root $backendRoot -Arguments @("rev-parse", "HEAD"))[0]
    $squashCherry = @(Invoke-Git -Root $squashWorktree -Arguments @("cherry", $squashMain, $squashHead))
    Assert-True (@($squashCherry -match '^\+ ').Count -eq 2) "The fixture must retain both per-commit squash mismatches."
    $squashTrees = @(Invoke-Git -Root $squashWorktree -Arguments @("rev-parse", "$squashHead^{tree}", "$squashMain^{tree}"))
    Assert-True ($squashTrees[0] -ceq $squashTrees[1]) "The aggregate squash must preserve the complete tracked tree."
    [void](& $scriptUnderTest -Action Close -Session squash-test -Repository backend `
        -WorkspaceRoot $fixtureRoot -WhatIf)
    Assert-True (Test-Path -LiteralPath $squashWorktree) "Tree-equivalent WhatIf must preserve the worktree."
    $squashState = @{
        Root = $backendRoot
        MainSha = $squashMain
        PreviousMainSha = @(Invoke-Git -Root $backendRoot -Arguments @("rev-parse", "HEAD^"))[0]
        Shifted = $false
    }
    $squashCloseOutput = @(Invoke-InterceptedClose -Session squash-test -State $squashState -BeforeGit {
        param($Arguments, $State, $GitExecutable)
        if ($Arguments[2] -eq "cherry" -and -not $State.Shifted) {
            & $GitExecutable -C $State.Root update-ref refs/remotes/origin/main $State.PreviousMainSha $State.MainSha
            if ($LASTEXITCODE -ne 0) { throw "Fixture could not move main before aggregate tree comparison." }
            $State.Shifted = $true
        }
    })
    Assert-True $squashState.Shifted "Tree equivalence must be checked after the fixture moves the mutable main ref."
    Assert-True (@($squashCloseOutput -match 'integration=tree-equivalent').Count -eq 1) "Close must recognize an identical aggregate squash tree."
    Assert-True (@($squashCloseOutput -match "main=$squashMain").Count -eq 1) "Tree comparison must use the checked immutable main."
    Assert-True (-not (Test-Path -LiteralPath $squashWorktree)) "Tree-equivalent worktree remains after close."
    Assert-True (@(Invoke-Git -Root $backendRoot -Arguments @("branch", "--list", $squashBranch)).Count -eq 0) "Tree-equivalent local branch remains."
    $preservedSquashHead = @(Invoke-Git -Root (Join-Path $fixtureRoot "remotes\backend.git") `
        -Arguments @("rev-parse", "refs/heads/$squashBranch"))[0]
    Assert-True ($preservedSquashHead -eq $squashHead) "Close must preserve the published original commits."
    Write-Output "SESSION_WORKTREE_CASE_PASS aggregate-squash-tree-equivalence"

    [void](& $scriptUnderTest -Action Start -Session reused-test -Repository backend `
        -WorkspaceRoot $fixtureRoot -AllowLowDisk)
    $reusedWorktree = Join-Path $fixtureRoot "_worktrees\sessions\reused-test\backend"
    $reusedBranch = "codex/later-owned-task"
    [void](Invoke-Git -Root $reusedWorktree -Arguments @("branch", "-m", $reusedBranch))
    foreach ($expected in @("", "codex/wrong-task")) {
        $refused = $false
        try {
            & $scriptUnderTest -Action Close -Session reused-test -Repository backend `
                -WorkspaceRoot $fixtureRoot -ExpectedBranch $expected *> $null
        } catch { $refused = $true }
        Assert-True $refused "A reused branch requires its exact explicit identity."
        Assert-True (Test-Path -LiteralPath $reusedWorktree) "Branch mismatch removed the worktree."
    }
    Set-Content -LiteralPath (Join-Path $reusedWorktree "unique.txt") -Value "owned" -Encoding UTF8
    foreach ($committed in @($false, $true)) {
        if ($committed) {
            [void](Invoke-Git -Root $reusedWorktree -Arguments @("add", "unique.txt"))
            [void](Invoke-Git -Root $reusedWorktree -Arguments @("commit", "-m", "owned unique change"))
        }
        $refused = $false
        try {
            & $scriptUnderTest -Action Close -Session reused-test -Repository backend `
                -WorkspaceRoot $fixtureRoot -ExpectedBranch $reusedBranch *> $null
        } catch { $refused = $true }
        Assert-True $refused "Exact branch must still reject dirty or unmerged work."
        Assert-True (Test-Path -LiteralPath $reusedWorktree) "Unique work was removed."
    }
    [void](Invoke-Git -Root $reusedWorktree -Arguments @("push", "origin", "HEAD:main"))
    [void](& $scriptUnderTest -Action Close -Session reused-test -Repository backend `
        -WorkspaceRoot $fixtureRoot -ExpectedBranch $reusedBranch)
    Assert-True (-not (Test-Path -LiteralPath $reusedWorktree)) "Merged reused worktree remains."

    [void](Invoke-Git -Root $backendRoot -Arguments @("merge", "--ff-only", "origin/main"))
    Set-Content -LiteralPath (Join-Path $backendRoot "merge.txt") -Value "base" -Encoding UTF8
    [void](Invoke-Git -Root $backendRoot -Arguments @("add", "merge.txt"))
    [void](Invoke-Git -Root $backendRoot -Arguments @("commit", "-m", "fixture merge base"))
    [void](Invoke-Git -Root $backendRoot -Arguments @("push", "origin", "main"))
    [void](& $scriptUnderTest -Action Start -Session merge-resolution-test -Repository backend `
        -WorkspaceRoot $fixtureRoot -AllowLowDisk)
    $mergeWorktree = Join-Path $fixtureRoot "_worktrees\sessions\merge-resolution-test\backend"
    $mergeBranch = @(Invoke-Git -Root $mergeWorktree -Arguments @("symbolic-ref", "--short", "HEAD"))[0]
    $mergeBase = @(Invoke-Git -Root $mergeWorktree -Arguments @("rev-parse", "HEAD"))[0]
    Set-Content -LiteralPath (Join-Path $mergeWorktree "merge.txt") -Value "left" -Encoding UTF8
    [void](Invoke-Git -Root $mergeWorktree -Arguments @("add", "merge.txt"))
    [void](Invoke-Git -Root $mergeWorktree -Arguments @("commit", "-m", "fixture left patch"))
    $leftSha = @(Invoke-Git -Root $mergeWorktree -Arguments @("rev-parse", "HEAD"))[0]
    [void](Invoke-Git -Root $mergeWorktree -Arguments @("checkout", "-b", "codex/fixture-merge-side", $mergeBase))
    Set-Content -LiteralPath (Join-Path $mergeWorktree "merge.txt") -Value "right" -Encoding UTF8
    [void](Invoke-Git -Root $mergeWorktree -Arguments @("add", "merge.txt"))
    [void](Invoke-Git -Root $mergeWorktree -Arguments @("commit", "-m", "fixture right patch"))
    $rightSha = @(Invoke-Git -Root $mergeWorktree -Arguments @("rev-parse", "HEAD"))[0]
    [void](Invoke-Git -Root $mergeWorktree -Arguments @("checkout", $mergeBranch))
    & git -C $mergeWorktree merge --no-ff --no-commit codex/fixture-merge-side *> $null
    Assert-True ($LASTEXITCODE -eq 1) "The merge-resolution fixture must create a real conflict."
    Set-Content -LiteralPath (Join-Path $mergeWorktree "merge.txt") -Value "unique merge resolution" -Encoding UTF8
    [void](Invoke-Git -Root $mergeWorktree -Arguments @("add", "merge.txt"))
    [void](Invoke-Git -Root $mergeWorktree -Arguments @("commit", "-m", "fixture unique merge resolution"))
    $mergeSha = @(Invoke-Git -Root $mergeWorktree -Arguments @("rev-parse", "HEAD"))[0]
    # Put both ordinary patches on main without integrating the merge resolution.
    [void](Invoke-Git -Root $backendRoot -Arguments @("cherry-pick", "-x", $leftSha))
    [void](Invoke-Git -Root $backendRoot -Arguments @("revert", "--no-edit", "HEAD"))
    [void](Invoke-Git -Root $backendRoot -Arguments @("cherry-pick", "-x", $rightSha))
    [void](Invoke-Git -Root $backendRoot -Arguments @("push", "origin", "main"))
    $mergeCherry = @(Invoke-Git -Root $mergeWorktree -Arguments @("cherry", "origin/main", "HEAD"))
    Assert-True (
        $mergeCherry.Count -gt 0 -and @($mergeCherry -like "+ *").Count -eq 0
    ) "The fixture must fool a check based only on git cherry."
    $mergeRefused = $false
    try {
        & $scriptUnderTest -Action Close -Session merge-resolution-test -Repository backend `
            -WorkspaceRoot $fixtureRoot *> $null
    } catch {
        if (-not $_.Exception.Message.Contains("not merged")) { throw }
        $mergeRefused = $true
    }
    Assert-True $mergeRefused "Close must preserve an exclusive merge despite equivalent ordinary patches."
    Assert-True (Test-Path -LiteralPath $mergeWorktree) "Close removed the unique merge worktree."
    Assert-True (
        @(Invoke-Git -Root $backendRoot -Arguments @("rev-parse", "refs/heads/$mergeBranch"))[0] -eq $mergeSha
    ) "Close removed or changed the unique merge branch."
    Assert-True (
        (Get-Content -LiteralPath (Join-Path $mergeWorktree "merge.txt") -Raw).Trim() -eq "unique merge resolution"
    ) "Close lost the unique merge resolution."
    Write-Output "SESSION_WORKTREE_CASE_PASS exclusive-merge-resolution"

    Add-Content -LiteralPath $backendExclude -Value ".env.boundary" -Encoding UTF8
    foreach ($boundary in @("registration", "branch", "head", "dirty", "ignored", "late-head")) {
        $boundarySession = "boundary-$boundary"
        [void](& $scriptUnderTest -Action Start -Session $boundarySession -Repository backend `
            -WorkspaceRoot $fixtureRoot -AllowLowDisk)
        $boundaryPath = Join-Path $fixtureRoot "_worktrees\sessions\$boundarySession\backend"
        $boundaryBranch = @(Invoke-Git -Root $boundaryPath -Arguments @("symbolic-ref", "--short", "HEAD"))[0]
        $checkedSha = @(Invoke-Git -Root $boundaryPath -Arguments @("rev-parse", "HEAD"))[0]
        $advancedSha = $checkedSha
        if ($boundary -in @("head", "late-head")) {
            Set-Content -LiteralPath (Join-Path $boundaryPath "boundary.txt") -Value "preserve unique commit" -Encoding UTF8
            [void](Invoke-Git -Root $boundaryPath -Arguments @("add", "boundary.txt"))
            [void](Invoke-Git -Root $boundaryPath -Arguments @("commit", "-m", "fixture concurrent advance"))
            $advancedSha = @(Invoke-Git -Root $boundaryPath -Arguments @("rev-parse", "HEAD"))[0]
            [void](Invoke-Git -Root $boundaryPath -Arguments @("reset", "--hard", $checkedSha))
        }
        $boundaryState = @{
            Kind = $boundary; Root = $backendRoot; Path = $boundaryPath
            SurvivorPath = $boundaryPath; SurvivorBranch = $boundaryBranch
            Ref = "refs/heads/$boundaryBranch"; CheckedSha = $checkedSha; AdvancedSha = $advancedSha
            Lists = 0; Injected = $false
        }
        $boundaryRefused = $false
        try {
            Invoke-InterceptedClose -Session $boundarySession -State $boundaryState -BeforeGit {
                param($Arguments, $State, $GitExecutable)
                $listing = $Arguments[2] -eq "worktree" -and $Arguments[3] -eq "list"
                if ($listing) { $State.Lists++ }
                $beforeRecheck = $State.Kind -ne "late-head" -and $listing -and $State.Lists -eq 2
                $beforeDelete = $State.Kind -eq "late-head" -and $Arguments[2] -eq "update-ref" -and $Arguments -contains "-d"
                if ($State.Injected -or -not ($beforeRecheck -or $beforeDelete)) { return }
                $State.Injected = $true
                switch ($State.Kind) {
                    "registration" {
                        $State.SurvivorPath = "$($State.Path)-moved"
                        & $GitExecutable -C $State.Root worktree move $State.Path $State.SurvivorPath
                    }
                    "branch" {
                        $State.SurvivorBranch = "codex/fixture-renamed-boundary"
                        & $GitExecutable -C $State.Path branch -m $State.SurvivorBranch
                    }
                    "head" { & $GitExecutable -C $State.Path reset --hard $State.AdvancedSha }
                    "dirty" {
                        Set-Content -LiteralPath (Join-Path $State.Path "README.md") -Value "preserve tracked edit" -Encoding UTF8
                    }
                    "ignored" {
                        Set-Content -LiteralPath (Join-Path $State.Path ".env.boundary") -Value "preserve ignored data" -Encoding UTF8
                    }
                    "late-head" {
                        & $GitExecutable -C $State.Root update-ref $State.Ref $State.AdvancedSha $State.CheckedSha
                    }
                }
                if ($LASTEXITCODE -ne 0) { throw "Fixture boundary mutation failed: $($State.Kind)" }
            } *> $null
        } catch {
            $expectedError = if ($boundary -eq "late-head") { "update-ref" } else { "after close preflight" }
            if (-not $_.Exception.Message.Contains($expectedError)) { throw }
            $boundaryRefused = $true
        }
        Assert-True $boundaryState.Injected "The $boundary fixture never reached its interception boundary."
        Assert-True $boundaryRefused "Close did not refuse the $boundary change."
        Assert-True (
            @(Invoke-Git -Root $backendRoot -Arguments @("rev-parse", "refs/heads/$($boundaryState.SurvivorBranch)"))[0] -eq $advancedSha
        ) "Close lost or changed the branch at the $boundary boundary."
        if ($boundary -eq "late-head") {
            Assert-True (-not (Test-Path -LiteralPath $boundaryPath)) "The late advance must happen after worktree removal."
        } else {
            Assert-True (Test-Path -LiteralPath $boundaryState.SurvivorPath) "Close removed the changed $boundary worktree."
        }
        if ($boundary -in @("head", "late-head")) {
            Assert-True (
                @(Invoke-Git -Root $backendRoot -Arguments @("show", "${advancedSha}:boundary.txt"))[0] -eq "preserve unique commit"
            ) "Close lost the advanced commit's content."
        } elseif ($boundary -eq "dirty") {
            Assert-True (
                (Get-Content -LiteralPath (Join-Path $boundaryPath "README.md") -Raw).Trim() -eq "preserve tracked edit"
            ) "Close lost the tracked edit."
        } elseif ($boundary -eq "ignored") {
            Assert-True (Test-Path -LiteralPath (Join-Path $boundaryPath ".env.boundary")) "Close lost new ignored data."
        }
        Write-Output "SESSION_WORKTREE_CASE_PASS $boundary-boundary"
    }

    $frontendRoot = Join-Path $fixtureRoot "frontend"
    Set-Content -LiteralPath (Join-Path $frontendRoot "dirty-sync.txt") -Value "dirty" -Encoding UTF8
    $dirtySyncRefused = $false
    try {
        & $scriptUnderTest `
            -Action Sync `
            -Repository both `
            -WorkspaceRoot $fixtureRoot *> $null
    } catch {
        $dirtySyncRefused = $_.Exception.Message.Contains("must be clean")
    }
    Assert-True $dirtySyncRefused "Sync must refuse a dirty canonical repository."
    Remove-Item -LiteralPath (Join-Path $frontendRoot "dirty-sync.txt")

    $frontendSeed = Join-Path $fixtureRoot "seed-frontend"
    Set-Content -LiteralPath (Join-Path $frontendSeed "remote.txt") -Value "remote" -Encoding UTF8
    [void](Invoke-Git -Root $frontendSeed -Arguments @("add", "remote.txt"))
    [void](Invoke-Git -Root $frontendSeed -Arguments @("commit", "-m", "fixture remote advance"))
    [void](Invoke-Git -Root $frontendSeed -Arguments @("push", "origin", "main"))

    $syncOutput = @(& $scriptUnderTest `
        -Action Sync `
        -Repository both `
        -WorkspaceRoot $fixtureRoot)
    Assert-True (@($syncOutput -match "SESSION_CANONICAL_SYNCED").Count -gt 0) "Sync did not update canonical repositories."
    $localSha = @(Invoke-Git -Root $frontendRoot -Arguments @("rev-parse", "HEAD"))[0]
    $remoteSha = @(Invoke-Git -Root $frontendRoot -Arguments @("rev-parse", "origin/main"))[0]
    Assert-True ($localSha -eq $remoteSha) "Canonical frontend is not exact origin/main after sync."

    $inspectOutput = @(& $scriptUnderTest `
        -Action Inspect `
        -Repository both `
        -WorkspaceRoot $fixtureRoot `
        -SkipFetch)
    Assert-True (@($inspectOutput -match "SESSION_WORKTREE_SUMMARY").Count -gt 0) "Inspect summary is missing."
    Write-Output "SESSION_WORKTREE_CONTRACT_PASS"
} finally {
    $resolvedFixture = [IO.Path]::GetFullPath($fixtureRoot)
    if (
        $resolvedFixture.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedFixture) -like "academy-session-contract-*" -and
        (Test-Path -LiteralPath $resolvedFixture)
    ) {
        Remove-Item -LiteralPath $resolvedFixture -Recurse -Force
    }
}
