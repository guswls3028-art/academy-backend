$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\rds_restore_drill.ps1")

function Assert-Throws {
    param([scriptblock]$Action, [string]$Message)
    try { & $Action } catch { return }
    throw $Message
}

$source = "academy-db"
$runId = "20260811-195500-abc123"
$target = "$source-drill-$runId"
Assert-RdsRestoreDrillIdentifier -TargetIdentifier $target -SourceIdentifier $source -RunId $runId
Assert-Throws {
    Assert-RdsRestoreDrillIdentifier -TargetIdentifier $source -SourceIdentifier $source -RunId $runId
} "Source DB identifier must never be accepted as a drill target."
Assert-Throws {
    Assert-RdsRestoreDrillIdentifier -TargetIdentifier "$source-drill-other" -SourceIdentifier $source -RunId $runId
} "A foreign drill identifier must never be accepted for cleanup."

$tags = @(
    [pscustomobject]@{ Key = "Project"; Value = "academy" }
    [pscustomobject]@{ Key = "Purpose"; Value = "rds-restore-drill" }
    [pscustomobject]@{ Key = "SourceDb"; Value = $source }
    [pscustomobject]@{ Key = "RunId"; Value = $runId }
)
Assert-RdsRestoreDrillTags -Tags $tags -SourceIdentifier $source -RunId $runId
Assert-Throws {
    Assert-RdsRestoreDrillTags -Tags @($tags | Where-Object { $_.Key -ne "RunId" }) -SourceIdentifier $source -RunId $runId
} "Cleanup must fail closed without the exact RunId tag."
Assert-Throws {
    $wrong = @($tags | ForEach-Object { if ($_.Key -eq "SourceDb") { [pscustomobject]@{ Key = "SourceDb"; Value = "other-db" } } else { $_ } })
    Assert-RdsRestoreDrillTags -Tags $wrong -SourceIdentifier $source -RunId $runId
} "Cleanup must fail closed for a foreign source tag."

$now = [datetimeoffset]"2026-08-11T12:00:00Z"
$age = Get-RdsRestoreDrillSnapshotAgeHours -SnapshotCreateTime ([datetimeoffset]"2026-08-10T18:00:00Z") -Now $now
if ($age -ne 18) { throw "Snapshot age calculation drifted: $age" }

$scriptPath = Join-Path $PSScriptRoot "run-rds-restore-drill.ps1"
$sourceText = Get-Content -Raw -LiteralPath $scriptPath
$requiredContracts = @(
    'finally',
    'Assert-RdsRestoreDrillIdentifier',
    'Assert-RdsRestoreDrillTags',
    '--no-publicly-accessible',
    '--no-multi-az',
    '--no-deletion-protection',
    '--skip-final-snapshot',
    'Invoke-ApiSsmDockerExec',
    'pending_migrations',
    'migration_hash',
    'vector_version'
)
foreach ($contract in $requiredContracts) {
    if (-not $sourceText.Contains($contract)) { throw "RDS drill script is missing safety contract: $contract" }
}
if ($sourceText -match 'delete-db-instance[^\r\n]+SourceDbIdentifier') {
    throw "RDS drill cleanup must never pass SourceDbIdentifier to delete-db-instance."
}

Write-Host "RDS_RESTORE_DRILL_CONTRACT_PASS" -ForegroundColor Green
