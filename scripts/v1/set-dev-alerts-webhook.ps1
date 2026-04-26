# Set DEV_ALERTS_WEBHOOK_URL in /academy/api/env (SSM SecureString) and refresh /opt/api.env on running API instances.
# 사용:
#   pwsh scripts/v1/set-dev-alerts-webhook.ps1 -Url "https://hooks.slack.com/services/..."
#   pwsh scripts/v1/set-dev-alerts-webhook.ps1 -Url ""  # 비활성화 (제거)
#
# 인프라:
#   /academy/api/env (SecureString JSON) → /opt/api.env on each API instance via UserData/refresh.
#   academy-api 컨테이너는 /opt/api.env --env-file로 마운트.
#   변경 후 ASG instance refresh 또는 본 스크립트의 -RefreshContainers 옵션 사용.

param(
    [Parameter(Mandatory=$true)] [AllowEmptyString()] [string]$Url,
    [switch]$RefreshContainers,
    [string]$Region = "ap-northeast-2",
    [string]$SsmApiEnv = "/academy/api/env",
    [string]$ApiAsgName = "academy-v1-api-asg"
)

$ErrorActionPreference = "Stop"

if ($Url -ne "" -and -not ($Url -match '^https?://')) {
    Write-Host "ERROR: -Url must start with http(s)://" -ForegroundColor Red
    exit 1
}

# 1) Read current SSM parameter
Write-Host "[1/3] Reading SSM $SsmApiEnv..."
$existing = aws ssm get-parameter --name $SsmApiEnv --with-decryption --region $Region --output json 2>&1 | ConvertFrom-Json
if (-not $existing.Parameter -or -not $existing.Parameter.Value) {
    Write-Host "ERROR: $SsmApiEnv not found or empty." -ForegroundColor Red
    exit 1
}

$valueRaw = $existing.Parameter.Value
$isBase64 = ($valueRaw -match '^[A-Za-z0-9+/]+=*$') -and ($valueRaw.Length -gt 100)
$jsonStr = $valueRaw
if ($isBase64) {
    $jsonStr = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valueRaw))
}
$obj = $jsonStr | ConvertFrom-Json

# 2) Update DEV_ALERTS_WEBHOOK_URL
$prev = $obj.PSObject.Properties["DEV_ALERTS_WEBHOOK_URL"].Value
if ($Url -eq "") {
    if ($obj.PSObject.Properties["DEV_ALERTS_WEBHOOK_URL"]) {
        $obj.PSObject.Properties.Remove("DEV_ALERTS_WEBHOOK_URL")
        Write-Host "[2/3] Removed DEV_ALERTS_WEBHOOK_URL (was: $(if ($prev) { '<set>' } else { '<unset>' }))"
    } else {
        Write-Host "[2/3] DEV_ALERTS_WEBHOOK_URL already unset, no change"
    }
} else {
    $obj | Add-Member -NotePropertyName "DEV_ALERTS_WEBHOOK_URL" -NotePropertyValue $Url -Force
    $masked = if ($Url.Length -gt 30) { $Url.Substring(0, 30) + "..." } else { $Url }
    Write-Host "[2/3] Set DEV_ALERTS_WEBHOOK_URL=$masked (was: $(if ($prev) { '<set>' } else { '<unset>' }))"
}

# 3) Write back
$newJson = $obj | ConvertTo-Json -Compress -Depth 10
$newValue = $newJson
if ($isBase64) {
    $newValue = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($newJson))
}
aws ssm put-parameter --name $SsmApiEnv --type SecureString --value $newValue --overwrite --region $Region | Out-Null
Write-Host "[3/3] SSM updated."

# Optional: refresh /opt/api.env on running instances (without ASG refresh)
if ($RefreshContainers) {
    Write-Host "`n[refresh] Refreshing /opt/api.env on InService API instances..."
    $instancesJson = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $ApiAsgName --region $Region --output json | ConvertFrom-Json
    $instances = @($instancesJson.AutoScalingGroups[0].Instances | Where-Object { $_.LifecycleState -eq "InService" } | ForEach-Object { $_.InstanceId })
    if ($instances.Count -eq 0) {
        Write-Host "No InService instances found." -ForegroundColor Yellow
        exit 0
    }
    Write-Host "Targets: $($instances -join ', ')"

    # /opt/api.env regen + container restart (refresh-api-env.sh 패턴 차용)
    $refreshCmd = "set -e; aws ssm get-parameter --name $SsmApiEnv --with-decryption --region $Region --query Parameter.Value --output text | (head -c 4 | grep -q '{' && cat || base64 -d) > /opt/api.env.new && mv /opt/api.env.new /opt/api.env && docker restart academy-api 2>&1 | tail -1"
    $params = @{ commands = @($refreshCmd) } | ConvertTo-Json -Compress

    foreach ($id in $instances) {
        $send = aws ssm send-command --instance-ids $id --document-name "AWS-RunShellScript" --parameters $params --region $Region --output json | ConvertFrom-Json
        Write-Host "  $id : SSM command $($send.Command.CommandId) sent"
    }
    Write-Host "Refresh dispatched. Verify with: docker exec academy-api env | grep DEV_ALERTS_WEBHOOK_URL"
} else {
    Write-Host "`nNote: /opt/api.env on running instances NOT refreshed yet."
    Write-Host "  Either run with -RefreshContainers, or wait for next ASG refresh / deploy."
}
