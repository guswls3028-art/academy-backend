param(
    [ValidateSet("true", "false")]
    [string]$Enabled = "true",
    [string]$AwsProfile = "",
    [switch]$RefreshMessagingWorker
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
}
. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
. (Join-Path $PSScriptRoot "core\ssm-safe-update.ps1")
$null = Load-SSOT -Env "prod"

function Get-RuntimeBindingKey {
    param([string]$Name, [ValidateSet("plain", "base64")][string]$Wrapping)
    $raw = aws ssm get-parameter --name $Name --with-decryption --query "Parameter.Value" --output text --region $script:Region 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $raw) { throw "Failed to read $Name" }
    $json = ($raw | Out-String).Trim()
    if ($Wrapping -eq "base64") {
        $json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($json))
    }
    $obj = $json | ConvertFrom-Json
    return [string]$obj.PSObject.Properties["MESSAGING_TENANT_BINDING_KEY"].Value
}

$apiKey = Get-RuntimeBindingKey -Name $script:SsmApiEnv -Wrapping "plain"
$workerKey = Get-RuntimeBindingKey -Name $script:SsmWorkersEnv -Wrapping "base64"
if ($apiKey.Length -lt 32 -or $workerKey.Length -lt 32) {
    throw "Dedicated messaging tenant-binding key is missing or too short. Run deploy env sync first."
}
if ($apiKey -cne $workerKey) {
    throw "API/worker messaging tenant-binding keys differ; enforcement aborted."
}

$updates = @{ "MESSAGING_TENANT_BINDING_ENFORCED" = $Enabled }
Update-AcademySSMParameter -Name $script:SsmApiEnv -KeyUpdates $updates -ExpectMinKeys 50 -Wrapping "plain" -Region $script:Region | Out-Null
Update-AcademySSMParameter -Name $script:SsmWorkersEnv -KeyUpdates $updates -ExpectMinKeys 35 -Wrapping "base64" -Region $script:Region | Out-Null
Write-Host "Messaging tenant-binding enforcement set to $Enabled in API and worker env." -ForegroundColor Green

if ($RefreshMessagingWorker) {
    try {
        $refresh = Invoke-AwsJson @(
            "autoscaling", "start-instance-refresh",
            "--auto-scaling-group-name", $script:MessagingASGName,
            "--region", $script:Region,
            "--output", "json"
        )
        Write-Host "Messaging worker refresh started: $($refresh.InstanceRefreshId)" -ForegroundColor Green
    } catch {
        if ($_.Exception.Message -match "InstanceRefreshInProgress") {
            Write-Host "Messaging worker refresh is already in progress." -ForegroundColor Yellow
        } else {
            throw
        }
    }
}
