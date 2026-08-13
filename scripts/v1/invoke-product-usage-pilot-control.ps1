# Bind the reviewed workflow inputs to the production telemetry mutation script.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("true", "false")]
    [string]$Enabled,
    [Parameter(Mandatory = $true)]
    [ValidateSet("0.05", "0.10")]
    [string]$SampleRate,
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$TelemetryScriptPath = (
        Join-Path $PSScriptRoot "set-tenant-db-usage-telemetry.ps1"
    )
)

$ErrorActionPreference = "Stop"
$sampleRateValue = [double]::Parse(
    $SampleRate,
    [Globalization.CultureInfo]::InvariantCulture
)
$telemetryParameters = @{
    Ci = $true
    SampleRate = $sampleRateValue
    SlowRequestMs = 1000
}
if ($Enabled -eq "false") {
    $telemetryParameters.Disable = $true
}

& $TelemetryScriptPath @telemetryParameters
if (-not $?) {
    throw "Tenant DB telemetry control failed."
}

Write-Host (
    "TENANT_DB_TELEMETRY_CONTROL_INVOKE_PASS enabled={0} sample_rate={1}" -f
    $Enabled,
    $sampleRateValue.ToString([Globalization.CultureInfo]::InvariantCulture)
)
