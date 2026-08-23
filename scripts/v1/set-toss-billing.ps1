# Safely stage or activate Toss automatic billing credentials in /academy/api/env.
# Secrets are read from local files so they do not appear in shell history or the
# process list. The script never prints key values.
#
# Phase A - test card registration only:
#   pwsh scripts/v1/set-toss-billing.ps1 `
#     -Mode Test `
#     -ClientKeyFile C:\secure\toss-client-key.txt `
#     -SecretKeyFile C:\secure\toss-secret-key.txt `
#     -RefreshInstances
#
# Phase B - live automatic billing:
#   pwsh scripts/v1/set-toss-billing.ps1 `
#     -Mode Live `
#     -ClientKeyFile C:\secure\toss-client-key.txt `
#     -SecretKeyFile C:\secure\toss-secret-key.txt `
#     -EnableAutoBilling `
#     -RefreshInstances

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Test", "Live")]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$ClientKeyFile,

    [Parameter(Mandatory = $true)]
    [string]$SecretKeyFile,

    [switch]$EnableAutoBilling,
    [switch]$RefreshInstances,
    [string]$AwsProfile = "default",
    [string]$Region = "ap-northeast-2",
    [string]$SsmApiEnv = "/academy/api/env",
    [string]$ApiAsgName = "academy-v1-api-asg"
)

$ErrorActionPreference = "Stop"

if ($EnableAutoBilling -and $Mode -ne "Live") {
    throw "Automatic billing can only be enabled with Mode=Live."
}

$resolvedClientKeyFile = (Resolve-Path -LiteralPath $ClientKeyFile).Path
$resolvedSecretKeyFile = (Resolve-Path -LiteralPath $SecretKeyFile).Path
$clientKey = (Get-Content -Raw -LiteralPath $resolvedClientKeyFile).Trim()
$secretKey = (Get-Content -Raw -LiteralPath $resolvedSecretKeyFile).Trim()

$expectedClientPrefix = if ($Mode -eq "Live") { "live_ck_" } else { "test_ck_" }
$expectedSecretPrefix = if ($Mode -eq "Live") { "live_sk_" } else { "test_sk_" }
if (-not $clientKey.StartsWith($expectedClientPrefix)) {
    throw "Client key does not match Mode=$Mode (expected $expectedClientPrefix prefix)."
}
if (-not $secretKey.StartsWith($expectedSecretPrefix)) {
    throw "Secret key does not match Mode=$Mode (expected $expectedSecretPrefix prefix)."
}

# Cloudflare R2 credentials must never shadow the selected AWS profile.
$env:AWS_ACCESS_KEY_ID = $null
$env:AWS_SECRET_ACCESS_KEY = $null
$env:AWS_SESSION_TOKEN = $null
if ($AwsProfile) { $env:AWS_PROFILE = $AwsProfile }

$awsArgs = @()
if ($AwsProfile) {
    $awsArgs = @("--profile", $AwsProfile)
}

. (Join-Path $PSScriptRoot "core\runtime-env-lock.ps1")
Enter-AcademyRuntimeEnvMutationLock `
    -Region $Region `
    -OwnerPrefix "toss-billing"
try {
$valueRaw = & aws ssm get-parameter `
    --name $SsmApiEnv `
    --with-decryption `
    --region $Region `
    @awsArgs `
    --query Parameter.Value `
    --output text
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($valueRaw)) {
    throw "Could not read $SsmApiEnv."
}

$json = $valueRaw
$wasBase64 = $false
if ($valueRaw -notmatch '^\s*\{') {
    try {
        $json = [Text.Encoding]::UTF8.GetString(
            [Convert]::FromBase64String($valueRaw)
        )
        $wasBase64 = $true
    } catch {
        throw "$SsmApiEnv is neither plain JSON nor base64(JSON)."
    }
}
$config = $json | ConvertFrom-Json

if ($EnableAutoBilling) {
    $encryptedWrites = [string]$config.BILLING_KEY_ENCRYPTION_WRITE_ENABLED
    $primaryKek = [string]$config.BILLING_KEY_ENCRYPTION_PRIMARY_KEY
    if ($encryptedWrites.ToLowerInvariant() -ne "true") {
        throw "BILLING_KEY_ENCRYPTION_WRITE_ENABLED must already be true."
    }
    if ([string]::IsNullOrWhiteSpace($primaryKek)) {
        throw "BILLING_KEY_ENCRYPTION_PRIMARY_KEY must already be configured."
    }
}

$config | Add-Member `
    -NotePropertyName "TOSS_PAYMENTS_CLIENT_KEY" `
    -NotePropertyValue $clientKey `
    -Force
$config | Add-Member `
    -NotePropertyName "TOSS_PAYMENTS_SECRET_KEY" `
    -NotePropertyValue $secretKey `
    -Force
$config | Add-Member `
    -NotePropertyName "TOSS_AUTO_BILLING_ENABLED" `
    -NotePropertyValue $(if ($EnableAutoBilling) { "true" } else { "false" }) `
    -Force

$updatedJson = $config | ConvertTo-Json -Compress -Depth 20
$updatedValue = if ($wasBase64) {
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($updatedJson))
} else {
    $updatedJson
}

$tempPayloadPath = [System.IO.Path]::Combine(
    [System.IO.Path]::GetTempPath(),
    "academy-toss-ssm-$([guid]::NewGuid().ToString('N')).json"
)
try {
    $putPayload = @{
        Name = $SsmApiEnv
        Type = "SecureString"
        Value = $updatedValue
        Overwrite = $true
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText(
        $tempPayloadPath,
        $putPayload,
        [System.Text.UTF8Encoding]::new($false)
    )

    Assert-AcademyRuntimeEnvMutationLock -Region $Region
    & aws ssm put-parameter `
        --cli-input-json "file://$tempPayloadPath" `
        --region $Region `
        @awsArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not update $SsmApiEnv."
    }
} finally {
    if (Test-Path -LiteralPath $tempPayloadPath) {
        Remove-Item -LiteralPath $tempPayloadPath -Force
    }
}

$activation = if ($EnableAutoBilling) { "enabled" } else { "disabled" }
Write-Host (
    "Toss $Mode key pair stored in $SsmApiEnv; automatic billing is $activation."
) -ForegroundColor Green

if ($RefreshInstances) {
    $refreshId = Start-AcademyInstanceRefresh `
        -AutoScalingGroupName $ApiAsgName `
        -Region $Region
    Write-Host "API instance refresh started: $refreshId" -ForegroundColor Cyan
    Wait-AcademyInstanceRefresh `
        -AutoScalingGroupName $ApiAsgName `
        -InstanceRefreshId $refreshId `
        -Region $Region
    Assert-AcademyPublicApiHealth
    Complete-AcademyRuntimeRefreshBoundary -Region $Region
    Write-Host "API instance refresh and public health readback passed." -ForegroundColor Green
} else {
    Write-Host (
        "API instances were not refreshed; the new settings are not active yet."
    ) -ForegroundColor Yellow
}

Write-Host "Delete the local key files after rollout verification." -ForegroundColor Yellow
} finally {
    Exit-AcademyRuntimeEnvMutationLock -Region $Region
}
