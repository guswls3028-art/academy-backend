# Persist drift/evidence/audit to docs/reports/ and history/.
# AWS/Cloudflare credentials are supplied by the caller through the intended profile or process environment; this script does not load backend/.env.
$ErrorActionPreference = "Stop"
$ReportsScriptDir = $PSScriptRoot
$ReportsRepoRoot = (Resolve-Path (Join-Path $ReportsScriptDir "..\..\..")).Path
$ReportsBase = Join-Path $ReportsRepoRoot "docs\reports"
$ReportsHistory = Join-Path $ReportsBase "history"

function Get-ReportsDir {
    if (-not (Test-Path $ReportsBase)) { New-Item -ItemType Directory -Path $ReportsBase -Force | Out-Null }
    return $ReportsBase
}
function Get-ReportsHistoryDir {
    if (-not (Test-Path $ReportsHistory)) { New-Item -ItemType Directory -Path $ReportsHistory -Force | Out-Null }
    return $ReportsHistory
}

function Remove-StaleVerificationArtifacts {
    param(
        [string]$ReportsDir,
        [string]$HistoryDir,
        [datetime]$Cutoff = (Get-Date).AddHours(-24)
    )
    $staleFiles = @(
        Get-ChildItem -LiteralPath $HistoryDir -File -Filter "*.tmp" -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath $ReportsDir -File -Filter "deploy-verification-latest.md.*.tmp" -ErrorAction SilentlyContinue
    ) | Where-Object { $_.LastWriteTime -lt $Cutoff }
    foreach ($file in $staleFiles) {
        Remove-Item -LiteralPath $file.FullName -Force
    }
    $staleDirs = @(
        Get-ChildItem -LiteralPath $HistoryDir -Directory -Filter ".staging-*" -Force -ErrorAction SilentlyContinue
    ) | Where-Object { $_.LastWriteTime -lt $Cutoff }
    foreach ($directory in $staleDirs) {
        [System.IO.Directory]::Delete($directory.FullName, $true)
    }
}

function Normalize-ReportContent {
    param([string]$Content)
    if ($null -eq $Content) { return "" }
    $lines = $Content -split "\r?\n"
    $trimmed = $lines | ForEach-Object { $_.TrimEnd() }
    return (($trimmed -join "`n").TrimEnd())
}

function New-ReportRunId {
    return "$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
}

function Add-VerificationRunMarker {
    param([string]$Content)
    if (-not $script:VerificationRunId) { return $Content }
    return "$Content`n`n**Verification Run ID:** $($script:VerificationRunId)"
}

function Save-DriftReport {
    param([System.Collections.ArrayList]$Rows)
    $dir = Get-ReportsDir
    $historyDir = Get-ReportsHistoryDir
    Remove-StaleVerificationArtifacts -ReportsDir $dir -HistoryDir $historyDir
    $runId = New-ReportRunId
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine("# Drift — SSOT vs actual")
    [void]$sb.AppendLine("**Generated:** $(Get-Date -Format 'o')")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("| ResourceType | Name | Expected | Actual | Action |")
    [void]$sb.AppendLine("|--------------|------|----------|--------|--------|")
    if ($Rows -and $Rows.Count -gt 0) {
        foreach ($row in $Rows) {
            [void]$sb.AppendLine("| $($row.ResourceType) | $($row.Name) | $($row.Expected) | $($row.Actual) | $($row.Action) |")
        }
    } else {
        [void]$sb.AppendLine("| (none) | - | - | - | NoOp |")
    }
    $content = Normalize-ReportContent -Content (Add-VerificationRunMarker -Content $sb.ToString())
    $latestPath = Join-Path $dir "drift.latest.md"
    $historyPath = Join-Path $historyDir "${runId}-drift.md"
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Set-Content -Path $historyPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Drift report: $latestPath" -ForegroundColor DarkGray
}

function Save-EvidenceReport {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $historyDir = Get-ReportsHistoryDir
    $runId = New-ReportRunId
    $header = "# Evidence / Audit`n**Generated:** $(Get-Date -Format 'o')`n`n"
    $content = Normalize-ReportContent -Content (Add-VerificationRunMarker -Content ($header + $MarkdownContent))
    $latestPath = Join-Path $dir "audit.latest.md"
    $historyPath = Join-Path $historyDir "${runId}-audit.md"
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Set-Content -Path $historyPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Evidence report: $latestPath" -ForegroundColor DarkGray
}

function Save-VerifyReport {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $historyDir = Get-ReportsHistoryDir
    $runId = New-ReportRunId
    $header = "# Verify v1`n**Generated:** $(Get-Date -Format 'o')`n`n"
    $content = Normalize-ReportContent -Content (Add-VerificationRunMarker -Content ($header + $MarkdownContent))
    $latestPath = Join-Path $dir "verify.latest.md"
    $historyPath = Join-Path $historyDir "${runId}-verify.md"
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Set-Content -Path $historyPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Verify report: $latestPath" -ForegroundColor DarkGray
}

function Save-DeployVerificationReport {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $historyDir = Get-ReportsHistoryDir
    Remove-StaleVerificationArtifacts -ReportsDir $dir -HistoryDir $historyDir
    $runId = New-ReportRunId
    $latestPath = Join-Path $dir "deploy-verification-latest.md"
    $content = Normalize-ReportContent -Content (Add-VerificationRunMarker -Content $MarkdownContent)

    $bundle = @(
        "audit.latest.md"
        "drift.latest.md"
        "runtime-images.latest.md"
        "consistency.latest.md"
        "front-connection.latest.md"
        "release-manifest.latest.json"
    )
    $missing = @($bundle | Where-Object { -not (Test-Path -LiteralPath (Join-Path $dir $_) -PathType Leaf) })
    if ($missing.Count -gt 0) {
        throw "Deploy verification history requires all companion evidence files. Missing: $($missing -join ', ')"
    }

    $historyBundleLinks = [System.Collections.Generic.List[string]]::new()
    $localBundleLinks = [System.Collections.Generic.List[string]]::new()
    $sourceHashes = [ordered]@{}
    $verificationMarker = if ($script:VerificationRunId) { "**Verification Run ID:** $($script:VerificationRunId)" } else { "" }
    foreach ($name in $bundle) {
        $sourcePath = Join-Path $dir $name
        if ($verificationMarker -and $name -ne "release-manifest.latest.json") {
            $sourceContent = Get-Content -Raw -LiteralPath $sourcePath
            if (-not $sourceContent.Contains($verificationMarker)) {
                throw "Companion evidence does not belong to verification run $($script:VerificationRunId): $name"
            }
        }
        $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
        if ($verificationMarker -and $name -eq "release-manifest.latest.json") {
            if (-not $script:VerificationReleaseManifestHash) {
                throw "Release manifest hash is missing for verification run $($script:VerificationRunId)."
            }
            if ($sourceHash -ne $script:VerificationReleaseManifestHash) {
                throw "Release manifest changed after runtime image evidence was collected."
            }
        }
        $sourceHashes[$sourcePath] = $sourceHash
    }

    $historyPath = Join-Path $historyDir "${runId}-deploy-verification.md"
    $historyTempPath = "${historyPath}.tmp"
    $latestTempPath = "${latestPath}.${runId}.tmp"
    $stagingDir = Join-Path $historyDir ".staging-$runId"
    $finalBundleDir = Join-Path $historyDir $runId
    $historyPublished = $false
    $bundlePublished = $false
    try {
        [System.IO.Directory]::CreateDirectory($stagingDir) | Out-Null
        foreach ($name in $bundle) {
            $sourcePath = Join-Path $dir $name
            $snapshotPath = Join-Path $stagingDir $name
            $sourceHashBefore = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
            if ($sourceHashBefore -ne $sourceHashes[$sourcePath]) {
                throw "Companion evidence set changed before snapshotting: $name"
            }
            Copy-Item -LiteralPath $sourcePath -Destination $snapshotPath
            $sourceHashAfter = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
            $snapshotHash = (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash
            if ($sourceHashes[$sourcePath] -ne $sourceHashAfter -or $sourceHashAfter -ne $snapshotHash) {
                throw "Companion evidence changed while snapshotting: $name"
            }
            $historyBundleLinks.Add("- [$name](./$runId/$name)")
            $localBundleLinks.Add("- [$name](./$name)")
        }
        foreach ($entry in $sourceHashes.GetEnumerator()) {
            if ((Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash -ne $entry.Value) {
                throw "Companion evidence set changed before bundle completion: $($entry.Key)"
            }
        }

        $historyContent = $content
        $historyContent += "`n`n## Immutable Evidence Bundle`n`n"
        $historyContent += $historyBundleLinks -join "`n"
        $localHistoryContent = $content
        $localHistoryContent += "`n`n## Immutable Evidence Bundle`n`n"
        $localHistoryContent += $localBundleLinks -join "`n"
        Set-Content -LiteralPath (Join-Path $stagingDir "deploy-verification.md") -Value $localHistoryContent -Encoding UTF8
        [System.IO.Directory]::Move($stagingDir, $finalBundleDir)
        $bundlePublished = $true
        Set-Content -LiteralPath $historyTempPath -Value $historyContent -Encoding UTF8
        [System.IO.File]::Move($historyTempPath, $historyPath, $false)
        $historyPublished = $true
        Set-Content -LiteralPath $latestTempPath -Value $content -Encoding UTF8
        [System.IO.File]::Move($latestTempPath, $latestPath, $true)
    } catch {
        $cleanupPaths = @($historyTempPath, $latestTempPath)
        if ($historyPublished) { $cleanupPaths += $historyPath }
        foreach ($artifactPath in $cleanupPaths) {
            if (Test-Path -LiteralPath $artifactPath -PathType Leaf) {
                Remove-Item -LiteralPath $artifactPath -Force
            }
        }
        if (Test-Path -LiteralPath $stagingDir -PathType Container) {
            [System.IO.Directory]::Delete($stagingDir, $true)
        }
        if ($bundlePublished -and (Test-Path -LiteralPath $finalBundleDir -PathType Container)) {
            [System.IO.Directory]::Delete($finalBundleDir, $true)
        }
        throw
    }
    Write-Host "  Deploy verification report: $latestPath" -ForegroundColor DarkGray
}

function Save-V1FinalReportInReports {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $latestPath = Join-Path $dir "V1-FINAL-REPORT.md"
    $content = Normalize-ReportContent -Content (Add-VerificationRunMarker -Content $MarkdownContent)
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Write-Host "  V1 Final report (reports): $latestPath" -ForegroundColor DarkGray
}

function Save-RuntimeImagesReport {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $latestPath = Join-Path $dir "runtime-images.latest.md"
    $content = Normalize-ReportContent -Content (Add-VerificationRunMarker -Content $MarkdownContent)
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Runtime images report: $latestPath" -ForegroundColor DarkGray
}

function Save-ConsistencyReport {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $latestPath = Join-Path $dir "consistency.latest.md"
    $content = Normalize-ReportContent -Content (Add-VerificationRunMarker -Content $MarkdownContent)
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Consistency report: $latestPath" -ForegroundColor DarkGray
}

function Save-FrontConnectionReport {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $latestPath = Join-Path $dir "front-connection.latest.md"
    $content = Normalize-ReportContent -Content (Add-VerificationRunMarker -Content $MarkdownContent)
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Front connection report: $latestPath" -ForegroundColor DarkGray
}
