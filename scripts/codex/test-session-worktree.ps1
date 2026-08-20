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

    & $scriptUnderTest `
        -Action Start `
        -Session dry-run-test `
        -Repository both `
        -WorkspaceRoot $fixtureRoot `
        -WhatIf *> $null
    Assert-True (
        -not (Test-Path -LiteralPath (Join-Path $fixtureRoot "_worktrees\sessions\dry-run-test"))
    ) "Start -WhatIf must not create a session directory."

    $startOutput = @(& $scriptUnderTest `
        -Action Start `
        -Session contract-test `
        -Repository both `
        -WorkspaceRoot $fixtureRoot)
    Assert-True (@($startOutput -match "SESSION_WORKTREE_CREATED").Count -gt 0) "Start did not create paired worktrees."

    $backendWorktree = Join-Path $fixtureRoot "_worktrees\sessions\contract-test\backend"
    $frontendWorktree = Join-Path $fixtureRoot "_worktrees\sessions\contract-test\frontend"
    Assert-True (Test-Path -LiteralPath $backendWorktree) "Backend session worktree is missing."
    Assert-True (Test-Path -LiteralPath $frontendWorktree) "Frontend session worktree is missing."

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
        -WorkspaceRoot $fixtureRoot)
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
        -WorkspaceRoot $fixtureRoot)
    $patchWorktree = Join-Path $fixtureRoot "_worktrees\sessions\patch-test\backend"
    Set-Content -LiteralPath (Join-Path $patchWorktree "equivalent.txt") -Value "same patch" -Encoding UTF8
    [void](Invoke-Git -Root $patchWorktree -Arguments @("add", "equivalent.txt"))
    [void](Invoke-Git -Root $patchWorktree -Arguments @("commit", "-m", "fixture session patch"))
    Set-Content -LiteralPath (Join-Path $backendRoot "equivalent.txt") -Value "same patch" -Encoding UTF8
    [void](Invoke-Git -Root $backendRoot -Arguments @("add", "equivalent.txt"))
    [void](Invoke-Git -Root $backendRoot -Arguments @("commit", "-m", "fixture main equivalent"))
    [void](Invoke-Git -Root $backendRoot -Arguments @("push", "origin", "main"))
    $patchCloseOutput = @(& $scriptUnderTest `
        -Action Close `
        -Session patch-test `
        -Repository backend `
        -WorkspaceRoot $fixtureRoot)
    Assert-True (
        @($patchCloseOutput -match "integration=patch-equivalent").Count -gt 0
    ) "Close did not recognize a fully patch-equivalent branch."
    Assert-True (-not (Test-Path -LiteralPath $patchWorktree)) "Patch-equivalent worktree remains after close."

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
