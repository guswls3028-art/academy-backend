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

function Assert-Throws {
    param([scriptblock]$Action, [string]$ExpectedText)
    try {
        & $Action
    } catch {
        if ($_.Exception.Message -notmatch [regex]::Escape($ExpectedText)) {
            throw "Expected failure containing '$ExpectedText', got: $($_.Exception.Message)"
        }
        return
    }
    throw "Expected change-risk planning to fail: $ExpectedText"
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
    -BackendPaths @(
        "docs/operations/github-governance.md",
        "scripts/README.md",
        "CONVENTIONS.md"
    ) `
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

$backendContractPaths = Get-AcademyChangeRiskPlan `
    -BackendPaths @(
        "schema/openapi.json",
        "schema/generation-baseline.json",
        "scripts/lint/check_safe_method_writes.py",
        "scripts/post_deploy_smoke/video_playback_chain.py"
    ) `
    -FrontendPaths @()
Assert-Contains $backendContractPaths.Risks "tenant-data" "API schema and lint paths must retain backend product gates"
Assert-Contains $backendContractPaths.Risks "deployment-governance" "post-deploy smoke paths must retain deployment governance"
Assert-Contains $backendContractPaths.Gates "backend-core" "API schema and lint paths must include backend core gates"
Assert-Contains $backendContractPaths.Gates "backend-deployment-contracts" "post-deploy smoke paths must include deployment contracts"

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
    -BackendPaths @(
        "apps/domains/results/tests/test_session_scores_roster_scope.py",
        "apps/domains/results/tests.py"
    ) `
    -FrontendPaths @(
        "src/app_admin/domains/results/api.test.ts",
        "e2e/admin/results.mock.spec.ts"
    )
Assert-True (-not $testsOnlyPair.RequiresProductionReleaseBundle) "tests-only paired changes must not pretend to be a production release bundle"
Assert-True ("unknown-non-doc" -notin $testsOnlyPair.Risks) "explicit tests-only changes must not be classified as unknown"

$excludedRuntimePaths = Get-AcademyChangeRiskPlan `
    -BackendPaths @(
        "libs/queue/tests/test_client.py",
        "docker/README-build.md"
    ) `
    -FrontendPaths @("src/shared/ui/README.md")
Assert-True (-not $excludedRuntimePaths.RequiresProductionReleaseBundle) "docs/tests exclusions must run before paired runtime/build classification"
Assert-True ("backend-runtime-build" -notin $excludedRuntimePaths.Risks) "backend docs/tests must not become runtime/build risk"
Assert-True ("frontend-runtime-build" -notin $excludedRuntimePaths.Risks) "frontend docs must not become runtime/build risk"
Assert-True ("user-visible-ui" -notin $excludedRuntimePaths.Risks) "frontend docs must not become user-visible UI risk"
Assert-True ("async-worker" -notin $excludedRuntimePaths.Risks) "backend queue tests must not become async-worker risk"

$backendBuild = Get-AcademyChangeRiskPlan `
    -BackendPaths @(
        "libs/queue/client.py",
        "docker/api/Dockerfile",
        "requirements/base.txt"
    ) `
    -FrontendPaths @()
Assert-Contains $backendBuild.Risks "backend-runtime-build" "backend runtime/build paths must not remain diff-only"
Assert-Contains $backendBuild.Risks "async-worker" "backend queue clients must retain worker/queue verification"
Assert-Contains $backendBuild.Gates "backend-core" "backend runtime/build paths must include core gates"
Assert-Contains $backendBuild.Gates "backend-deployment-contracts" "backend runtime/build paths must include deployment contracts"
Assert-True (-not $backendBuild.RequiresProductionReleaseBundle) "single-repository backend build changes must not invent a frontend release"

$frontendBuild = Get-AcademyChangeRiskPlan `
    -BackendPaths @() `
    -FrontendPaths @(
        "package.json",
        "pnpm-lock.yaml",
        "vite.config.ts",
        "tsconfig.json",
        "eslint.config.js",
        "index.html"
    )
Assert-Contains $frontendBuild.Risks "frontend-runtime-build" "frontend runtime/build paths must not remain diff-only"
Assert-Contains $frontendBuild.Gates "frontend-core" "frontend runtime/build paths must include core gates"
Assert-Contains $frontendBuild.Gates "frontend-e2e" "frontend runtime/build paths must include E2E gates"
Assert-Contains $frontendBuild.Gates "frontend-deployment-contracts" "frontend runtime/build paths must include deployment contracts"
Assert-True (-not $frontendBuild.RequiresProductionReleaseBundle) "single-repository frontend build changes must not invent a backend release"

$domainPath = Get-AcademyChangeRiskPlan `
    -BackendPaths @("apps/domains/results/service.py") `
    -FrontendPaths @()
Assert-True ("async-worker" -notin $domainPath.Risks) "worker keywords must match exact path segments rather than the ai substring in domains"

$crossRepositoryBuild = Get-AcademyChangeRiskPlan `
    -BackendPaths @("requirements/base.txt") `
    -FrontendPaths @("package.json")
Assert-True $crossRepositoryBuild.RequiresProductionReleaseBundle "paired runtime/build changes must require final release-bundle readback"

Assert-Throws {
    Get-AcademyChangeRiskPlan -BackendPaths @("unowned/runtime-switch.conf") -FrontendPaths @()
} "Unclassified non-documentation path"

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
