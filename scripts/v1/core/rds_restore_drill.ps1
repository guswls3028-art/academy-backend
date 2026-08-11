$ErrorActionPreference = "Stop"

function Assert-RdsRestoreDrillIdentifier {
    param(
        [Parameter(Mandatory = $true)][string]$TargetIdentifier,
        [Parameter(Mandatory = $true)][string]$SourceIdentifier,
        [Parameter(Mandatory = $true)][string]$RunId
    )

    $expected = "$SourceIdentifier-drill-$RunId"
    if ($TargetIdentifier -cne $expected) {
        throw "RDS drill target must be the exact generated identifier '$expected' (actual='$TargetIdentifier')."
    }
    if ($TargetIdentifier.Length -gt 63 -or $TargetIdentifier -notmatch '^[a-z][a-z0-9-]*[a-z0-9]$') {
        throw "RDS drill target is not a valid RDS identifier: $TargetIdentifier"
    }
}

function Assert-RdsRestoreDrillTags {
    param(
        [Parameter(Mandatory = $true)]$Tags,
        [Parameter(Mandatory = $true)][string]$SourceIdentifier,
        [Parameter(Mandatory = $true)][string]$RunId
    )

    $tagMap = @{}
    foreach ($tag in @($Tags)) {
        if ($null -ne $tag -and $tag.Key) {
            $tagMap[[string]$tag.Key] = [string]$tag.Value
        }
    }
    $expected = @{
        Project = "academy"
        Purpose = "rds-restore-drill"
        SourceDb = $SourceIdentifier
        RunId = $RunId
    }
    foreach ($key in $expected.Keys) {
        if (-not $tagMap.ContainsKey($key) -or $tagMap[$key] -cne $expected[$key]) {
            throw "RDS drill cleanup tag mismatch for '$key' (expected='$($expected[$key])' actual='$($tagMap[$key])')."
        }
    }
}

function Get-RdsRestoreDrillSnapshotAgeHours {
    param(
        [Parameter(Mandatory = $true)][datetimeoffset]$SnapshotCreateTime,
        [datetimeoffset]$Now = [datetimeoffset]::UtcNow
    )

    return [math]::Round(($Now.ToUniversalTime() - $SnapshotCreateTime.ToUniversalTime()).TotalHours, 3)
}

function Resolve-RdsRestoreDrillReportPath {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string]$ReportPath,
        [Parameter(Mandatory = $true)][string]$RunId
    )

    if ($ReportPath -and $ReportPath.Trim()) {
        return [IO.Path]::GetFullPath($ReportPath)
    }

    $workspaceRoot = Split-Path -Parent $RepoRoot
    if ($RepoRoot -match '^(.*?)[\\/]_worktrees[\\/]sessions[\\/]') {
        $workspaceRoot = $matches[1]
    }
    $artifactRoot = Join-Path $workspaceRoot "_artifacts\rds-restore-drill"
    return Join-Path $artifactRoot "dr-drill-$RunId.md"
}

function ConvertTo-RdsRestoreDrillMarkdown {
    param([Parameter(Mandatory = $true)]$Evidence)

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("# Academy RDS Restore Drill")
    $lines.Add("")
    $lines.Add("- Run ID: ``$($Evidence.RunId)``")
    $lines.Add("- Started (UTC): ``$($Evidence.StartedAtUtc)``")
    $lines.Add("- Finished (UTC): ``$($Evidence.FinishedAtUtc)``")
    $lines.Add("- Result: **$($Evidence.Result)**")
    $lines.Add("- Source DB: ``$($Evidence.SourceDb)``")
    $lines.Add("- Source snapshot: ``$($Evidence.SourceSnapshot)``")
    $lines.Add("- Snapshot age: ``$($Evidence.SnapshotAgeHours) hours``")
    $lines.Add("- Drill target: ``$($Evidence.TargetDb)``")
    $lines.Add("- Drill class: ``$($Evidence.TargetClass)``")
    $lines.Add("- Restore duration: ``$($Evidence.RestoreDurationSeconds) seconds``")
    $lines.Add("- Cleanup: **$($Evidence.CleanupResult)**")
    $lines.Add("")
    $lines.Add("## Safety and verification")
    $lines.Add("")
    $lines.Add("| Check | Result |")
    $lines.Add("|---|---|")
    foreach ($check in @($Evidence.Checks)) {
        $detail = ([string]$check.Detail).Replace("|", "\|").Replace("`r", " ").Replace("`n", " ")
        $lines.Add("| $($check.Name) | $($check.Status): $detail |")
    }
    if ($Evidence.SourceCounts -and $Evidence.RestoredCounts) {
        $lines.Add("")
        $lines.Add("## Snapshot row-count comparison")
        $lines.Add("")
        $lines.Add("Counts only; no tenant or user row data is written to this report.")
        $lines.Add("")
        $lines.Add("| Table | Live source | Restored snapshot | Difference |")
        $lines.Add("|---|---:|---:|---:|")
        foreach ($name in @($Evidence.SourceCounts.PSObject.Properties.Name | Sort-Object)) {
            $source = [long]$Evidence.SourceCounts.$name
            $restored = [long]$Evidence.RestoredCounts.$name
            $lines.Add("| ``$name`` | $source | $restored | $($restored - $source) |")
        }
    }
    if ($Evidence.Error) {
        $lines.Add("")
        $lines.Add("## Failure")
        $lines.Add("")
        $lines.Add(([string]$Evidence.Error).Replace("`r", " ").Replace("`n", " "))
    }
    $lines.Add("")
    return ($lines -join [Environment]::NewLine)
}
