# RDS: status/endpoint/SG check. Add Batch SG -> 5432 ingress if missing.
function Confirm-RDSState {
    Write-Step "RDS $($script:RdsDbIdentifier)"
    $rds = Invoke-AwsJson @("rds", "describe-db-instances", "--region", $script:Region, "--output", "json")
    $db = $rds.DBInstances | Where-Object { $_.DBInstanceIdentifier -eq $script:RdsDbIdentifier } | Select-Object -First 1
    if (-not $db) {
        Write-Warn "RDS $($script:RdsDbIdentifier) not found"
        return
    }
    Write-Ok "RDS $($script:RdsDbIdentifier) Status=$($db.DBInstanceStatus) Endpoint=$($db.Endpoint.Address)"
}

function Ensure-RDSSecurityGroup {
    $rds = Invoke-AwsJson @("rds", "describe-db-instances", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $script:Region, "--output", "json")
    if (-not $rds -or -not $rds.DBInstances -or $rds.DBInstances.Count -eq 0) { return }
    $sgIds = $rds.DBInstances[0].VpcSecurityGroups | ForEach-Object { $_.VpcSecurityGroupId }
    foreach ($sgId in $sgIds) {
        $rules = Invoke-AwsJson @("ec2", "describe-security-groups", "--group-ids", $sgId, "--region", $script:Region, "--output", "json")
        $hasBatch = $rules.SecurityGroups[0].IpPermissions | Where-Object {
            $_.FromPort -eq 5432 -and $_.UserIdGroupPairs | Where-Object { $_.GroupId -eq $script:BatchSecurityGroupId }
        }
        if (-not $hasBatch -and $script:BatchSecurityGroupId) {
            Write-Host "  Adding Batch SG to RDS SG $sgId (5432)" -ForegroundColor Yellow
            try {
                Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--protocol", "tcp", "--port", "5432", "--source-group", $script:BatchSecurityGroupId, "--region", $script:Region) -ErrorMessage "RDS SG 5432 from Batch" 2>$null | Out-Null
                $script:ChangesMade = $true
            } catch {
                if ($_.Exception.Message -notmatch "Duplicate|InvalidPermission\.Duplicate") { throw }
                Write-Host "  Rule already exists (Duplicate), skip." -ForegroundColor Gray
            }
        }
    }
}
