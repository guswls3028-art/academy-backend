# Execute the production Logs Insights query used by the analytics hard gate.
[CmdletBinding()]
param(
    [ValidatePattern('^[a-z]{2}-[a-z]+-\d$')]
    [string]$Region = "ap-northeast-2",
    [ValidatePattern('^/academy/api$')]
    [string]$LogGroupName = "/academy/api",
    [Parameter(Mandatory = $true)]
    [string]$GithubOutputPath,
    [ValidateRange(1, 60)]
    [int]$MaxAttempts = 30,
    [ValidateRange(0, 30)]
    [int]$PollSeconds = 2,
    [datetime]$NowUtc = [datetime]::UtcNow
)

$ErrorActionPreference = "Stop"
$invariant = [Globalization.CultureInfo]::InvariantCulture
$query = @'
fields toDouble(extra.db_duration_ms) * toDouble(extra.sample_weight) as weighted_db_ms, toDouble(extra.write_query_count) * toDouble(extra.sample_weight) as weighted_writes, extra.route_or_job_family as route_or_job_family | filter extra.event = "tenant_db_usage" | stats sum(weighted_db_ms) as total_db_ms, sum(if(route_or_job_family like /product-analytics/, weighted_db_ms, 0)) as analytics_db_ms, sum(weighted_writes) as total_writes, sum(if(route_or_job_family like /product-analytics/, weighted_writes, 0)) as analytics_writes
'@.Trim()

function Add-GithubOutput {
    param([string]$Name, [string]$Value)
    Add-Content -LiteralPath $GithubOutputPath -Value ("{0}={1}" -f $Name, $Value)
}

function Fail-Readback {
    param([string]$Message)
    Add-GithubOutput -Name "available" -Value "false"
    throw $Message
}

$endTime = [DateTimeOffset]::new($NowUtc.ToUniversalTime()).ToUnixTimeSeconds()
$startTime = $endTime - (24 * 60 * 60)

$startQueryError = $null
try {
    $queryId = & aws logs start-query `
        --log-group-name $LogGroupName `
        --start-time $startTime `
        --end-time $endTime `
        --query-string $query `
        --region $Region `
        --query queryId `
        --output text
    if (-not $?) {
        $startQueryError = "aws logs start-query returned a failure status."
    }
} catch {
    $startQueryError = $_.Exception.Message
}
if ($startQueryError) {
    Fail-Readback ("Tenant DB telemetry query could not be started: {0}" -f $startQueryError)
}
if ([string]::IsNullOrWhiteSpace([string]$queryId)) {
    Fail-Readback "Tenant DB telemetry query could not be started."
}

for ($attempt = 1; $attempt -le $MaxAttempts; $attempt += 1) {
    $resultReadError = $null
    try {
        $resultJson = & aws logs get-query-results `
            --query-id ([string]$queryId).Trim() `
            --region $Region `
            --output json
        if (-not $?) {
            $resultReadError = "aws logs get-query-results returned a failure status."
        }
        $result = ([string]$resultJson | ConvertFrom-Json)
    } catch {
        $resultReadError = $_.Exception.Message
    }
    if ($resultReadError) {
        Fail-Readback ("Tenant DB telemetry query result could not be read: {0}" -f $resultReadError)
    }

    if ($result.status -eq "Complete") {
        $row = @{}
        foreach ($field in @($result.results[0])) {
            if ($field.field) {
                $row[[string]$field.field] = [string]$field.value
            }
        }
        $totalDb = [double]::Parse(($row.total_db_ms ?? "0"), $invariant)
        $analyticsDb = [double]::Parse(($row.analytics_db_ms ?? "0"), $invariant)
        $totalWrites = [double]::Parse(($row.total_writes ?? "0"), $invariant)
        $analyticsWrites = [double]::Parse(($row.analytics_writes ?? "0"), $invariant)
        $dbShare = if ($totalDb -gt 0) { $analyticsDb / $totalDb } else { $null }
        $writeShare = if ($totalWrites -gt 0) { $analyticsWrites / $totalWrites } else { $null }

        if ($null -ne $dbShare) {
            Add-GithubOutput -Name "db_time_share" -Value (
                $dbShare.ToString("0.################", $invariant)
            )
        }
        if ($null -ne $writeShare) {
            Add-GithubOutput -Name "write_share" -Value (
                $writeShare.ToString("0.################", $invariant)
            )
        }
        Add-GithubOutput -Name "available" -Value "true"
        Write-Host (
            "PRODUCT_ANALYTICS_DB_SHARE_READBACK_PASS db_time_share={0} write_share={1}" -f
            $(if ($null -ne $dbShare) { $dbShare.ToString("0.################", $invariant) } else { "unavailable" }),
            $(if ($null -ne $writeShare) { $writeShare.ToString("0.################", $invariant) } else { "unavailable" })
        )
        return
    }

    if ($result.status -in @("Failed", "Cancelled", "Timeout")) {
        Fail-Readback ("Tenant DB telemetry query ended with status={0}." -f $result.status)
    }
    if ($attempt -lt $MaxAttempts -and $PollSeconds -gt 0) {
        Start-Sleep -Seconds $PollSeconds
    }
}

Fail-Readback "Tenant DB telemetry query timed out."
