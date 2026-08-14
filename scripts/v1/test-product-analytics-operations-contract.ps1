$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "academy-product-analytics-contract-" + [Guid]::NewGuid().ToString("N")
)
[void](New-Item -ItemType Directory -Path $tempRoot)

function Assert-Contract {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

try {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)
    $maintenanceWorkflowPath = Join-Path $repoRoot ".github/workflows/product-usage-maintenance.yml"
    $maintenanceWorkflow = Get-Content -Raw -LiteralPath $maintenanceWorkflowPath
    $checkoutMarker = "uses: actions/checkout@"
    $dbShareScriptMarker = "./scripts/v1/read-product-analytics-db-share.ps1"
    $checkoutIndex = $maintenanceWorkflow.IndexOf($checkoutMarker)
    $dbShareScriptIndex = $maintenanceWorkflow.IndexOf($dbShareScriptMarker)
    Assert-Contract ($checkoutIndex -ge 0) (
        "Product analytics maintenance must check out the repository before invoking local scripts."
    )
    Assert-Contract ($dbShareScriptIndex -gt $checkoutIndex) (
        "Repository checkout must precede the DB-share script invocation."
    )

    $recorderPath = Join-Path $tempRoot "telemetry-recorder.ps1"
    $recordPath = Join-Path $tempRoot "telemetry-record.json"
    @'
param(
    [switch]$Disable,
    [double]$SampleRate,
    [int]$SlowRequestMs,
    [switch]$Ci
)
@{
    Disable = [bool]$Disable
    SampleRate = $SampleRate
    SlowRequestMs = $SlowRequestMs
    Ci = [bool]$Ci
} | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:TELEMETRY_RECORD_PATH
'@ | Set-Content -LiteralPath $recorderPath -Encoding utf8
    $env:TELEMETRY_RECORD_PATH = $recordPath

    & (Join-Path $scriptRoot "invoke-product-usage-pilot-control.ps1") `
        -Enabled true `
        -SampleRate "0.10" `
        -TelemetryScriptPath $recorderPath
    $enabledRecord = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
    Assert-Contract ($enabledRecord.Ci -eq $true) "Enabled control must bind -Ci."
    Assert-Contract ($enabledRecord.Disable -eq $false) "Enabled control must not bind -Disable."
    Assert-Contract ($enabledRecord.SampleRate -eq 0.1) "Enabled sample rate binding regressed."
    Assert-Contract ($enabledRecord.SlowRequestMs -eq 1000) "Slow request threshold binding regressed."

    & (Join-Path $scriptRoot "invoke-product-usage-pilot-control.ps1") `
        -Enabled false `
        -SampleRate "0.05" `
        -TelemetryScriptPath $recorderPath
    $disabledRecord = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
    Assert-Contract ($disabledRecord.Disable -eq $true) "Disabled control must bind -Disable."
    Assert-Contract ($disabledRecord.SampleRate -eq 0.05) "Disabled sample rate binding regressed."

    $global:ProductAnalyticsAwsCalls = @()
    function global:aws {
        $global:ProductAnalyticsAwsCalls += ,@($args)
        if ($args[0] -eq "logs" -and $args[1] -eq "start-query") {
            return "query-contract-123"
        }
        if ($args[0] -eq "logs" -and $args[1] -eq "get-query-results") {
            return '{"status":"Complete","results":[[{"field":"total_db_ms","value":"1000"},{"field":"analytics_db_ms","value":"100"},{"field":"total_writes","value":"200"},{"field":"analytics_writes","value":"20"}]]}'
        }
        throw "Unexpected aws invocation: $args"
    }

    $outputPath = Join-Path $tempRoot "github-output.txt"
    & (Join-Path $scriptRoot "read-product-analytics-db-share.ps1") `
        -GithubOutputPath $outputPath `
        -MaxAttempts 1 `
        -PollSeconds 0 `
        -NowUtc ([datetime]"2026-08-13T00:00:00Z")
    $output = Get-Content -Raw -LiteralPath $outputPath
    Assert-Contract ($output -match "available=true") "Logs Insights readback must be available."
    Assert-Contract ($output -match "db_time_share=0.1") "DB time share calculation regressed."
    Assert-Contract ($output -match "write_share=0.1") "Write share calculation regressed."

    $startQueryCall = $global:ProductAnalyticsAwsCalls | Where-Object {
        $_[0] -eq "logs" -and $_[1] -eq "start-query"
    } | Select-Object -First 1
    Assert-Contract ($null -ne $startQueryCall) "Logs Insights start-query was not executed."
    $startQueryText = $startQueryCall -join " "
    foreach ($required in @(
        "--log-group-name /academy/api",
        'extra.event = "tenant_db_usage"',
        "extra.db_duration_ms",
        "extra.write_query_count",
        "extra.sample_weight",
        "extra.route_or_job_family"
    )) {
        Assert-Contract ($startQueryText.Contains($required)) "Missing query contract: $required"
    }
    Assert-Contract (-not $startQueryText.Contains("toDouble(db_duration_ms)")) (
        "Top-level telemetry fields must not re-enter the Logs Insights query."
    )

    function global:aws { throw "simulated StartQuery denial" }
    $failureOutputPath = Join-Path $tempRoot "github-output-failure.txt"
    $failedClosed = $false
    try {
        & (Join-Path $scriptRoot "read-product-analytics-db-share.ps1") `
            -GithubOutputPath $failureOutputPath `
            -MaxAttempts 1 `
            -PollSeconds 0
    } catch {
        $failedClosed = $_.Exception.Message -match "could not be started"
    }
    Assert-Contract $failedClosed "Logs Insights authorization failure must fail closed."
    Assert-Contract (
        (Get-Content -Raw -LiteralPath $failureOutputPath) -match "available=false"
    ) "Failed readback must publish available=false."

    Write-Host "PRODUCT_ANALYTICS_OPERATIONS_CONTRACT_PASS"
} finally {
    Remove-Item Env:TELEMETRY_RECORD_PATH -ErrorAction SilentlyContinue
    Remove-Item Function:\aws -ErrorAction SilentlyContinue
    Remove-Variable ProductAnalyticsAwsCalls -Scope Global -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $tempRoot) {
        [IO.Directory]::Delete($tempRoot, $true)
    }
}
