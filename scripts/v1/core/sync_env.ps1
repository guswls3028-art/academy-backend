# SSOT → Runtime env sync. Idempotent: run after infrastructure Ensure; keeps API and Workers SSM in sync with params.yaml.
# - API env: merge SQS, Video Batch, Redis (discovered from replication group) into /academy/api/env.
# - Workers env: merge SQS, Redis into /academy/workers/env (preserves existing secrets from Bootstrap).
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용.
$ErrorActionPreference = "Stop"

function Get-RuntimeApiBaseUrl {
    if ($script:FrontDomainApi -and $script:FrontDomainApi.Trim() -ne "") {
        return $script:FrontDomainApi.Trim().TrimEnd("/")
    }
    if ($script:ApiBaseUrl -and $script:ApiBaseUrl.Trim() -ne "") {
        return $script:ApiBaseUrl.Trim().TrimEnd("/")
    }
    return ""
}

function Convert-RuntimeEnvValueToObject {
    param([string]$RawValue)
    if (-not $RawValue) { return [PSCustomObject]@{} }
    $json = $RawValue
    if ($RawValue -match '^[A-Za-z0-9+/]+=*$') {
        try { $json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RawValue)) } catch { }
    }
    $obj = $json | ConvertFrom-Json
    if (-not $obj) { return [PSCustomObject]@{} }
    return $obj
}

function Assert-RuntimeEnvSettingsModule {
    param(
        [Parameter(Mandatory = $true)]$EnvObject,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$ParameterName
    )
    $property = $EnvObject.PSObject.Properties["DJANGO_SETTINGS_MODULE"]
    $actual = if ($property) { ([string]$property.Value).Trim() } else { "" }
    if ($actual -ne $Expected) {
        throw "$ParameterName DJANGO_SETTINGS_MODULE must be '$Expected' (actual='$actual'). Refusing cross-role runtime env sync."
    }
}

function Assert-ApiVideoPlaybackEnv {
    param(
        [Parameter(Mandatory = $true)]$EnvObject,
        [Parameter(Mandatory = $true)][string]$ParameterName
    )
    $canonicalBaseUrl = "https://cdn.hakwonplus.com"
    $baseProperty = $EnvObject.PSObject.Properties["CDN_HLS_BASE_URL"]
    $baseUrl = if ($baseProperty) { ([string]$baseProperty.Value).Trim().TrimEnd("/") } else { "" }
    if ($baseUrl -ne $canonicalBaseUrl) {
        throw "$ParameterName CDN_HLS_BASE_URL must be '$canonicalBaseUrl' (actual='$baseUrl'). Refusing to deploy broken video playback."
    }

    $secretProperty = $EnvObject.PSObject.Properties["CDN_HLS_SIGNING_SECRET"]
    $signingSecret = if ($secretProperty) { ([string]$secretProperty.Value).Trim() } else { "" }
    if ($signingSecret.Length -lt 32) {
        throw "$ParameterName CDN_HLS_SIGNING_SECRET must contain at least 32 characters. Refusing to deploy unsigned video playback."
    }

    $canonicalKeyId = "v1"
    $keyIdProperty = $EnvObject.PSObject.Properties["CDN_HLS_SIGNING_KEY_ID"]
    $keyId = if ($keyIdProperty) { ([string]$keyIdProperty.Value).Trim() } else { "" }
    if ($keyId -ne $canonicalKeyId) {
        throw "$ParameterName CDN_HLS_SIGNING_KEY_ID must be '$canonicalKeyId' (actual='$keyId'). Refusing to deploy an unknown CDN signing key."
    }
}

function Resolve-MessagingTenantBindingKey {
    <# Resolve one dedicated HMAC key shared by API and workers without printing it. #>
    $keys = @()
    foreach ($paramName in @($script:SsmApiEnv, $script:SsmWorkersEnv)) {
        if (-not $paramName) { continue }
        try {
            $parameter = Invoke-AwsJson @("ssm", "get-parameter", "--name", $paramName, "--with-decryption", "--region", $script:Region, "--output", "json")
            if ($parameter -and $parameter.Parameter -and $parameter.Parameter.Value) {
                $envObj = Convert-RuntimeEnvValueToObject -RawValue $parameter.Parameter.Value
                $value = [string]$envObj.PSObject.Properties["MESSAGING_TENANT_BINDING_KEY"].Value
                if ($value -and $value.Trim()) { $keys += $value.Trim() }
            }
        } catch {
            if ($_.Exception.Message -notmatch "ParameterNotFound|InvalidParameter") { throw }
        }
    }
    $uniqueKeys = @($keys | Select-Object -Unique)
    if ($uniqueKeys.Count -gt 1) {
        throw "API/workers MESSAGING_TENANT_BINDING_KEY mismatch; refusing unsafe env sync."
    }
    if ($uniqueKeys.Count -eq 1) {
        if ($uniqueKeys[0].Length -lt 32) {
            throw "MESSAGING_TENANT_BINDING_KEY must be at least 32 characters."
        }
        return $uniqueKeys[0]
    }

    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes)
}

function Sync-ApiEnvFromSSOT {
    <#
    .SYNOPSIS
        Prepares SSOT-derived API env and optionally publishes it. Missing or
        cross-role source data always fails closed.
    #>
    param([switch]$PrepareOnly)
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

    if (-not $valueRaw) {
        throw "API env $($script:SsmApiEnv) is missing or unreadable. Refusing to synthesize it from workers env."
    }
    if ($isBase64) {
        throw "API env $($script:SsmApiEnv) must be plain JSON, not base64-wrapped workers JSON."
    }
    $obj = $valueRaw | ConvertFrom-Json
    Assert-RuntimeEnvSettingsModule -EnvObject $obj -Expected "apps.api.config.settings.prod" -ParameterName $script:SsmApiEnv
    $script:OriginalApiEnvValue = $valueRaw
    $script:ApiEnvVersion = [int]$existing.Parameter.Version

    # SSOT: SQS
    $obj | Add-Member -NotePropertyName "MESSAGING_SQS_QUEUE_NAME" -NotePropertyValue $script:MessagingSqsQueueName -Force
    $obj | Add-Member -NotePropertyName "MESSAGING_TENANT_BINDING_KEY" -NotePropertyValue $script:ResolvedMessagingTenantBindingKey -Force
    if (-not $obj.PSObject.Properties["MESSAGING_TENANT_BINDING_ENFORCED"]) {
        # First installation is an explicit compatibility phase. Seal to true only
        # after every producer signs and the unsigned backlog has drained.
        $obj | Add-Member -NotePropertyName "MESSAGING_TENANT_BINDING_ENFORCED" -NotePropertyValue "false" -Force
    }
    $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_BASIC" -NotePropertyValue $script:AiSqsQueueName -Force
    $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_LITE" -NotePropertyValue $script:AiSqsQueueName -Force
    $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_PREMIUM" -NotePropertyValue $script:AiSqsQueueName -Force
    if ($script:ToolsSqsQueueName) { $obj | Add-Member -NotePropertyName "TOOLS_SQS_QUEUE_NAME" -NotePropertyValue $script:ToolsSqsQueueName -Force }
    $runtimeApiBaseUrl = Get-RuntimeApiBaseUrl
    if ($runtimeApiBaseUrl) { $obj | Add-Member -NotePropertyName "API_BASE_URL" -NotePropertyValue $runtimeApiBaseUrl -Force }

    # SSOT: Video Batch (long path 폐기 2026-05-10 — standard encoding queue/jobdef + separate ops queue/jobdefs)
    $obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_QUEUE" -NotePropertyValue $script:VideoQueueName -Force
    $obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_DEFINITION" -NotePropertyValue $script:VideoJobDefName -Force
    $obj | Add-Member -NotePropertyName "VIDEO_BATCH_COMPUTE_ENV_NAME" -NotePropertyValue $script:VideoCEName -Force
    # 옛 SSM 잔재 청소 (long 키가 박혀 있었으면 제거).
    $obj.PSObject.Properties.Remove("VIDEO_BATCH_JOB_QUEUE_LONG") | Out-Null
    $obj.PSObject.Properties.Remove("VIDEO_BATCH_JOB_DEFINITION_LONG") | Out-Null

    # Redis: discovered from replication group (SSOT)
    $redisEp = Get-RedisPrimaryEndpoint
    if ($redisEp) {
        $obj | Add-Member -NotePropertyName "REDIS_HOST" -NotePropertyValue $redisEp.Host -Force
        $obj | Add-Member -NotePropertyName "REDIS_PORT" -NotePropertyValue ([string]$redisEp.Port) -Force
    }

    if ($script:RdsProxyRequireTls) {
        $obj | Add-Member -NotePropertyName "DB_SSL_MODE" -NotePropertyValue "require" -Force
    }
    Assert-RuntimeEnvSettingsModule -EnvObject $obj -Expected "apps.api.config.settings.prod" -ParameterName $script:SsmApiEnv
    Assert-ApiVideoPlaybackEnv -EnvObject $obj -ParameterName $script:SsmApiEnv
    $newJson = $obj | ConvertTo-Json -Compress -Depth 10
    $newValue = $newJson
    $script:CandidateApiEnvValue = $newValue
    $script:ApiEnvChanged = ($newValue -ne $valueRaw)
    if ($PrepareOnly) {
        Write-Ok "API env candidate prepared without mutating $($script:SsmApiEnv)"
        return
    }
    Publish-ApiEnvCandidate
}

function Sync-WorkersEnvFromSSOT {
    <#
    .SYNOPSIS
        Merges SSOT-derived keys (SQS, REDIS_HOST, REDIS_PORT) into SSM /academy/workers/env.
        Parameter must exist (created by Bootstrap from .env). Idempotent.
    #>
    param([switch]$PrepareOnly)
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
    Assert-RuntimeEnvSettingsModule -EnvObject $obj -Expected "apps.api.config.settings.worker" -ParameterName $script:SsmWorkersEnv
    $script:OriginalWorkersEnvValue = $valueRaw

    # SSOT: SQS
    $obj | Add-Member -NotePropertyName "MESSAGING_TENANT_BINDING_KEY" -NotePropertyValue $script:ResolvedMessagingTenantBindingKey -Force
    if (-not $obj.PSObject.Properties["MESSAGING_TENANT_BINDING_ENFORCED"]) {
        $obj | Add-Member -NotePropertyName "MESSAGING_TENANT_BINDING_ENFORCED" -NotePropertyValue "false" -Force
    }
    if ($script:MessagingSqsQueueName) { $obj | Add-Member -NotePropertyName "MESSAGING_SQS_QUEUE_NAME" -NotePropertyValue $script:MessagingSqsQueueName -Force }
    if ($script:AiSqsQueueName) {
        $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_BASIC" -NotePropertyValue $script:AiSqsQueueName -Force
        $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_LITE" -NotePropertyValue $script:AiSqsQueueName -Force
        $obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_PREMIUM" -NotePropertyValue $script:AiSqsQueueName -Force
    }
    if ($script:ToolsSqsQueueName) { $obj | Add-Member -NotePropertyName "TOOLS_SQS_QUEUE_NAME" -NotePropertyValue $script:ToolsSqsQueueName -Force }
    $runtimeApiBaseUrl = Get-RuntimeApiBaseUrl
    if ($runtimeApiBaseUrl) { $obj | Add-Member -NotePropertyName "API_BASE_URL" -NotePropertyValue $runtimeApiBaseUrl -Force }
    # 옛 long path SSM 잔재 청소 (workers env). API env 동기와 동일 패턴.
    $obj.PSObject.Properties.Remove("VIDEO_BATCH_JOB_QUEUE_LONG") | Out-Null
    $obj.PSObject.Properties.Remove("VIDEO_BATCH_JOB_DEFINITION_LONG") | Out-Null

    # Redis: discovered from replication group
    $redisEp = Get-RedisPrimaryEndpoint
    if ($redisEp) {
        $obj | Add-Member -NotePropertyName "REDIS_HOST" -NotePropertyValue $redisEp.Host -Force
        $obj | Add-Member -NotePropertyName "REDIS_PORT" -NotePropertyValue ([string]$redisEp.Port) -Force
    }

    if ($script:RdsProxyRequireTls) {
        $obj | Add-Member -NotePropertyName "DB_SSL_MODE" -NotePropertyValue "require" -Force
    }

    Assert-RuntimeEnvSettingsModule -EnvObject $obj -Expected "apps.api.config.settings.worker" -ParameterName $script:SsmWorkersEnv
    $newJson = $obj | ConvertTo-Json -Compress -Depth 10
    $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newJson)
    $newValue = [Convert]::ToBase64String($newBytes)
    $script:CandidateWorkersEnvValue = $newValue
    $script:WorkersEnvChanged = ($newValue -ne $valueRaw)
    if ($PrepareOnly) {
        Write-Ok "Workers env candidate prepared without mutating $($script:SsmWorkersEnv)"
        return
    }
    Publish-WorkersEnvCandidate
}

function Invoke-SyncEnvFromSSOT {
    <#
    .SYNOPSIS
        Runs API and Workers env sync with SSOT. Call after infrastructure (including Redis) is ensured.
    #>
    param([switch]$PrepareOnly)
    Write-Step $(if ($PrepareOnly) { "Prepare runtime env candidates" } else { "Sync runtime env with SSOT" })
    $script:ApiEnvChanged = $false
    $script:WorkersEnvChanged = $false
    $script:CandidateApiEnvValue = $null
    $script:CandidateWorkersEnvValue = $null
    $script:ResolvedMessagingTenantBindingKey = Resolve-MessagingTenantBindingKey
    Sync-ApiEnvFromSSOT -PrepareOnly:$PrepareOnly
    Sync-WorkersEnvFromSSOT -PrepareOnly:$PrepareOnly
}

function Invoke-RequiredAwsJson {
    param(
        [string[]]$ArgsArray,
        [string]$ErrorMessage
    )
    $raw = Invoke-Aws -ArgsArray $ArgsArray -ErrorMessage $ErrorMessage
    if (-not $raw) { throw "$ErrorMessage returned no output." }
    try {
        return (($raw | Out-String).Trim() | ConvertFrom-Json)
    } catch {
        throw "$ErrorMessage returned invalid JSON."
    }
}

function Publish-ApiEnvCandidate {
    if (-not $script:CandidateApiEnvValue) { throw "API env candidate is not prepared." }
    if (-not $script:ApiEnvChanged) {
        Write-Ok "API env already matches SSOT: $($script:SsmApiEnv)"
        return
    }
    $put = Invoke-RequiredAwsJson -ErrorMessage "API env promotion failed" -ArgsArray @(
        "ssm", "put-parameter",
        "--name", $script:SsmApiEnv,
        "--type", "SecureString",
        "--value", $script:CandidateApiEnvValue,
        "--overwrite",
        "--region", $script:Region,
        "--output", "json"
    )
    if (-not $put -or -not $put.Version) { throw "API env promotion returned no parameter version." }
    $script:ApiEnvVersion = [int]$put.Version
    $script:ChangesMade = $true
    Write-Ok "API env candidate promoted to $($script:SsmApiEnv)"
}

function Publish-WorkersEnvCandidate {
    if (-not $script:CandidateWorkersEnvValue) { throw "Workers env candidate is not prepared." }
    if (-not $script:WorkersEnvChanged) {
        Write-Ok "Workers env already matches SSOT: $($script:SsmWorkersEnv)"
        return
    }
    $put = Invoke-RequiredAwsJson -ErrorMessage "Workers env promotion failed" -ArgsArray @(
        "ssm", "put-parameter",
        "--name", $script:SsmWorkersEnv,
        "--type", "SecureString",
        "--value", $script:CandidateWorkersEnvValue,
        "--overwrite",
        "--region", $script:Region,
        "--output", "json"
    )
    if (-not $put -or -not $put.Version) { throw "Workers env promotion returned no parameter version." }
    $script:ChangesMade = $true
    Write-Ok "Workers env candidate promoted to $($script:SsmWorkersEnv)"
}

function Publish-RuntimeEnvCandidates {
    if (-not $script:CandidateApiEnvValue -or -not $script:CandidateWorkersEnvValue) {
        throw "Runtime env candidates must be prepared before promotion."
    }
    try {
        Publish-ApiEnvCandidate
        Publish-WorkersEnvCandidate
    } catch {
        $promotionError = $_
        Write-Warn "Runtime env promotion failed; restoring prior parameter values."
        try {
            if ($script:ApiEnvChanged -and $script:OriginalApiEnvValue) {
                $rollback = Invoke-RequiredAwsJson -ErrorMessage "API env rollback failed" -ArgsArray @(
                    "ssm", "put-parameter",
                    "--name", $script:SsmApiEnv,
                    "--type", "SecureString",
                    "--value", $script:OriginalApiEnvValue,
                    "--overwrite",
                    "--region", $script:Region,
                    "--output", "json"
                )
                if ($rollback -and $rollback.Version) { $script:ApiEnvVersion = [int]$rollback.Version }
            }
            if ($script:WorkersEnvChanged -and $script:OriginalWorkersEnvValue) {
                Invoke-RequiredAwsJson -ErrorMessage "Workers env rollback failed" -ArgsArray @(
                    "ssm", "put-parameter",
                    "--name", $script:SsmWorkersEnv,
                    "--type", "SecureString",
                    "--value", $script:OriginalWorkersEnvValue,
                    "--overwrite",
                    "--region", $script:Region,
                    "--output", "json"
                ) | Out-Null
            }
        } catch {
            throw "Runtime env promotion failed and rollback also failed. Promotion error: $($promotionError.Exception.Message); rollback error: $($_.Exception.Message)"
        }
        throw $promotionError
    }
}

function Publish-ApiPreprodEnvCandidate {
    param(
        [string]$ParameterName = "/academy/api/preprod/env",
        [string]$DatabaseName = "academy_api_preprod"
    )
    if (-not $script:CandidateApiEnvValue) { throw "API env candidate is not prepared." }
    if ($DatabaseName -notmatch '^[a-z][a-z0-9_]{2,62}$') { throw "Invalid API preprod database name." }
    $obj = $script:CandidateApiEnvValue | ConvertFrom-Json
    Assert-RuntimeEnvSettingsModule -EnvObject $obj -Expected "apps.api.config.settings.prod" -ParameterName "API preprod candidate"
    $obj | Add-Member -NotePropertyName "DB_NAME" -NotePropertyValue $DatabaseName -Force
    $value = $obj | ConvertTo-Json -Compress -Depth 10
    $put = Invoke-RequiredAwsJson -ErrorMessage "API preprod env candidate write failed" -ArgsArray @(
        "ssm", "put-parameter",
        "--name", $ParameterName,
        "--type", "SecureString",
        "--tier", "Advanced",
        "--value", $value,
        "--overwrite",
        "--region", $script:Region,
        "--output", "json"
    )
    if (-not $put -or -not $put.Version) { throw "API preprod env candidate write returned no version." }
    $script:SsmApiPreprodEnv = $ParameterName
    $script:ApiPreprodDatabaseName = $DatabaseName
    Write-Ok "API preprod env candidate published to isolated parameter."
    return $ParameterName
}
