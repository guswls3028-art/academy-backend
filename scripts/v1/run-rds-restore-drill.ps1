# Restores the latest automated production RDS snapshot into an isolated,
# tagged temporary instance, verifies it through the live API container's
# private network path, and deletes only the exact generated target.

[CmdletBinding()]
param(
    [string]$AwsProfile = "default",
    [string]$Env = "prod",
    [string]$SourceDbIdentifier = "",
    [string]$DbInstanceClass = "db.t4g.micro",
    [ValidateRange(1, 168)][int]$MaxSnapshotAgeHours = 36,
    [ValidateRange(5, 120)][int]$RestoreTimeoutMinutes = 45,
    [ValidateRange(5, 120)][int]$DeleteTimeoutMinutes = 30,
    [string]$ReportPath = "",
    [switch]$Plan
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Text.Encoding]::UTF8
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path

if ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) {
    $env:AWS_DEFAULT_REGION = "ap-northeast-2"
}

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\rds_restore_drill.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")
. (Join-Path $ScriptRoot "core\remote.ps1")

$null = Load-SSOT -Env $Env
$Region = $script:Region
if (-not $SourceDbIdentifier) { $SourceDbIdentifier = $script:RdsDbIdentifier }
if (-not $SourceDbIdentifier -or $SourceDbIdentifier -cne $script:RdsDbIdentifier) {
    throw "Source DB must be the exact RDS SSOT identifier '$($script:RdsDbIdentifier)'."
}
if ($DbInstanceClass -notmatch '^db\.[a-z0-9]+\.[a-z0-9]+$') {
    throw "Invalid drill DB instance class: $DbInstanceClass"
}

$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss") + "-" + [Guid]::NewGuid().ToString("N").Substring(0, 6)
$TargetDbIdentifier = "$SourceDbIdentifier-drill-$RunId"
Assert-RdsRestoreDrillIdentifier -TargetIdentifier $TargetDbIdentifier -SourceIdentifier $SourceDbIdentifier -RunId $RunId
$ReportPath = Resolve-RdsRestoreDrillReportPath -RepoRoot $RepoRoot -ReportPath $ReportPath -RunId $RunId

$checks = [System.Collections.ArrayList]::new()
$startedAt = [datetimeoffset]::UtcNow
$restoreStartedAt = $null
$restoreFinishedAt = $null
$sourceSnapshot = ""
$snapshotAgeHours = 0
$sourceProbe = $null
$restoredProbe = $null
$primaryError = $null
$cleanupError = $null
$cleanupResult = "NOT_REQUIRED"
$drillSucceeded = $false

function Add-DrillCheck {
    param([string]$Name, [string]$Status, [string]$Detail)
    [void]$checks.Add([pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail })
    $color = if ($Status -eq "PASS") { "Green" } elseif ($Status -eq "PLAN") { "Cyan" } else { "Red" }
    Write-Host "[$Status] $Name - $Detail" -ForegroundColor $color
}

function Invoke-DrillAwsJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments, [switch]$AllowMissing)
    try {
        $output = @(Invoke-Aws -ArgsArray $Arguments -ErrorMessage "AWS command failed")
    } catch {
        if ($AllowMissing -and $_.Exception.Message -match 'DBInstanceNotFound') { return $null }
        throw
    }
    $text = ($output | Out-String).Trim()
    if (-not $text) { return $null }
    try { return $text | ConvertFrom-Json } catch { throw "AWS returned invalid JSON for '$($Arguments[0..1] -join ' ')'." }
}

function Get-DrillDbInstance {
    param([Parameter(Mandatory = $true)][string]$Identifier, [switch]$AllowMissing)
    $response = Invoke-DrillAwsJson -Arguments @(
        "rds", "describe-db-instances", "--db-instance-identifier", $Identifier,
        "--region", $Region, "--output", "json"
    ) -AllowMissing:$AllowMissing
    if ($null -eq $response -or -not $response.DBInstances -or $response.DBInstances.Count -eq 0) { return $null }
    return @($response.DBInstances)[0]
}

function Get-DrillTags {
    param([Parameter(Mandatory = $true)][string]$Arn)
    $response = Invoke-DrillAwsJson -Arguments @(
        "rds", "list-tags-for-resource", "--resource-name", $Arn,
        "--region", $Region, "--output", "json"
    )
    return @($response.TagList)
}

function Wait-DrillDbAvailable {
    param([Parameter(Mandatory = $true)][string]$Identifier, [int]$TimeoutMinutes)
    $deadline = [datetimeoffset]::UtcNow.AddMinutes($TimeoutMinutes)
    do {
        $instance = Get-DrillDbInstance -Identifier $Identifier -AllowMissing
        if ($null -eq $instance) { throw "Drill DB disappeared while waiting for availability: $Identifier" }
        $status = [string]$instance.DBInstanceStatus
        Write-Host "RDS restore status: $status"
        if ($status -eq "available") { return $instance }
        if ($status -in @("failed", "incompatible-restore", "incompatible-parameters", "storage-full")) {
            throw "Drill DB entered terminal restore status '$status'."
        }
        Start-Sleep -Seconds 30
    } while ([datetimeoffset]::UtcNow -lt $deadline)
    throw "Timed out after $TimeoutMinutes minutes waiting for drill DB availability."
}

function Wait-DrillDbDeleted {
    param([Parameter(Mandatory = $true)][string]$Identifier, [int]$TimeoutMinutes)
    $deadline = [datetimeoffset]::UtcNow.AddMinutes($TimeoutMinutes)
    do {
        $instance = Get-DrillDbInstance -Identifier $Identifier -AllowMissing
        if ($null -eq $instance) { return }
        Write-Host "RDS cleanup status: $($instance.DBInstanceStatus)"
        Start-Sleep -Seconds 30
    } while ([datetimeoffset]::UtcNow -lt $deadline)
    throw "Timed out after $TimeoutMinutes minutes waiting for drill DB deletion."
}

function Invoke-DrillDatabaseProbe {
    param([string]$HostName = "")
    $probe = @'
import hashlib
import json
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

tables = [
    "django_migrations",
    "core_tenant",
    "accounts_user",
    "students_student",
    "exams_exam",
    "results_exam_result",
    "fee_payment",
    "messaging_schedulednotification",
]
with connection.cursor() as cursor:
    present = set(connection.introspection.table_names(cursor))
    missing = [name for name in tables if name not in present]
    counts = {}
    for name in tables:
        if name in present:
            cursor.execute(f'SELECT COUNT(*) FROM "{name}"')
            counts[name] = int(cursor.fetchone()[0])
    cursor.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
    migrations = [[str(app), str(name)] for app, name in cursor.fetchall()]
    cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    vector_row = cursor.fetchone()
    cursor.execute("SELECT current_database()")
    database_name = str(cursor.fetchone()[0])
executor = MigrationExecutor(connection)
pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
payload = {
    "database_name": database_name,
    "missing_tables": missing,
    "counts": counts,
    "migration_count": len(migrations),
    "migration_hash": hashlib.sha256(json.dumps(migrations, separators=(",", ":")).encode()).hexdigest(),
    "pending_migrations": len(pending),
    "vector_version": str(vector_row[0]) if vector_row else "",
}
print("DR_PROBE_JSON=" + json.dumps(payload, separators=(",", ":"), sort_keys=True))
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($probe))
    $hostPrefix = ""
    if ($HostName) {
        if ($HostName -notmatch '^[a-z0-9.-]+\.rds\.amazonaws\.com$') { throw "Unexpected restored RDS endpoint format." }
        $hostPrefix = "export DB_HOST='$HostName'; "
    }
    $command = "${hostPrefix}printf '%s' '$encoded' | base64 -d | python manage.py shell"
    $result = @(Invoke-ApiSsmDockerExec -Command $command -TimeoutSec 240)[0]
    if ($null -eq $result -or $result.Status -ne "Success" -or $result.ResponseCode -ne 0) {
        $detail = if ($result) { [string]$result.StandardErrorContent } else { "no SSM result" }
        throw "Database probe failed: $detail"
    }
    $line = @(([string]$result.StandardOutputContent -split "`r?`n") | Where-Object { $_ -like "DR_PROBE_JSON=*" }) | Select-Object -Last 1
    if (-not $line) { throw "Database probe returned no DR_PROBE_JSON marker." }
    return $line.Substring("DR_PROBE_JSON=".Length) | ConvertFrom-Json
}

try {
    Write-Host "=== Academy isolated RDS restore drill ===" -ForegroundColor Cyan
    Write-Host "RunId=$RunId source=$SourceDbIdentifier target=$TargetDbIdentifier region=$Region plan=$Plan"

    $identity = Invoke-DrillAwsJson -Arguments @("sts", "get-caller-identity", "--output", "json")
    if (-not $identity.Account -or [string]$identity.Account -cne [string]$script:AccountId) {
        throw "AWS account does not match SSOT."
    }
    Add-DrillCheck "aws_identity" "PASS" "Authenticated account matches SSOT."

    $source = Get-DrillDbInstance -Identifier $SourceDbIdentifier
    if ($null -eq $source) { throw "Source DB not found: $SourceDbIdentifier" }
    if ($source.DBInstanceStatus -ne "available") { throw "Source DB is not available: $($source.DBInstanceStatus)" }
    if ($source.Engine -ne "postgres" -or -not [bool]$source.StorageEncrypted) { throw "Source DB must be encrypted PostgreSQL." }
    if ([bool]$source.PubliclyAccessible) { throw "Source DB must remain private." }
    if (-not [bool]$source.DeletionProtection) { throw "Source DB deletion protection must be enabled." }
    if ([int]$source.BackupRetentionPeriod -lt 7) { throw "Source DB backup retention is below 7 days." }
    Add-DrillCheck "source_safety" "PASS" "available, private, encrypted, deletion-protected, retention=$($source.BackupRetentionPeriod)d"

    $allInstances = Invoke-DrillAwsJson -Arguments @("rds", "describe-db-instances", "--region", $Region, "--output", "json")
    $prefix = "$SourceDbIdentifier-drill-"
    $residue = @($allInstances.DBInstances | Where-Object { ([string]$_.DBInstanceIdentifier).StartsWith($prefix, [StringComparison]::Ordinal) })
    if ($residue.Count -gt 0) {
        throw "Existing RDS drill residue must be reviewed before a new drill: $(@($residue.DBInstanceIdentifier) -join ', ')"
    }
    Add-DrillCheck "preexisting_residue" "PASS" "No existing '$prefix*' DB instances."

    $snapshotsResponse = Invoke-DrillAwsJson -Arguments @(
        "rds", "describe-db-snapshots", "--db-instance-identifier", $SourceDbIdentifier,
        "--snapshot-type", "automated", "--region", $Region, "--output", "json"
    )
    $snapshots = @($snapshotsResponse.DBSnapshots | Where-Object { $_.Status -eq "available" } | Sort-Object { [datetimeoffset]$_.SnapshotCreateTime } -Descending)
    if ($snapshots.Count -eq 0) { throw "No available automated snapshot found for $SourceDbIdentifier." }
    $snapshot = $snapshots[0]
    $sourceSnapshot = [string]$snapshot.DBSnapshotIdentifier
    $snapshotAgeHours = Get-RdsRestoreDrillSnapshotAgeHours -SnapshotCreateTime ([datetimeoffset]$snapshot.SnapshotCreateTime)
    if ($snapshotAgeHours -lt 0 -or $snapshotAgeHours -gt $MaxSnapshotAgeHours) {
        throw "Latest automated snapshot age is outside 0..$MaxSnapshotAgeHours hours: $snapshotAgeHours"
    }
    if (-not [bool]$snapshot.Encrypted) { throw "Latest automated snapshot is not encrypted." }
    Add-DrillCheck "snapshot_freshness" "PASS" "latest automated snapshot age=${snapshotAgeHours}h"

    $orderable = Invoke-DrillAwsJson -Arguments @(
        "rds", "describe-orderable-db-instance-options", "--engine", "postgres",
        "--engine-version", ([string]$source.EngineVersion), "--db-instance-class", $DbInstanceClass,
        "--region", $Region, "--output", "json"
    )
    if (-not $orderable.OrderableDBInstanceOptions -or $orderable.OrderableDBInstanceOptions.Count -eq 0) {
        throw "$DbInstanceClass is not orderable for PostgreSQL $($source.EngineVersion) in $Region."
    }
    Add-DrillCheck "target_class" "PASS" "$DbInstanceClass is orderable for PostgreSQL $($source.EngineVersion)."

    if ($Plan) {
        Add-DrillCheck "plan" "PLAN" "Would restore $sourceSnapshot to $TargetDbIdentifier and delete it after verification."
        $drillSucceeded = $true
    } else {
        $sourceProbe = Invoke-DrillDatabaseProbe
        if ($sourceProbe.missing_tables.Count -gt 0 -or [int]$sourceProbe.pending_migrations -ne 0) {
            throw "Live source DB probe is not healthy."
        }
        Add-DrillCheck "source_probe" "PASS" "critical tables present, pending migrations=0, counts captured without row data"

        $subnetGroup = [string]$source.DBSubnetGroup.DBSubnetGroupName
        $securityGroups = @($source.VpcSecurityGroups | ForEach-Object { [string]$_.VpcSecurityGroupId } | Where-Object { $_ })
        if (-not $subnetGroup -or $securityGroups.Count -eq 0) { throw "Source subnet group or VPC security groups are missing." }

        $restoreArgs = [System.Collections.Generic.List[string]]::new()
        @(
            "rds", "restore-db-instance-from-db-snapshot",
            "--db-instance-identifier", $TargetDbIdentifier,
            "--db-snapshot-identifier", ([string]$snapshot.DBSnapshotArn),
            "--db-instance-class", $DbInstanceClass,
            "--db-subnet-group-name", $subnetGroup,
            "--no-publicly-accessible", "--no-multi-az", "--no-deletion-protection",
            "--vpc-security-group-ids"
        ) | ForEach-Object { $restoreArgs.Add($_) }
        foreach ($securityGroup in $securityGroups) { $restoreArgs.Add($securityGroup) }
        @(
            "--tags",
            "Key=Project,Value=academy",
            "Key=Purpose,Value=rds-restore-drill",
            "Key=SourceDb,Value=$SourceDbIdentifier",
            "Key=RunId,Value=$RunId",
            "--region", $Region, "--output", "json"
        ) | ForEach-Object { $restoreArgs.Add($_) }

        $restoreStartedAt = [datetimeoffset]::UtcNow
        $restore = Invoke-DrillAwsJson -Arguments $restoreArgs.ToArray()
        if (-not $restore.DBInstance -or $restore.DBInstance.DBInstanceIdentifier -cne $TargetDbIdentifier) {
            throw "RDS restore did not return the exact generated target."
        }
        Add-DrillCheck "restore_started" "PASS" "Exact tagged target creation accepted."

        $target = Wait-DrillDbAvailable -Identifier $TargetDbIdentifier -TimeoutMinutes $RestoreTimeoutMinutes
        $restoreFinishedAt = [datetimeoffset]::UtcNow
        if ([bool]$target.PubliclyAccessible -or [bool]$target.MultiAZ -or -not [bool]$target.StorageEncrypted) {
            throw "Restored DB violates private, Single-AZ, or encryption constraints."
        }
        if ([string]$target.DBInstanceClass -cne $DbInstanceClass) { throw "Restored DB class mismatch." }
        if ([string]$target.DBSubnetGroup.DBSubnetGroupName -cne $subnetGroup) { throw "Restored DB subnet group mismatch." }
        $targetSecurityGroups = @($target.VpcSecurityGroups | ForEach-Object { [string]$_.VpcSecurityGroupId } | Sort-Object)
        if (($targetSecurityGroups -join ',') -cne (@($securityGroups | Sort-Object) -join ',')) { throw "Restored DB security group mismatch." }
        $tags = Get-DrillTags -Arn ([string]$target.DBInstanceArn)
        Assert-RdsRestoreDrillTags -Tags $tags -SourceIdentifier $SourceDbIdentifier -RunId $RunId
        Add-DrillCheck "restored_boundary" "PASS" "private, encrypted, Single-AZ, exact subnet/SG/class/tags"

        $endpoint = [string]$target.Endpoint.Address
        if ($endpoint -notmatch '^[a-z0-9.-]+\.rds\.amazonaws\.com$') { throw "Restored DB endpoint is missing or unexpected." }
        $migrateCommand = "export DB_HOST='$endpoint'; python manage.py migrate --noinput"
        $migrationResult = @(Invoke-ApiSsmDockerExec -Command $migrateCommand -TimeoutSec 900)[0]
        if ($null -eq $migrationResult -or $migrationResult.Status -ne "Success" -or $migrationResult.ResponseCode -ne 0) {
            $detail = if ($migrationResult) { [string]$migrationResult.StandardErrorContent } else { "no SSM result" }
            throw "Restored DB migration failed: $detail"
        }
        Add-DrillCheck "restored_migrate" "PASS" "Current release migrations applied only to isolated target."

        $restoredProbe = Invoke-DrillDatabaseProbe -HostName $endpoint
        if ($restoredProbe.missing_tables.Count -gt 0) { throw "Restored DB is missing critical tables." }
        if ([int]$restoredProbe.pending_migrations -ne 0) { throw "Restored DB has pending migrations." }
        if (-not [string]$restoredProbe.vector_version) { throw "Restored DB is missing the pgvector extension." }
        if ([string]$restoredProbe.migration_hash -cne [string]$sourceProbe.migration_hash) { throw "Restored migration hash differs from live source after migrate." }
        foreach ($criticalTable in @("django_migrations", "core_tenant", "accounts_user")) {
            if ([long]$restoredProbe.counts.$criticalTable -le 0) { throw "Restored critical table is empty: $criticalTable" }
        }
        Add-DrillCheck "restored_probe" "PASS" "migration hash matches live, pgvector=$($restoredProbe.vector_version), critical counts nonzero"
        $drillSucceeded = $true
    }
} catch {
    $primaryError = $_
    Add-DrillCheck "drill" "FAIL" $_.Exception.Message
} finally {
    if (-not $Plan) {
        try {
            $targetForCleanup = Get-DrillDbInstance -Identifier $TargetDbIdentifier -AllowMissing
            if ($null -ne $targetForCleanup) {
                Assert-RdsRestoreDrillIdentifier -TargetIdentifier ([string]$targetForCleanup.DBInstanceIdentifier) -SourceIdentifier $SourceDbIdentifier -RunId $RunId
                $cleanupTags = Get-DrillTags -Arn ([string]$targetForCleanup.DBInstanceArn)
                Assert-RdsRestoreDrillTags -Tags $cleanupTags -SourceIdentifier $SourceDbIdentifier -RunId $RunId
                $null = Invoke-DrillAwsJson -Arguments @(
                    "rds", "delete-db-instance", "--db-instance-identifier", $TargetDbIdentifier,
                    "--skip-final-snapshot", "--delete-automated-backups",
                    "--region", $Region, "--output", "json"
                )
                Wait-DrillDbDeleted -Identifier $TargetDbIdentifier -TimeoutMinutes $DeleteTimeoutMinutes
            }
            $postCleanup = Get-DrillDbInstance -Identifier $TargetDbIdentifier -AllowMissing
            if ($null -ne $postCleanup) { throw "Exact drill target still exists after cleanup." }
            $cleanupResult = "PASS"
            Add-DrillCheck "cleanup" "PASS" "Exact RunId-tagged target is absent."
        } catch {
            $cleanupError = $_
            $cleanupResult = "FAIL"
            Add-DrillCheck "cleanup" "FAIL" $_.Exception.Message
        }
    }

    $finishedAt = [datetimeoffset]::UtcNow
    $restoreDurationSeconds = if ($restoreStartedAt -and $restoreFinishedAt) {
        [math]::Round(($restoreFinishedAt - $restoreStartedAt).TotalSeconds, 1)
    } else { 0 }
    $evidence = [pscustomobject]@{
        RunId = $RunId
        StartedAtUtc = $startedAt.ToString("o")
        FinishedAtUtc = $finishedAt.ToString("o")
        Result = if ($drillSucceeded -and -not $primaryError -and -not $cleanupError) { if ($Plan) { "PLAN PASS" } else { "PASS" } } else { "FAIL" }
        SourceDb = $SourceDbIdentifier
        SourceSnapshot = $sourceSnapshot
        SnapshotAgeHours = $snapshotAgeHours
        TargetDb = $TargetDbIdentifier
        TargetClass = $DbInstanceClass
        RestoreDurationSeconds = $restoreDurationSeconds
        CleanupResult = $cleanupResult
        Checks = @($checks)
        SourceCounts = if ($sourceProbe) { $sourceProbe.counts } else { $null }
        RestoredCounts = if ($restoredProbe) { $restoredProbe.counts } else { $null }
        Error = if ($primaryError) { $primaryError.Exception.Message } elseif ($cleanupError) { $cleanupError.Exception.Message } else { "" }
    }
    $reportDirectory = Split-Path -Parent $ReportPath
    [IO.Directory]::CreateDirectory($reportDirectory) | Out-Null
    [IO.File]::WriteAllText($ReportPath, (ConvertTo-RdsRestoreDrillMarkdown -Evidence $evidence), [Text.UTF8Encoding]::new($false))
    Write-Host "RDS restore drill report: $ReportPath"
}

if ($primaryError) { throw "RDS restore drill failed: $($primaryError.Exception.Message)" }
if ($cleanupError) { throw "RDS restore drill cleanup failed: $($cleanupError.Exception.Message)" }
if (-not $drillSucceeded) { throw "RDS restore drill did not reach a successful terminal state." }
Write-Host "RDS_RESTORE_DRILL_PASS run_id=$RunId cleanup=$cleanupResult" -ForegroundColor Green
