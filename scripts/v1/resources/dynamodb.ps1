# DynamoDB: Ensure video lock table (FD1). No delete; Strict 게이트.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Ensure-DynamoLockTable {
    Write-Step "Ensure DynamoDB lock table $($script:DynamoLockTableName)"
    if ($script:PlanMode) {
        Write-Ok "DynamoDB lock ensure skipped (Plan)"
        return
    }
    if (-not $script:DynamoLockTableName) {
        throw "dynamodb.lockTableName is required in params.yaml."
    }
    $tableName = $script:DynamoLockTableName
    $ttlAttr = if ($script:DynamoLockTtlAttribute) { $script:DynamoLockTtlAttribute } else { "ttl" }

    $table = $null
    try {
        $desc = Invoke-AwsJson @("dynamodb", "describe-table", "--table-name", $tableName, "--region", $script:Region, "--output", "json")
        if ($desc -and $desc.Table) { $table = $desc.Table }
    } catch {
        if ($_.Exception.Message -notmatch "ResourceNotFoundException|Requested resource not found") { throw }
    }

    if (-not $table) {
        Write-Host "  Creating DynamoDB table $tableName" -ForegroundColor Yellow
        $createArgs = @(
            "dynamodb", "create-table",
            "--table-name", $tableName,
            "--attribute-definitions", "AttributeName=videoId,AttributeType=S",
            "--key-schema", "AttributeName=videoId,KeyType=HASH",
            "--billing-mode", "PAY_PER_REQUEST",
            "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
            "--region", $script:Region, "--output", "json"
        )
        $create = Invoke-AwsJson $createArgs
        if (-not $create -or -not $create.TableDescription) {
            throw "create-table failed for $tableName"
        }
        $table = $create.TableDescription
        $script:ChangesMade = $true
    }

    $timeoutSec = 600
    $elapsed = 0
    while ($elapsed -lt $timeoutSec) {
        $desc = Invoke-AwsJson @("dynamodb", "describe-table", "--table-name", $tableName, "--region", $script:Region, "--output", "json")
        if ($desc -and $desc.Table) {
            $table = $desc.Table
            $status = $table.TableStatus
            Write-Host "  DynamoDB table status=$status" -ForegroundColor Gray
            if ($status -eq "ACTIVE") { break }
        }
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    if (-not $table -or $table.TableStatus -ne "ACTIVE") {
        throw "DynamoDB table $tableName did not reach ACTIVE status in ${timeoutSec}s."
    }

    $ttl = $null
    try {
        $ttlDesc = Invoke-AwsJson @("dynamodb", "describe-time-to-live", "--table-name", $tableName, "--region", $script:Region, "--output", "json")
        if ($ttlDesc -and $ttlDesc.TimeToLiveDescription) { $ttl = $ttlDesc.TimeToLiveDescription }
    } catch {
        # describe-time-to-live is eventually consistent; ignore transient errors
    }

    $needsTtl = $true
    if ($ttl -and $ttl.TimeToLiveStatus -eq "ENABLED" -and $ttl.AttributeName -eq $ttlAttr) {
        $needsTtl = $false
    }

    if ($needsTtl) {
        Write-Host "  Enabling TTL on $tableName (attribute=$ttlAttr)" -ForegroundColor Yellow
        Invoke-Aws @(
            "dynamodb", "update-time-to-live",
            "--table-name", $tableName,
            "--time-to-live-specification", "Enabled=true,AttributeName=$ttlAttr",
            "--region", $script:Region
        ) -ErrorMessage "enable DynamoDB TTL" | Out-Null
        $script:ChangesMade = $true

        $ttlWait = 0
        $ttlTimeout = 300
        while ($ttlWait -lt $ttlTimeout) {
            $ttlDesc = Invoke-AwsJson @("dynamodb", "describe-time-to-live", "--table-name", $tableName, "--region", $script:Region, "--output", "json")
            if ($ttlDesc -and $ttlDesc.TimeToLiveDescription) {
                $ttl = $ttlDesc.TimeToLiveDescription
                if ($ttl.TimeToLiveStatus -eq "ENABLED" -and $ttl.AttributeName -eq $ttlAttr) {
                    Write-Ok "DynamoDB TTL enabled on $tableName (attribute=$ttlAttr)"
                    break
                }
            }
            Start-Sleep -Seconds 5
            $ttlWait += 5
        }
        if (-not $ttl -or $ttl.TimeToLiveStatus -ne "ENABLED") {
            throw "DynamoDB TTL not ENABLED for $tableName within ${ttlTimeout}s."
        }
    } else {
        Write-Ok "DynamoDB TTL already ENABLED on $tableName (attribute=$ttlAttr)"
    }
}

function Ensure-DynamoUploadCheckpointTable {
    if (-not $script:DynamoUploadCheckpointTableName -or $script:PlanMode) { return }
    Write-Step "Ensure DynamoDB upload checkpoint table $($script:DynamoUploadCheckpointTableName)"
    $tableName = $script:DynamoUploadCheckpointTableName
    $table = $null
    try {
        $desc = Invoke-AwsJson @("dynamodb", "describe-table", "--table-name", $tableName, "--region", $script:Region, "--output", "json")
        if ($desc -and $desc.Table) { $table = $desc.Table }
    } catch {
        if ($_.Exception.Message -notmatch "ResourceNotFoundException|Requested resource not found") { throw }
    }
    if (-not $table) {
        Write-Host "  Creating DynamoDB table $tableName (upload checkpoint)" -ForegroundColor Yellow
        $createArgs = @(
            "dynamodb", "create-table",
            "--table-name", $tableName,
            "--attribute-definitions", "AttributeName=uploadId,AttributeType=S", "AttributeName=partNumber,AttributeType=N",
            "--key-schema", "AttributeName=uploadId,KeyType=HASH", "AttributeName=partNumber,KeyType=RANGE",
            "--billing-mode", "PAY_PER_REQUEST",
            "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
            "--region", $script:Region, "--output", "json"
        )
        $create = Invoke-AwsJson $createArgs
        if (-not $create -or -not $create.TableDescription) { throw "create-table failed for $tableName" }
        $script:ChangesMade = $true
        $elapsed = 0
        while ($elapsed -lt 600) {
            $desc = Invoke-AwsJson @("dynamodb", "describe-table", "--table-name", $tableName, "--region", $script:Region, "--output", "json")
            if ($desc -and $desc.Table -and $desc.Table.TableStatus -eq "ACTIVE") { Write-Ok "DynamoDB upload checkpoint table $tableName ACTIVE"; return }
            Start-Sleep -Seconds 10
            $elapsed += 10
        }
        throw "DynamoDB table $tableName did not reach ACTIVE in 600s."
    }
    Write-Ok "DynamoDB upload checkpoint table $tableName exists"
}

