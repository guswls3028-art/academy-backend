# Persist drift/evidence/audit to docs/00-SSOT/v4/reports/ and history/.
$ErrorActionPreference = "Stop"
$ReportsScriptDir = $PSScriptRoot
$ReportsRepoRoot = (Resolve-Path (Join-Path $ReportsScriptDir "..\..\..")).Path
$ReportsBase = Join-Path $ReportsRepoRoot "docs\00-SSOT\v4\reports"
$ReportsHistory = Join-Path $ReportsBase "history"

function Get-ReportsDir { return $ReportsBase }
function Get-ReportsHistoryDir {
    if (-not (Test-Path $ReportsHistory)) { New-Item -ItemType Directory -Path $ReportsHistory -Force | Out-Null }
    return $ReportsHistory
}

function Save-DriftReport {
    param([System.Collections.ArrayList]$Rows)
    $dir = Get-ReportsDir
    $historyDir = Get-ReportsHistoryDir
    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
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
    $content = $sb.ToString()
    $latestPath = Join-Path $dir "drift.latest.md"
    $historyPath = Join-Path $historyDir "${ts}-drift.md"
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Set-Content -Path $historyPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Drift report: $latestPath" -ForegroundColor DarkGray
}

function Save-EvidenceReport {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $historyDir = Get-ReportsHistoryDir
    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    $header = "# Evidence / Audit`n**Generated:** $(Get-Date -Format 'o')`n`n"
    $content = $header + $MarkdownContent
    $latestPath = Join-Path $dir "audit.latest.md"
    $historyPath = Join-Path $historyDir "${ts}-audit.md"
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Set-Content -Path $historyPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Evidence report: $latestPath" -ForegroundColor DarkGray
}

function Save-VerifyReport {
    param([string]$MarkdownContent)
    $dir = Get-ReportsDir
    $historyDir = Get-ReportsHistoryDir
    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    $header = "# Verify v4`n**Generated:** $(Get-Date -Format 'o')`n`n"
    $content = $header + $MarkdownContent
    $latestPath = Join-Path $dir "verify.latest.md"
    $historyPath = Join-Path $historyDir "${ts}-verify.md"
    Set-Content -Path $latestPath -Value $content -Encoding UTF8 -Force
    Set-Content -Path $historyPath -Value $content -Encoding UTF8 -Force
    Write-Host "  Verify report: $latestPath" -ForegroundColor DarkGray
}
