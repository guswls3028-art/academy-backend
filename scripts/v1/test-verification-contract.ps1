$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot

function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    if ($Actual -ne $Expected) {
        throw "$Message (expected=$Expected actual=$Actual)"
    }
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Message)
    $threw = $false
    try {
        & $Action
    } catch {
        $threw = $true
    }
    if (-not $threw) { throw $Message }
}

. (Join-Path $ScriptRoot "core\batch.ps1")
. (Join-Path $ScriptRoot "resources\jobdef.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")

$modern = @{
    image = "repo/image:sha"
    resourceRequirements = @(
        @{ type = "VCPU"; value = "2" },
        @{ type = "MEMORY"; value = "4096" }
    )
} | ConvertTo-Json -Depth 4 | ConvertFrom-Json
$legacy = @{
    image = "repo/image:sha"
    vcpus = 2
    memory = 4096
} | ConvertTo-Json | ConvertFrom-Json

Assert-Equal (Get-BatchContainerResourceValue $modern "VCPU") 2 "modern VCPU must be read"
Assert-Equal (Get-BatchContainerResourceValue $modern "MEMORY") 4096 "modern memory must be read"
Assert-Equal (Get-BatchContainerResourceValue $legacy "VCPU") 2 "legacy VCPU must remain compatible"
Assert-Equal (Get-BatchContainerResourceValue $legacy "MEMORY") 4096 "legacy memory must remain compatible"
Assert-True (-not (Test-JobDefDrift @{ containerProperties = $modern } @{ containerProperties = $legacy })) "equivalent modern/legacy resources must not drift"

$changed = @{
    image = "repo/image:sha"
    resourceRequirements = @(
        @{ type = "VCPU"; value = "4" },
        @{ type = "MEMORY"; value = "4096" }
    )
} | ConvertTo-Json -Depth 4 | ConvertFrom-Json
Assert-True (Test-JobDefDrift @{ containerProperties = $modern } @{ containerProperties = $changed }) "changed VCPU must drift"

. (Join-Path $ScriptRoot "core\reports.ps1")
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("academy-report-contract-" + [guid]::NewGuid().ToString("N"))
try {
    $ReportsBase = $tempRoot
    $ReportsHistory = Join-Path $ReportsBase "history"
    New-Item -ItemType Directory -Path $ReportsHistory -Force | Out-Null
    $script:VerificationRunId = "contract-run-a"
    $validReleaseManifest = [ordered]@{
        schemaVersion = 1
        status = "successful"
        complete = $true
        gitSha = "1234567890abcdef1234567890abcdef12345678"
        images = [ordered]@{
            "academy-base" = @{ digest = "sha256:$('1' * 64)" }
            "academy-api" = @{ digest = "sha256:$('2' * 64)" }
            "academy-video-worker" = @{ digest = "sha256:$('3' * 64)" }
            "academy-messaging-worker" = @{ digest = "sha256:$('4' * 64)" }
            "academy-ai-worker-cpu" = @{ digest = "sha256:$('5' * 64)" }
            "academy-tools-worker" = @{ digest = "sha256:$('6' * 64)" }
        }
    } | ConvertTo-Json -Depth 5 -Compress

    $companions = @(
        "audit.latest.md",
        "drift.latest.md",
        "runtime-images.latest.md",
        "consistency.latest.md",
        "front-connection.latest.md",
        "release-manifest.latest.json"
    )
    foreach ($name in $companions) {
        $value = if ($name -eq "release-manifest.latest.json") {
            $validReleaseManifest
        } else {
            "snapshot:$name`n`n**Verification Run ID:** $($script:VerificationRunId)"
        }
        Set-Content -LiteralPath (Join-Path $ReportsBase $name) -Value $value -Encoding UTF8
    }
    $releaseManifestPath = Join-Path $ReportsBase "release-manifest.latest.json"
    $manifestEvidence = Get-SuccessfulReleaseImageDigests -Path $releaseManifestPath
    $script:VerificationReleaseManifestHash = $manifestEvidence.ManifestHash
    Assert-Equal $manifestEvidence.Digests["academy-api"] "sha256:$('2' * 64)" "manifest digest and hash must come from one byte snapshot"

    $invalidManifestPath = Join-Path $ReportsBase "invalid-release-manifest.json"
    Set-Content -LiteralPath $invalidManifestPath -Value '{"schemaVersion":1,"status":"candidate","complete":false,"images":{}}' -Encoding UTF8
    Assert-Throws { Get-SuccessfulReleaseImageDigests -Path $invalidManifestPath } "incomplete manifest must be rejected by fallback validation"
    $missingDigestManifest = $validReleaseManifest | ConvertFrom-Json
    $missingDigestManifest.images."academy-api".digest = ""
    Set-Content -LiteralPath $invalidManifestPath -Value ($missingDigestManifest | ConvertTo-Json -Depth 5 -Compress) -Encoding UTF8
    Assert-Throws { Get-SuccessfulReleaseImageDigests -Path $invalidManifestPath } "missing required digest must be rejected by fallback validation"
    Set-Content -LiteralPath $invalidManifestPath -Value ($validReleaseManifest -replace '"schemaVersion":1', '"schemaVersion":"1"') -Encoding UTF8
    Assert-Throws { Get-SuccessfulReleaseImageDigests -Path $invalidManifestPath } "string schemaVersion must be rejected"
    Set-Content -LiteralPath $invalidManifestPath -Value ($validReleaseManifest -replace '"complete":true', '"complete":"false"') -Encoding UTF8
    Assert-Throws { Get-SuccessfulReleaseImageDigests -Path $invalidManifestPath } "string complete flag must be rejected"
    Set-Content -LiteralPath $invalidManifestPath -Value ($validReleaseManifest -replace '"complete":true', '"complete":1') -Encoding UTF8
    Assert-Throws { Get-SuccessfulReleaseImageDigests -Path $invalidManifestPath } "numeric complete flag must be rejected"
    Set-Content -LiteralPath $invalidManifestPath -Value ($validReleaseManifest -replace '"gitSha":"[0-9a-f]{40}"', '"gitSha":""') -Encoding UTF8
    Assert-Throws { Get-SuccessfulReleaseImageDigests -Path $invalidManifestPath } "invalid git SHA must be rejected"
    Assert-Throws { Get-SuccessfulReleaseImageDigests -Path (Join-Path $ReportsBase "missing-release-manifest.json") } "missing manifest must be rejected by fallback validation"
    Remove-Item -LiteralPath $invalidManifestPath

    Save-DeployVerificationReport -MarkdownContent "# verification"
    Save-DeployVerificationReport -MarkdownContent "# verification second run"
    $historyReport = Get-ChildItem -LiteralPath $ReportsHistory -Filter "*-deploy-verification.md" | Select-Object -First 1
    Assert-True ($null -ne $historyReport) "deploy history report must be created"
    $historyContent = Get-Content -Raw -LiteralPath $historyReport.FullName
    Assert-True ($historyContent.Contains("## Immutable Evidence Bundle")) "history report must link immutable evidence"
    Assert-Equal (Get-ChildItem -LiteralPath $ReportsHistory -Filter "*-deploy-verification.md" | Measure-Object).Count 2 "same-second reports must remain unique"
    Assert-Equal (Get-ChildItem -LiteralPath $ReportsHistory | Measure-Object).Count 14 "each history report must keep six companions"

    $releaseManifestContent = Get-Content -Raw -LiteralPath $releaseManifestPath
    Set-Content -LiteralPath $releaseManifestPath -Value '{"status":"PASS","release":"changed"}' -Encoding UTF8
    $changedManifestRejected = $false
    try {
        Save-DeployVerificationReport -MarkdownContent "# changed manifest must fail"
    } catch {
        $changedManifestRejected = $true
    }
    Assert-True $changedManifestRejected "release manifest changed after runtime collection must fail closed"
    Assert-Equal (Get-ChildItem -LiteralPath $ReportsHistory | Measure-Object).Count 14 "changed manifest rejection must not leave history artifacts"
    Set-Content -LiteralPath $releaseManifestPath -Value $releaseManifestContent -Encoding UTF8
    $script:VerificationReleaseManifestHash = (Get-SuccessfulReleaseImageDigests -Path $releaseManifestPath).ManifestHash

    Remove-Item -LiteralPath (Join-Path $ReportsBase "audit.latest.md")
    $latestBeforeMissingCompanion = Get-Content -Raw -LiteralPath (Join-Path $ReportsBase "deploy-verification-latest.md")
    $missingCompanionRejected = $false
    try {
        Save-DeployVerificationReport -MarkdownContent "# must fail"
    } catch {
        $missingCompanionRejected = $true
    }
    Assert-True $missingCompanionRejected "missing companion evidence must fail closed"
    Assert-Equal (Get-Content -Raw -LiteralPath (Join-Path $ReportsBase "deploy-verification-latest.md")) $latestBeforeMissingCompanion "failed history save must not replace latest"

    Set-Content -LiteralPath (Join-Path $ReportsBase "audit.latest.md") -Value "snapshot:audit.latest.md`n`n**Verification Run ID:** contract-run-a" -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $ReportsBase "drift.latest.md") -Value "snapshot:drift.latest.md`n`n**Verification Run ID:** contract-run-b" -Encoding UTF8
    $mixedRunRejected = $false
    try {
        Save-DeployVerificationReport -MarkdownContent "# mixed run must fail"
    } catch {
        $mixedRunRejected = $true
    }
    Assert-True $mixedRunRejected "mixed verification run companions must fail closed"
    Assert-Equal (Get-ChildItem -LiteralPath $ReportsHistory | Measure-Object).Count 14 "mixed run rejection must not leave history artifacts"

    Set-Content -LiteralPath (Join-Path $ReportsBase "drift.latest.md") -Value "snapshot:drift.latest.md`n`n**Verification Run ID:** contract-run-a" -Encoding UTF8
    $latestPath = Join-Path $ReportsBase "deploy-verification-latest.md"
    Remove-Item -LiteralPath $latestPath
    New-Item -ItemType Directory -Path $latestPath | Out-Null
    $latestPublishRejected = $false
    try {
        Save-DeployVerificationReport -MarkdownContent "# latest publish must fail"
    } catch {
        $latestPublishRejected = $true
    }
    Assert-True $latestPublishRejected "latest publish failure must be reported"
    Assert-Equal (Get-ChildItem -LiteralPath $ReportsHistory | Measure-Object).Count 14 "latest publish failure must clean history report and companions"
    Remove-Item -LiteralPath $latestPath
} finally {
    $script:VerificationRunId = $null
    $script:VerificationReleaseManifestHash = $null
    if (Test-Path -LiteralPath $tempRoot) {
        [System.IO.Directory]::Delete($tempRoot, $true)
    }
}

$verificationSource = Get-Content -Raw -LiteralPath (Join-Path $ScriptRoot "run-deploy-verification.ps1")
Assert-True ($verificationSource.Contains('$s2Smoke = "ADVISORY"')) "functional smoke must not default to PASS"
Assert-True ($verificationSource.Contains('$s4Sqs = "ADVISORY"')) "worker smoke must not default to PASS"
Assert-True ($verificationSource.Contains('if ($driftFail -and $driftFail.Count -gt 0) { $consistencySummary = "WARNING" }')) "structural drift must affect consistency summary"
Assert-True ($verificationSource.Contains('if (-not $msgVisibilityOk -or -not $aiVisibilityOk) { $consistencySummary = "WARNING" }')) "SQS visibility drift must affect consistency summary"
Assert-True ($verificationSource.Contains('Add-Finding -Severity "WARNING" -Area "SQS" -Message "Messaging VisibilityTimeout mismatch:')) "SQS visibility drift must affect overall decision"
Assert-True ($verificationSource.Contains('Save-RuntimeImagesUnknownReport -Reason $_.Exception.Message')) "runtime collection failure must replace stale evidence with UNKNOWN"
$runtimeCollectorSource = Get-Content -Raw -LiteralPath (Join-Path $ScriptRoot "resources\api.ps1")
Assert-True ($runtimeCollectorSource.Contains('ManifestHash = $manifestHash')) "runtime evidence must expose the release manifest hash"
Assert-True ($verificationSource.Contains('$manifestEvidence = Get-CurrentReleaseManifestEvidence')) "UNKNOWN runtime evidence must validate the successful release manifest"

Write-Host "Verification contract checks passed." -ForegroundColor Green
