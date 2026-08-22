param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int]$TenantId,

    [Parameter(Mandatory = $true)]
    [ValidateSet("hold", "release")]
    [string]$Mode,

    [string]$AwsProfile = "default",
    [string]$Region = "ap-northeast-2",
    [string]$RunWithEnvPath = ""
)

$ErrorActionPreference = "Stop"

$runWithEnv = if ($RunWithEnvPath) {
    $RunWithEnvPath
} else {
    Join-Path $PSScriptRoot "run-with-env.ps1"
}
if (-not (Test-Path -LiteralPath $runWithEnv)) {
    throw "run-with-env.ps1 not found: $runWithEnv"
}

function Invoke-AwsJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $raw = & pwsh $runWithEnv -- aws @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "AWS command failed: aws $($Arguments[0]) ..."
    }
    return ($raw | ConvertFrom-Json)
}

function Read-ParameterState {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet("plain", "base64")][string]$Wrapping
    )

    $response = Invoke-AwsJson -Arguments @(
        "ssm", "get-parameter",
        "--name", $Name,
        "--with-decryption",
        "--region", $Region,
        "--profile", $AwsProfile,
        "--output", "json"
    )
    $wireValue = [string]$response.Parameter.Value
    $json = if ($Wrapping -eq "base64") {
        [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($wireValue))
    } else {
        $wireValue
    }
    $node = [System.Text.Json.Nodes.JsonNode]::Parse($json)
    if ($null -eq $node -or $null -eq $node.AsObject()) {
        throw "$Name did not decode to a JSON object"
    }
    return @{
        Version = [int]$response.Parameter.Version
        Object = $node.AsObject()
    }
}

function Set-ParameterState {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet("plain", "base64")][string]$Wrapping,
        [Parameter(Mandatory = $true)][int]$ExpectMinKeys
    )

    $state = Read-ParameterState -Name $Name -Wrapping $Wrapping
    $object = $state.Object
    if ($object.Count -lt $ExpectMinKeys) {
        throw "$Name has $($object.Count) keys; expected at least $ExpectMinKeys"
    }

    $rawIds = [string]$object["MESSAGING_DISABLED_TENANT_IDS"].GetValue[string]()
    $ids = @(
        $rawIds.Split(",") |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ } |
            ForEach-Object {
                $parsed = 0
                if (-not [int]::TryParse($_, [ref]$parsed) -or $parsed -le 0) {
                    throw "$Name contains an invalid disabled tenant id"
                }
                $parsed
            }
    )
    if (($ids | Select-Object -Unique).Count -ne $ids.Count) {
        throw "$Name contains duplicate disabled tenant ids"
    }

    $beforeContains = $ids -contains $TenantId
    if ($Mode -eq "release" -and -not $beforeContains) {
        throw "$Name does not contain tenant $TenantId; refusing a no-op release"
    }
    if ($Mode -eq "hold" -and $beforeContains) {
        throw "$Name already contains tenant $TenantId; refusing a no-op hold"
    }

    $nextIds = @()
    if ($Mode -eq "release") {
        $nextIds += @($ids | Where-Object { $_ -ne $TenantId })
    } else {
        $nextIds += @($ids + $TenantId | Sort-Object -Unique)
    }
    $nextValue = ($nextIds | ForEach-Object { [string]$_ }) -join ","
    $object["MESSAGING_DISABLED_TENANT_IDS"] = $nextValue

    $jsonOptions = [System.Text.Json.JsonSerializerOptions]::new()
    $jsonOptions.WriteIndented = $false
    $newJson = $object.ToJsonString($jsonOptions)
    $roundTrip = [System.Text.Json.Nodes.JsonNode]::Parse($newJson).AsObject()
    if ($roundTrip.Count -ne $object.Count) {
        throw "$Name key count changed during JSON round-trip"
    }
    if ([string]$roundTrip["MESSAGING_DISABLED_TENANT_IDS"].GetValue[string]() -ne $nextValue) {
        throw "$Name disabled tenant readback changed during JSON round-trip"
    }

    $wireValue = if ($Wrapping -eq "base64") {
        [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($newJson))
    } else {
        $newJson
    }
    $tempFile = New-TemporaryFile
    try {
        [IO.File]::WriteAllText(
            $tempFile.FullName,
            $wireValue,
            [Text.UTF8Encoding]::new($false)
        )
        $putResponse = Invoke-AwsJson -Arguments @(
            "ssm", "put-parameter",
            "--name", $Name,
            "--type", "SecureString",
            "--value", "file://$($tempFile.FullName)",
            "--overwrite",
            "--region", $Region,
            "--profile", $AwsProfile,
            "--output", "json"
        )
    } finally {
        Remove-Item -LiteralPath $tempFile.FullName -Force -ErrorAction SilentlyContinue
    }

    $readback = Read-ParameterState -Name $Name -Wrapping $Wrapping
    $readbackValue = [string]$readback.Object["MESSAGING_DISABLED_TENANT_IDS"].GetValue[string]()
    if ($readback.Object.Count -ne $object.Count -or $readbackValue -ne $nextValue) {
        throw "$Name failed the post-write invariant check"
    }
    Write-Host (
        "MESSAGING_TENANT_HOLD_UPDATED name={0} mode={1} tenant={2} version={3} keys={4} disabled_count={5}" -f
        $Name, $Mode, $TenantId, $readback.Version, $readback.Object.Count, $nextIds.Count
    )
}

# The API producer and messaging worker consumer must share the exact same hold set.
# Write the worker value first so a release cannot enqueue against a stale consumer.
Set-ParameterState -Name "/academy/workers/env" -Wrapping "base64" -ExpectMinKeys 40
Set-ParameterState -Name "/academy/api/env" -Wrapping "plain" -ExpectMinKeys 50

Write-Host "NEXT_ACTION_REQUIRED refresh the API and messaging worker runtimes, then verify the exact hold set on both before relying on this change"
