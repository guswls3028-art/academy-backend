$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "stability-contract.ps1")

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
    throw "Expected release-bundle validation to fail: $ExpectedText"
}

$noLock = Get-AcademyDeploymentLockState -LockReadback $null -Now 100
if ($noLock.Active -or $noLock.Owner -or $noLock.ExpiresAt -ne 0) {
    throw "an empty DynamoDB get-item response must mean no active lock"
}

$activeLockReadback = [pscustomobject]@{
    Item = [pscustomobject]@{
        owner = [pscustomobject]@{ S = "ci-deploy:123:1" }
        ttl = [pscustomobject]@{ N = "101" }
    }
}
$parsedActiveLock = Get-AcademyDeploymentLockState -LockReadback $activeLockReadback -Now 100
if (-not $parsedActiveLock.Active -or $parsedActiveLock.Owner -ne "ci-deploy:123:1") {
    throw "an unexpired DynamoDB lock item must remain active"
}

$backendSha = "1111111111111111111111111111111111111111"
$frontendSha = "2222222222222222222222222222222222222222"
$evidence = [pscustomobject]@{
    SchemaVersion = 1
    Backend = [pscustomobject]@{
        ExpectedSha = $backendSha
        IsAncestorOfOriginMain = $true
        PendingDeploymentsCount = 0
        ManifestContainsExpectedSha = $true
        ManifestShaIsAncestorOfOriginMain = $true
        Manifest = [pscustomobject]@{
            schemaVersion = 1
            status = "successful"
            complete = $true
            gitSha = $backendSha
        }
        Lock = [pscustomobject]@{ Active = $false; Owner = $null; ExpiresAt = 0 }
        Run = [pscustomobject]@{
            databaseId = 101
            status = "completed"
            conclusion = "success"
            event = "workflow_dispatch"
            headBranch = "main"
            headSha = $backendSha
            workflowName = "V1 Build and Push latest (OIDC)"
            jobs = @(
                [pscustomobject]@{ name = "Verify deployment"; status = "completed"; conclusion = "success" },
                [pscustomobject]@{ name = "Release shared production mutation lock"; status = "completed"; conclusion = "success" }
            )
        }
    }
    Frontend = [pscustomobject]@{
        ExpectedSha = $frontendSha
        IsAncestorOfOriginMain = $true
        PendingDeploymentsCount = 0
        LiveVersions = @(
            [pscustomobject]@{
                Url = "https://hakwonplus.com/version.json"
                Version = $frontendSha
                IncludesExpectedSha = $true
                IsAncestorOfOriginMain = $true
            }
        )
        Run = [pscustomobject]@{
            databaseId = 202
            status = "completed"
            conclusion = "success"
            event = "push"
            headBranch = "main"
            headSha = $frontendSha
            workflowName = "Frontend Quality Gate"
            jobs = @(
                [pscustomobject]@{ name = "Deploy to Cloudflare Pages"; status = "completed"; conclusion = "success" },
                [pscustomobject]@{ name = "E2E 왕복 테스트 + tenant availability"; status = "completed"; conclusion = "success" }
            )
        }
    }
}

$result = Assert-AcademyProductionReleaseBundle -Evidence $evidence
if (-not $result.Passed) { throw "valid release bundle did not pass" }

$badManifest = $evidence | ConvertTo-Json -Depth 12 | ConvertFrom-Json
$badManifest.Backend.Manifest.gitSha = "3333333333333333333333333333333333333333"
$badManifest.Backend.ManifestContainsExpectedSha = $false
Assert-Throws { Assert-AcademyProductionReleaseBundle -Evidence $badManifest } "backend manifest SHA"

$pendingApproval = $evidence | ConvertTo-Json -Depth 12 | ConvertFrom-Json
$pendingApproval.Frontend.PendingDeploymentsCount = 1
Assert-Throws { Assert-AcademyProductionReleaseBundle -Evidence $pendingApproval } "pending deployment"

$activeLock = $evidence | ConvertTo-Json -Depth 12 | ConvertFrom-Json
$activeLock.Backend.Lock.Active = $true
$activeLock.Backend.Lock.Owner = "github-run-999"
Assert-Throws { Assert-AcademyProductionReleaseBundle -Evidence $activeLock } "deployment lock is active"

$prRun = $evidence | ConvertTo-Json -Depth 12 | ConvertFrom-Json
$prRun.Frontend.Run.event = "pull_request"
Assert-Throws { Assert-AcademyProductionReleaseBundle -Evidence $prRun } "frontend release run event"

$missingVerification = $evidence | ConvertTo-Json -Depth 12 | ConvertFrom-Json
$missingVerification.Backend.Run.jobs = @(
    [pscustomobject]@{ name = "Release shared production mutation lock"; status = "completed"; conclusion = "success" }
)
Assert-Throws { Assert-AcademyProductionReleaseBundle -Evidence $missingVerification } "Verify deployment"

$staleFrontend = $evidence | ConvertTo-Json -Depth 12 | ConvertFrom-Json
$staleFrontend.Frontend.LiveVersions[0].Version = "4444444444444444444444444444444444444444"
$staleFrontend.Frontend.LiveVersions[0].IncludesExpectedSha = $false
Assert-Throws { Assert-AcademyProductionReleaseBundle -Evidence $staleFrontend } "frontend live version"

Write-Host "ACADEMY_PRODUCTION_RELEASE_BUNDLE_CONTRACT_PASS" -ForegroundColor Green
