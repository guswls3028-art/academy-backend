# SSOT → Runtime env sync. Idempotent: run after infrastructure Ensure; keeps API and Workers SSM in sync with params.yaml.
# - API env: merge SQS, Video Batch, Redis (discovered from replication group) into /academy/api/env.
# - Workers env: merge SQS, Redis into /academy/workers/env (preserves existing secrets from Bootstrap).
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용.
$ErrorActionPreference = "Stop"

function Sync-ApiEnvFromSSOT {
    <#
    .SYNOPSIS
        Merges SSOT-derived keys (SQS, VIDEO_BATCH_*, REDIS_HOST, REDIS_PORT) into SSM /academy/api/env.
        If parameter does not exist, creates it from SSOT+Redis; if workers env exists, uses it as base so API gets DB/R2 etc.
        Idempotent: multiple runs produce the same env state.
    #>
    if ($script:PlanMode) { Write-Ok "Sync API env skipped (Plan)"; return }
    if (-not $script:SsmApiEnv -or $script:SsmApiEnv.Trim() -eq "") { Write-Warn "SsmApiEnv not set; skip API env sync"; return }

    $existing = $null
    $valueRaw = $null
    $isBase64 = $false
    try {
        $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmApiEnv, "--with-decryption", "--region", $script:Region, "--output", "json")
        if ($existing -and $existing.Parameter -and $existing.Parameter.Value) {
            $valueRaw = $existing.Parameter.Value
            if ($valueRaw -match '^[A-Za-z0-9+/]+=*$') { $isBase64 = $true }
        }
    } catch {
        if ($_.Exception.Message -notmatch "ParameterNotFound|InvalidParameter") { throw }
    }

    $obj = $null
    if ($valueRaw) {
        $jsonStr = $valueRaw
        if ($isBase64) {
            try { $jsonStr = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valueRaw)) } catch { $jsonStr = $valueRaw }
        }
        $obj = $jsonStr | ConvertFrom-Json
    } else {
        # API env missing: use workers env as base if present (so API gets DB/R2/secrets), then SSOT+Redis will overwrite where applicable.
        if ($script:SsmWorkersEnv) {
            try {
                $w = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmWorkersEnv, "--with-decryption", "--region", $script:Region, "--output", "json")
                if ($w -and $w.Parameter -and $w.Parameter.Value) {
                    $wRaw = $w.Parameter.Value
                    if ($wRaw -match '^[A-Za-z0-9+/]+=*$') {
                        try { $wRaw = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($wRaw)) } catch { }
                    }
                    $obj = $wRaw | ConvertFrom-Json
                }
            } catch { }
        }
        if (-not $obj) { $obj = [PSCustomObject]@{} }
    }

    # SSOT: SQS
    $obj | Add-Member -NotePropertyName "MESSAGING_SQS_QUEUE_NAME" -NotePropertyValue $script:MessagingSqsQueueName -Force
    $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_BASIC" -NotePropertyValue $script:AiSqsQueueName -Force
    $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_LITE" -NotePropertyValue $script:AiSqsQueueName -Force
    $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_PREMIUM" -NotePropertyValue $script:AiSqsQueueName -Force

    # SSOT: Video Batch
    $obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_QUEUE" -NotePropertyValue $script:VideoQueueName -Force
    $obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_DEFINITION" -NotePropertyValue $script:VideoJobDefName -Force
    $obj | Add-Member -NotePropertyName "VIDEO_BATCH_COMPUTE_ENV_NAME" -NotePropertyValue $script:VideoCEName -Force
    if ($script:VideoLongQueueName) {
        $obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_QUEUE_LONG" -NotePropertyValue $script:VideoLongQueueName -Force
        $obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_DEFINITION_LONG" -NotePropertyValue $script:VideoLongJobDefName -Force
    }

    # Redis: discovered from replication group (SSOT)
    $redisEp = Get-RedisPrimaryEndpoint
    if ($redisEp) {
        $obj | Add-Member -NotePropertyName "REDIS_HOST" -NotePropertyValue $redisEp.Host -Force
        $obj | Add-Member -NotePropertyName "REDIS_PORT" -NotePropertyValue ([string]$redisEp.Port) -Force
    }

    $newJson = $obj | ConvertTo-Json -Compress -Depth 10
    $newValue = $newJson
    if ($isBase64) {
        $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newJson)
        $newValue = [Convert]::ToBase64String($newBytes)
    }

    Invoke-Aws @("ssm", "put-parameter", "--name", $script:SsmApiEnv, "--type", "SecureString", "--value", $newValue, "--overwrite", "--region", $script:Region) -ErrorMessage "put-parameter api env" | Out-Null
    Write-Ok "API env synced with SSOT: $($script:SsmApiEnv) (SQS, Video Batch, Redis)"
    $script:ChangesMade = $true
}

function Sync-WorkersEnvFromSSOT {
    <#
    .SYNOPSIS
        Merges SSOT-derived keys (SQS, REDIS_HOST, REDIS_PORT) into SSM /academy/workers/env.
        Parameter must exist (created by Bootstrap from .env). Idempotent.
    #>
    if ($script:PlanMode) { Write-Ok "Sync Workers env skipped (Plan)"; return }
    if (-not $script:SsmWorkersEnv -or $script:SsmWorkersEnv.Trim() -eq "") { Write-Warn "SsmWorkersEnv not set; skip Workers env sync"; return }

    $existing = $null
    try {
        $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmWorkersEnv, "--with-decryption", "--region", $script:Region, "--output", "json")
    } catch {
        if ($_.Exception.Message -match "ParameterNotFound|InvalidParameter") {
            Write-Warn "Workers env $($script:SsmWorkersEnv) not found; run Bootstrap first (create from .env)."
            return
        }
        throw
    }
    if (-not $existing -or -not $existing.Parameter -or -not $existing.Parameter.Value) {
        Write-Warn "Workers env empty; run Bootstrap first."
        return
    }

    $valueRaw = $existing.Parameter.Value
    $jsonStr = $valueRaw
    if ($valueRaw -match '^[A-Za-z0-9+/]+=*$') {
        try { $jsonStr = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valueRaw)) } catch { }
    }
    $obj = $jsonStr | ConvertFrom-Json

    # SSOT: SQS
    if ($script:MessagingSqsQueueName) { $obj | Add-Member -NotePropertyName "MESSAGING_SQS_QUEUE_NAME" -NotePropertyValue $script:MessagingSqsQueueName -Force }
    if ($script:AiSqsQueueName) {
        $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_BASIC" -NotePropertyValue $script:AiSqsQueueName -Force
        $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_LITE" -NotePropertyValue $script:AiSqsQueueName -Force
        $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_PREMIUM" -NotePropertyValue $script:AiSqsQueueName -Force
    }

    # Redis: discovered from replication group
    $redisEp = Get-RedisPrimaryEndpoint
    if ($redisEp) {
        $obj | Add-Member -NotePropertyName "REDIS_HOST" -NotePropertyValue $redisEp.Host -Force
        $obj | Add-Member -NotePropertyName "REDIS_PORT" -NotePropertyValue ([string]$redisEp.Port) -Force
    }

    $newJson = $obj | ConvertTo-Json -Compress -Depth 10
    $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newJson)
    $newValue = [Convert]::ToBase64String($newBytes)

    Invoke-Aws @("ssm", "put-parameter", "--name", $script:SsmWorkersEnv, "--type", "SecureString", "--value", $newValue, "--overwrite", "--region", $script:Region) -ErrorMessage "put-parameter workers env" | Out-Null
    Write-Ok "Workers env synced with SSOT: $($script:SsmWorkersEnv) (SQS, Redis)"
    $script:ChangesMade = $true
}

function Invoke-SyncEnvFromSSOT {
    <#
    .SYNOPSIS
        Runs API and Workers env sync with SSOT. Call after infrastructure (including Redis) is ensured.
    #>
    Write-Step "Sync runtime env with SSOT"
    Sync-ApiEnvFromSSOT
    Sync-WorkersEnvFromSSOT
}
