[CmdletBinding()]
param(
    [string]$BackendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$FrontendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\frontend")).Path,
    [string]$BackendBaseRef = "origin/main",
    [string]$FrontendBaseRef = "origin/main",
    [switch]$RunLocalGates
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "stability-contract.ps1")

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$File,
        [string[]]$Arguments = @()
    )
    Write-Host ("[change-risk] {0}> {1} {2}" -f $Root, $File, ($Arguments -join " ")) -ForegroundColor Cyan
    Push-Location $Root
    try {
        & $File @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$File failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

function Get-ChangedPaths {
    param([string]$Root, [string]$BaseRef)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "Git root does not exist: $Root"
    }
    $topOutput = @(& git -C $Root rev-parse --show-toplevel 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Not a Git root: $Root" }
    $top = [string]$topOutput[0]
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    if (-not [string]::Equals((Resolve-Path -LiteralPath $top).Path, $resolved, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Expected a repository root. expected=$resolved actual=$top"
    }

    $paths = [Collections.Generic.List[string]]::new()
    foreach ($gitArgs in @(
        @("diff", "--name-only", "$BaseRef...HEAD"),
        @("diff", "--cached", "--name-only"),
        @("diff", "--name-only"),
        @("ls-files", "--others", "--exclude-standard")
    )) {
        $output = @(& git -C $Root @gitArgs 2>&1)
        if ($LASTEXITCODE -ne 0) { throw "git $($gitArgs -join ' ') failed: $($output -join [Environment]::NewLine)" }
        foreach ($path in $output) {
            if ($path -and -not $paths.Contains([string]$path)) { [void]$paths.Add([string]$path) }
        }
    }
    return @($paths)
}

function Add-GateCommand {
    param(
        [Collections.Generic.List[object]]$Commands,
        [string]$Gate,
        [string]$Repository,
        [string]$File,
        [string[]]$Arguments
    )
    [void]$Commands.Add([pscustomobject]@{
        Gate = $Gate
        Repository = $Repository
        File = $File
        Arguments = @($Arguments)
    })
}

$BackendRoot = (Resolve-Path -LiteralPath $BackendRoot).Path
$FrontendRoot = (Resolve-Path -LiteralPath $FrontendRoot).Path
$backendPaths = Get-ChangedPaths -Root $BackendRoot -BaseRef $BackendBaseRef
$frontendPaths = Get-ChangedPaths -Root $FrontendRoot -BaseRef $FrontendBaseRef
$plan = Get-AcademyChangeRiskPlan -BackendPaths $backendPaths -FrontendPaths $frontendPaths
$commands = [Collections.Generic.List[object]]::new()

if ("backend-diff-check" -in $plan.Gates) {
    Add-GateCommand $commands "backend-diff-check" "backend" "git" @("diff", "--check", "$BackendBaseRef...HEAD")
    Add-GateCommand $commands "backend-diff-check" "backend" "git" @("diff", "--check")
}
if ("frontend-diff-check" -in $plan.Gates) {
    Add-GateCommand $commands "frontend-diff-check" "frontend" "git" @("diff", "--check", "$FrontendBaseRef...HEAD")
    Add-GateCommand $commands "frontend-diff-check" "frontend" "git" @("diff", "--check")
}
if ("backend-core" -in $plan.Gates) {
    Add-GateCommand $commands "backend-core" "backend" "python" @("manage.py", "check", "--settings", "apps.api.config.settings.test")
    Add-GateCommand $commands "backend-core" "backend" "python" @("manage.py", "makemigrations", "--check", "--dry-run", "--settings", "apps.api.config.settings.test")
    Add-GateCommand $commands "backend-core" "backend" "python" @("-m", "ruff", "check", "apps/", "academy/")
    Add-GateCommand $commands "backend-core" "backend" "python" @("scripts/lint/check_submission_lifecycle_boundary.py")
    Add-GateCommand $commands "backend-core" "backend" "python" @("scripts/lint/refactor_boundary_snapshot.py", "--strict-touched", "--base-ref", $BackendBaseRef)
}
if ("backend-deployment-contracts" -in $plan.Gates) {
    foreach ($script in @(
        "scripts/codex/test-change-risk-contract.ps1",
        "scripts/codex/test-production-release-bundle-contract.ps1",
        "scripts/v1/test-verification-contract.ps1",
        "scripts/v1/test-candidate-env-contract.ps1",
        "scripts/v1/test-aws-json-utf8-contract.ps1",
        "scripts/v1/test-production-source-freshness.ps1",
        "scripts/v1/test-rds-restore-drill-contract.ps1",
        "scripts/v1/test-workflow-governance-contract.ps1",
        "scripts/v1/test-product-analytics-operations-contract.ps1"
    )) {
        Add-GateCommand $commands "backend-deployment-contracts" "backend" "pwsh" @("-NoProfile", "-File", $script)
    }
}
if ("frontend-core" -in $plan.Gates) {
    foreach ($script in @("typecheck", "guard:legacy-api", "lint", "build")) {
        Add-GateCommand $commands "frontend-core" "frontend" "pnpm" @($script)
    }
}
if ("frontend-e2e" -in $plan.Gates) {
    Add-GateCommand $commands "frontend-e2e" "frontend" "pnpm" @("test:e2e:gate")
}
if ("frontend-deployment-contracts" -in $plan.Gates -and "frontend-core" -notin $plan.Gates) {
    Add-GateCommand $commands "frontend-deployment-contracts" "frontend" "pnpm" @("guard:deployment-governance")
    Add-GateCommand $commands "frontend-deployment-contracts" "frontend" "pnpm" @("guard:runtime-recovery")
}

$result = [pscustomobject]@{
    SchemaVersion = 1
    BackendBaseRef = $BackendBaseRef
    FrontendBaseRef = $FrontendBaseRef
    Plan = $plan
    Commands = @($commands)
}

if ($RunLocalGates) {
    foreach ($command in $commands) {
        $root = if ($command.Repository -eq "backend") { $BackendRoot } else { $FrontendRoot }
        Invoke-Checked -Root $root -File $command.File -Arguments $command.Arguments
    }
    Write-Host "ACADEMY_CHANGE_RISK_LOCAL_GATES_PASS" -ForegroundColor Green
}

$result | ConvertTo-Json -Depth 8
