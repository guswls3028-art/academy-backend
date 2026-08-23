$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "stability-contract.ps1")

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Contains {
    param([object[]]$Values, [string]$Expected, [string]$Message)
    if ($Expected -notin @($Values)) {
        throw "$Message expected=$Expected actual=$(@($Values) -join ',')"
    }
}

$gitPathLines = @(Get-AcademyGitPathLines -Lines @(
    "warning: in the working copy of 'scripts/codex/stability-contract.ps1', LF will be replaced by CRLF",
    "hint: use a narrower diff",
    "scripts/codex/stability-contract.ps1",
    "docs/README.md"
))
Assert-True ($gitPathLines.Count -eq 2) "git warning and hint lines must not become changed paths"
Assert-Contains $gitPathLines "scripts/codex/stability-contract.ps1" "real git path output must be preserved"

$docsOnly = Get-AcademyChangeRiskPlan `
    -BackendPaths @("docs/operations/github-governance.md") `
    -FrontendPaths @()
Assert-True $docsOnly.DocsOnly "docs-only changes must remain docs-only"
Assert-Contains $docsOnly.Requirements "owning-docs-current" "docs-only plan must keep owning documentation current"
Assert-Contains $docsOnly.Gates "backend-diff-check" "docs-only plan must include a backend diff check"
Assert-True (-not $docsOnly.RequiresProductionReleaseBundle) "backend docs-only changes must not require a production release bundle"

$backendProduct = Get-AcademyChangeRiskPlan `
    -BackendPaths @(
        "apps/domains/results/views/session_scores_view.py",
        "apps/domains/results/tests/test_session_scores_roster_scope.py",
        "docs/domain/exam-grading.md"
    ) `
    -FrontendPaths @()
Assert-Contains $backendProduct.Risks "tenant-data" "backend product code must route to tenant/data verification"
Assert-Contains $backendProduct.Requirements "failure-first-regression" "product changes must require a reproducing regression"
Assert-Contains $backendProduct.Requirements "postgresql-tenant-ci" "backend product changes must require PostgreSQL tenant CI"
Assert-Contains $backendProduct.Gates "backend-core" "backend product changes must include the core local gates"

$frontendProduct = Get-AcademyChangeRiskPlan `
    -BackendPaths @() `
    -FrontendPaths @(
        "src/shared/ui/modal/AdminModal.tsx",
        "e2e/admin/messaging-operations-control.mock.spec.ts",
        "docs/DEPLOYMENT-OPERATIONS.md"
    )
Assert-Contains $frontendProduct.Risks "user-visible-ui" "frontend UI changes must route to visual verification"
Assert-Contains $frontendProduct.Requirements "desktop-390-live-readback" "frontend UI changes must require desktop/390 readback"
Assert-Contains $frontendProduct.Gates "frontend-core" "frontend UI changes must include the core frontend gates"
Assert-Contains $frontendProduct.Gates "frontend-e2e" "frontend UI changes must include the PR E2E gate"

$crossRepository = Get-AcademyChangeRiskPlan `
    -BackendPaths @("apps/domains/enrollment/services.py") `
    -FrontendPaths @("src/app_admin/domains/students/api.ts")
Assert-Contains $crossRepository.Risks "cross-repository-contract" "paired product changes must expose cross-repository risk"
Assert-True $crossRepository.RequiresProductionReleaseBundle "paired product changes must require final release-bundle readback"
Assert-Contains $crossRepository.Requirements "backward-compatible-api-window" "paired product changes must preserve a deployment compatibility window"

$testsOnlyPair = Get-AcademyChangeRiskPlan `
    -BackendPaths @("apps/domains/results/tests/test_session_scores_roster_scope.py") `
    -FrontendPaths @(
        "src/app_admin/domains/results/api.test.ts",
        "e2e/admin/results.mock.spec.ts"
    )
Assert-True (-not $testsOnlyPair.RequiresProductionReleaseBundle) "tests-only paired changes must not pretend to be a production release bundle"

$governance = Get-AcademyChangeRiskPlan `
    -BackendPaths @(
        ".github/workflows/quality-gate.yml",
        "scripts/codex/stability-contract.ps1",
        "scripts/v1/test-workflow-governance-contract.ps1"
    ) `
    -FrontendPaths @("scripts/guard-deployment-governance.mjs")
Assert-Contains $governance.BackendPaths ".github/workflows/quality-gate.yml" "leading dot in .github paths must be preserved"
Assert-Contains $governance.Risks "deployment-governance" "deployment paths must route to governance verification"
Assert-Contains $governance.Gates "backend-deployment-contracts" "backend deployment paths must invoke existing contract tests"
Assert-Contains $governance.Gates "frontend-deployment-contracts" "frontend deployment paths must invoke existing governance guards"

Write-Host "ACADEMY_CHANGE_RISK_CONTRACT_PASS" -ForegroundColor Green
