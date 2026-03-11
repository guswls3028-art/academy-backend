# RDS: Ensure new PostgreSQL in private subnets + sg-data. No delete; Strict 게이트.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Get-RdsSubnetGroupName {
    if (-not $script:RdsDbSubnetGroupName) {
        throw "rds.dbSubnetGroupName is required in params.yaml for RDS subnet group."
    }
    return $script:RdsDbSubnetGroupName
}

function Ensure-RdsSubnetGroup {
    if ($script:PlanMode) { Write-Ok "RDS subnet group ensure skipped (Plan)"; return }
    $name = Get-RdsSubnetGroupName
    $existing = $null
    try {
        $r = Invoke-AwsJson @("rds", "describe-db-subnet-groups", "--db-subnet-group-name", $name, "--region", $script:Region, "--output", "json")
        if ($r -and $r.DBSubnetGroups -and $r.DBSubnetGroups.Count -gt 0) { $existing = $r.DBSubnetGroups[0] }
    } catch {
        if ($_.Exception.Message -notmatch "DBSubnetGroupNotFoundFault|DBSubnetGroupNotFound") { throw }
    }
    if ($existing) {
        Write-Ok "RDS DB subnet group $name exists"
        return
    }
    $subnetIds = @($script:PrivateSubnets | Where-Object { $_ })
    if (-not $subnetIds -or $subnetIds.Count -lt 2) {
        throw "At least 2 private subnets required for RDS DB subnet group (have $($subnetIds.Count)). Ensure-Network must run first."
    }
    $args = @(
        "rds", "create-db-subnet-group",
        "--db-subnet-group-name", $name,
        "--db-subnet-group-description", "Academy v1 RDS",
        "--subnet-ids"
    ) + $subnetIds + @(
        "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
        "--region", $script:Region, "--output", "json"
    )
    $create = Invoke-AwsJson $args
    if (-not $create -or -not $create.DBSubnetGroup) { throw "create-db-subnet-group failed for $name" }
    Write-Ok "RDS DB subnet group $name created"
    $script:ChangesMade = $true
}

function Get-RdsMasterPassword {
    if (-not $script:RdsMasterPasswordSsmParam) {
        throw "rds.masterPasswordSsmParam is required in params.yaml (SSM SecureString with DB master password)."
    }
    $p = Invoke-AwsJson @(
        "ssm", "get-parameter",
        "--name", $script:RdsMasterPasswordSsmParam,
        "--with-decryption",
        "--region", $script:Region,
        "--output", "json"
    )
    if (-not $p -or -not $p.Parameter -or -not $p.Parameter.Value) {
        throw "SSM parameter $($script:RdsMasterPasswordSsmParam) not found or empty."
    }
    return $p.Parameter.Value
}

function Ensure-RDSSecurityGroup {
    param($DbInstance)
    if ($script:PlanMode) { return }
    if (-not $script:RdsDbIdentifier) { return }
    if (-not $DbInstance) {
        try {
            $r = Invoke-AwsJson @("rds", "describe-db-instances", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $script:Region, "--output", "json")
        } catch {
            if ($_.Exception.Message -match "DBInstanceNotFound") { return }
            throw
        }
        if (-not $r -or -not $r.DBInstances -or $r.DBInstances.Count -eq 0) { return }
        $DbInstance = $r.DBInstances[0]
    }
    if (-not $script:SecurityGroupData) { return }
    $sgIds = @($DbInstance.VpcSecurityGroups | ForEach-Object { $_.VpcSecurityGroupId })
    if (-not $sgIds) { $sgIds = @() }
    if ($sgIds -contains $script:SecurityGroupData) {
        Write-Ok "RDS SGs already include sg-data $($script:SecurityGroupData)"
        return
    }
    $newSgs = @($sgIds + $script:SecurityGroupData | Select-Object -Unique)
    Write-Host "  Updating RDS SGs to include sg-data $($script:SecurityGroupData)" -ForegroundColor Yellow
    try {
        Invoke-Aws @(
            "rds", "modify-db-instance",
            "--db-instance-identifier", $script:RdsDbIdentifier,
            "--vpc-security-group-ids"
        ) + $newSgs + @(
            "--apply-immediately",
            "--region", $script:Region
        ) -ErrorMessage "modify RDS SGs" | Out-Null
        $script:ChangesMade = $true
    } catch {
        if ($_.Exception.Message -match "No modifications were requested|InvalidParameterCombination") {
            Write-Ok "RDS SGs unchanged (already include sg-data or same set)"
        } else { throw }
    }
}

function Ensure-RdsObservability {
    param($DbInstance)
    if ($script:PlanMode) { return }
    if (-not $script:RdsDbIdentifier) { return }
    if (-not $DbInstance) {
        try {
            $r = Invoke-AwsJson @("rds", "describe-db-instances", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $script:Region, "--output", "json")
            if (-not $r -or -not $r.DBInstances -or $r.DBInstances.Count -eq 0) { return }
            $DbInstance = $r.DBInstances[0]
        } catch { return }
    }
    $modify = $false
    $args = @("rds", "modify-db-instance", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $script:Region)
    if ($script:RdsPerformanceInsightsEnabled -and -not $DbInstance.PerformanceInsightsEnabled) {
        $retention = if ($script:RdsPerformanceInsightsRetentionDays -gt 0) { $script:RdsPerformanceInsightsRetentionDays } else { 7 }
        $args += "--enable-performance-insights", "--performance-insights-retention-period", $retention.ToString()
        $modify = $true
    }
    if ($script:RdsMultiAz -and -not $DbInstance.MultiAZ) {
        $args += "--multi-az"
        $modify = $true
    }
    if ($modify) {
        $args += "--apply-immediately"
        try {
            Invoke-Aws $args -ErrorMessage "modify-db-instance observability" | Out-Null
            Write-Ok "RDS $($script:RdsDbIdentifier) PI/MultiAZ updated per SSOT"
            $script:ChangesMade = $true
        } catch {
            if ($_.Exception.Message -match "No modifications were requested") { Write-Ok "RDS observability unchanged" }
            else { Write-Host "  RDS observability modify: $($_.Exception.Message)" -ForegroundColor Yellow }
        }
    }
}

function Confirm-RDSState {
    Write-Step "Ensure RDS $($script:RdsDbIdentifier)"
    if ($script:PlanMode) {
        Write-Ok "RDS ensure skipped (Plan)"
        return
    }
    if (-not $script:RdsDbIdentifier) {
        throw "rds.dbIdentifier is required in params.yaml."
    }
    if (-not $script:RdsMasterUsername) {
        throw "rds.masterUsername is required in params.yaml."
    }
    if (-not $script:SecurityGroupData) {
        throw "SecurityGroupData (sg-data) must be set before RDS ensure. Ensure-Network must run first."
    }

    Ensure-RdsSubnetGroup

    $db = $null
    try {
        $rds = Invoke-AwsJson @("rds", "describe-db-instances", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $script:Region, "--output", "json")
        if ($rds -and $rds.DBInstances -and $rds.DBInstances.Count -gt 0) { $db = $rds.DBInstances[0] }
    } catch {
        if ($_.Exception.Message -notmatch "DBInstanceNotFound") { throw }
    }

    if (-not $db) {
        $password = Get-RdsMasterPassword
        $engineVer = if ($script:RdsEngineVersionResolved) { $script:RdsEngineVersionResolved } else { $script:RdsEngineVersion }
        $engineArgs = @()
        if ($engineVer -and $engineVer.Trim() -ne "") { $engineArgs = @("--engine-version", $engineVer.Trim()) }
        $createArgs = @(
            "rds", "create-db-instance",
            "--db-instance-identifier", $script:RdsDbIdentifier,
            "--db-instance-class", $script:RdsInstanceClass,
            "--engine", $script:RdsEngine
        ) + $engineArgs + @(
            "--allocated-storage", $script:RdsAllocatedStorage.ToString(),
            "--master-username", $script:RdsMasterUsername,
            "--master-user-password", $password,
            "--db-subnet-group-name", (Get-RdsSubnetGroupName),
            "--vpc-security-group-ids", $script:SecurityGroupData,
            "--no-publicly-accessible",
            "--backup-retention-period", "7",
            "--deletion-protection",
            "--copy-tags-to-snapshot",
            "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
            "--region", $script:Region, "--output", "json"
        )
        $create = Invoke-AwsJson $createArgs
        if (-not $create -or -not $create.DBInstance) {
            $prev = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            $errOut = & aws @createArgs 2>&1
            $ErrorActionPreference = $prev
            $errText = ($errOut | Out-String).Trim()
            throw "create-db-instance failed for $($script:RdsDbIdentifier). AWS output: $errText"
        }
        $db = $create.DBInstance
        Write-Ok "RDS $($script:RdsDbIdentifier) creating (status=$($db.DBInstanceStatus))"
        $script:ChangesMade = $true
    }

    $timeoutSec = 1800
    $elapsed = 0
    while ($elapsed -lt $timeoutSec) {
        $desc = Invoke-AwsJson @("rds", "describe-db-instances", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $script:Region, "--output", "json")
        if ($desc -and $desc.DBInstances -and $desc.DBInstances.Count -gt 0) {
            $db = $desc.DBInstances[0]
            $status = $db.DBInstanceStatus
            Write-Host "  RDS status=$status" -ForegroundColor Gray
            if ($status -eq "available") {
                $ep = $db.Endpoint
                Write-Ok "RDS $($script:RdsDbIdentifier) available (endpoint: $($ep.Address):$($ep.Port))"
                Ensure-RDSSecurityGroup -DbInstance $db
                Ensure-RdsObservability -DbInstance $db
                return
            }
            if ($status -eq "failed") {
                throw "RDS $($script:RdsDbIdentifier) entered failed state."
            }
        }
        Start-Sleep -Seconds 30
        $elapsed += 30
    }
    throw "RDS $($script:RdsDbIdentifier) did not reach available status in ${timeoutSec}s."
}
