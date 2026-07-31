$ErrorActionPreference = "Stop"
$guard = Join-Path $PSScriptRoot "assert-production-source-freshness.ps1"
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testRoot = Join-Path $tempBase ("academy-source-freshness-" + [Guid]::NewGuid().ToString("N"))

function Invoke-Git {
    param([string]$Root, [string[]]$Arguments)
    $output = @(& git -C $Root @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return $output
}

function Assert-GuardFails {
    param([scriptblock]$Action, [string]$ExpectedText)
    try {
        & $Action
    } catch {
        if ($_.Exception.Message -notmatch [regex]::Escape($ExpectedText)) {
            throw "Expected failure containing '$ExpectedText', got: $($_.Exception.Message)"
        }
        return
    }
    throw "Expected source freshness guard to fail: $ExpectedText"
}

try {
    $seed = Join-Path $testRoot "seed"
    $remote = Join-Path $testRoot "remote.git"
    $checkout = Join-Path $testRoot "checkout"
    $writer = Join-Path $testRoot "writer"
    New-Item -ItemType Directory -Path $seed -Force | Out-Null
    Invoke-Git -Root $seed -Arguments @("init", "-b", "main") | Out-Null
    Invoke-Git -Root $seed -Arguments @("config", "user.name", "Academy Test") | Out-Null
    Invoke-Git -Root $seed -Arguments @("config", "user.email", "academy-test@example.invalid") | Out-Null
    Set-Content -LiteralPath (Join-Path $seed "README.md") -Value "source freshness fixture"
    Invoke-Git -Root $seed -Arguments @("add", "README.md") | Out-Null
    Invoke-Git -Root $seed -Arguments @("commit", "-m", "fixture base") | Out-Null
    $releaseSha = [string](Invoke-Git -Root $seed -Arguments @("rev-parse", "HEAD") | Select-Object -First 1)
    $reports = Join-Path $seed "docs\reports"
    New-Item -ItemType Directory -Path $reports -Force | Out-Null
    [ordered]@{
        schemaVersion = 1
        status = "successful"
        complete = $true
        gitSha = $releaseSha
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reports "release-manifest.latest.json")
    Invoke-Git -Root $seed -Arguments @("add", "docs/reports/release-manifest.latest.json") | Out-Null
    Invoke-Git -Root $seed -Arguments @("commit", "-m", "fixture release manifest") | Out-Null

    & git clone --bare $seed $remote 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to create freshness-test remote." }
    & git clone $remote $checkout 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to create freshness-test checkout." }
    & $guard -RepoRoot $checkout

    Add-Content -LiteralPath (Join-Path $checkout "README.md") -Value "dirty"
    Assert-GuardFails -ExpectedText "must be clean" -Action {
        & $guard -RepoRoot $checkout -SkipFetch
    }
    Invoke-Git -Root $checkout -Arguments @("restore", "README.md") | Out-Null

    Invoke-Git -Root $checkout -Arguments @("switch", "-c", "topic") | Out-Null
    Assert-GuardFails -ExpectedText "must be branch 'main'" -Action {
        & $guard -RepoRoot $checkout -SkipFetch
    }
    Invoke-Git -Root $checkout -Arguments @("switch", "main") | Out-Null

    & git clone $remote $writer 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to create freshness-test writer." }
    Invoke-Git -Root $writer -Arguments @("config", "user.name", "Academy Test") | Out-Null
    Invoke-Git -Root $writer -Arguments @("config", "user.email", "academy-test@example.invalid") | Out-Null
    Add-Content -LiteralPath (Join-Path $writer "README.md") -Value "remote advance"
    Invoke-Git -Root $writer -Arguments @("add", "README.md") | Out-Null
    Invoke-Git -Root $writer -Arguments @("commit", "-m", "advance remote") | Out-Null
    Invoke-Git -Root $writer -Arguments @("push", "origin", "main") | Out-Null
    Assert-GuardFails -ExpectedText "not the exact latest origin/main" -Action {
        & $guard -RepoRoot $checkout
    }

    Write-Host "PRODUCTION_SOURCE_FRESHNESS_TEST_PASS" -ForegroundColor Green
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        $resolved = (Resolve-Path -LiteralPath $testRoot).Path
        if (-not $resolved.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove unexpected freshness-test path: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
