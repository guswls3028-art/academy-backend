# ==============================================================================
# Disable legacy API deploy crons on current API ASG instances.
#
# This is cleanup-only. It does not deploy code, clone repos, or restart services.
# Formal deploy paths are:
# - backend/.github/workflows/v1-build-and-push-latest.yml
# - scripts/v1/deploy.ps1 -AwsProfile default
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("Off", "Status")]
    [string]$Action = "Status",
    [string]$AwsProfile = ""
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
}

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
. (Join-Path $PSScriptRoot "resources\api.ps1")
. (Join-Path $PSScriptRoot "core\remote.ps1")
$null = Load-SSOT -Env "prod"

function Invoke-LegacyDeployCronCommand {
    param(
        [string]$Command,
        [string]$Label,
        [int]$TimeoutSec = 120
    )

    Write-Host "$Label..." -ForegroundColor Cyan
    $results = @(Invoke-ApiSsmShellCommand -Command $Command -TimeoutSec $TimeoutSec -AllInstances)
    $failed = $false
    foreach ($result in $results) {
        $ok = $result.Status -eq "Success" -and $result.ResponseCode -eq 0
        Write-Host "  $($result.InstanceId) : $($result.Status)" -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
        if ($result.StandardOutputContent) {
            Write-Host $result.StandardOutputContent.Trim() -ForegroundColor Gray
        }
        if ($result.StandardErrorContent) {
            Write-Host $result.StandardErrorContent.Trim() -ForegroundColor Red
        }
        if (-not $ok) { $failed = $true }
    }

    if ($failed) {
        exit 1
    }
}

$statusCmd = @'
echo '=== Crontab ==='
crontab -l 2>/dev/null || echo '(no crontab)'
echo ''
echo '=== Legacy deploy state files ==='
for f in /home/ec2-user/.academy-hot-deploy-state /home/ec2-user/.academy-rapid-deploy-last; do
  if [ -f "$f" ]; then
    echo "--- $f"
    cat "$f"
  else
    echo "$f: absent"
  fi
done
'@

$offCmd = @'
set -e
if ! command -v crontab >/dev/null 2>&1; then
  echo 'crontab command not found; nothing to clean.'
  exit 0
fi

current="$(crontab -l 2>/dev/null || true)"
if [ -z "$current" ]; then
  echo 'No crontab present.'
else
  filtered="$(printf '%s\n' "$current" \
    | grep -v 'deploy_api_on_server\.sh' \
    | grep -v 'auto_deploy_cron' \
    | grep -v 'hot_deploy_watch\.sh' \
    | grep -v 'hot_deploy_on\.sh' || true)"
  if [ -n "$filtered" ]; then
    printf '%s\n' "$filtered" | crontab -
    echo 'Legacy deploy cron entries removed; unrelated entries preserved.'
  else
    crontab -r 2>/dev/null || true
    echo 'Crontab cleared; it contained no unrelated entries.'
  fi
fi

rm -f /home/ec2-user/.academy-hot-deploy-state /home/ec2-user/.academy-rapid-deploy-last 2>/dev/null || true
echo 'Legacy deploy state files removed if present.'
'@

switch ($Action) {
    "Off" {
        Invoke-LegacyDeployCronCommand -Command $offCmd -Label "Disable legacy deploy crons"
    }
    "Status" {
        Invoke-LegacyDeployCronCommand -Command $statusCmd -Label "Legacy deploy cron status" -TimeoutSec 60
    }
}
