# Enable or disable fixed-recipient operator incident SMS settings in /academy/api/env.
# The application rejects every recipient except 01031217466.
# Usage:
#   pwsh scripts/v1/set-dev-alerts-sms.ps1 -AwsProfile default
#   pwsh scripts/v1/set-dev-alerts-sms.ps1 -Disable -AwsProfile default

param(
    [switch]$Disable,
    [string]$AwsProfile = "default",
    [string]$Region = "ap-northeast-2",
    [string]$SsmApiEnv = "/academy/api/env"
)

$ErrorActionPreference = "Stop"
$ControlledPhone = "01031217466"

# Cloudflare R2 credentials must never shadow the selected AWS profile.
$env:AWS_ACCESS_KEY_ID = $null
$env:AWS_SECRET_ACCESS_KEY = $null
$env:AWS_SESSION_TOKEN = $null

$awsArgs = @()
if ($AwsProfile) {
    $awsArgs = @("--profile", $AwsProfile)
}

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

if ($Disable) {
    $config | Add-Member -NotePropertyName "DEV_ALERTS_SMS_ENABLED" -NotePropertyValue "false" -Force
    if ($config.PSObject.Properties["DEV_ALERTS_SMS_RECIPIENT"]) {
        $config.PSObject.Properties.Remove("DEV_ALERTS_SMS_RECIPIENT")
    }
} else {
    $config | Add-Member -NotePropertyName "DEV_ALERTS_SMS_ENABLED" -NotePropertyValue "true" -Force
    $config | Add-Member -NotePropertyName "DEV_ALERTS_SMS_RECIPIENT" -NotePropertyValue $ControlledPhone -Force
}

$updatedJson = $config | ConvertTo-Json -Compress -Depth 20
$updatedValue = if ($wasBase64) {
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($updatedJson))
} else {
    $updatedJson
}

& aws ssm put-parameter `
    --name $SsmApiEnv `
    --type SecureString `
    --value $updatedValue `
    --overwrite `
    --region $Region `
    @awsArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not update $SsmApiEnv."
}

$mode = if ($Disable) { "disabled" } else { "enabled for ***7466" }
Write-Host "Operator incident SMS $mode in $SsmApiEnv." -ForegroundColor Green
Write-Host "Dev Alerts Cron atomically syncs this setting before its next run." -ForegroundColor Cyan
