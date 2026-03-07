# ==============================================================================
# API 서버 자동 배포 원격 제어 (SSM Send-Command)
# ==============================================================================
# API ASG 인스턴스에서 2분마다 git 기준 자동 배포 cron ON/OFF/상태 확인/수동 배포 1회
# 사용:
#   pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action On   -AwsProfile default
#   pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Off  -AwsProfile default
#   pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Status -AwsProfile default
#   pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Deploy -AwsProfile default
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("On", "Off", "Status", "Deploy")]
    [string]$Action = "Status",
    [string]$RepoPath = "/home/ec2-user/academy",
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
$null = Load-SSOT -Env "prod"

$ids = @(Get-APIASGInstanceIds)
if (-not $ids -or $ids.Count -eq 0) {
    Write-Host "API ASG 인스턴스 없음. ASG 이름: $($script:ApiASGName)" -ForegroundColor Yellow
    exit 1
}

$region = $script:Region
$repoPath = $RepoPath.TrimEnd('/')

function Invoke-RemoteCommand {
    param([string]$Command, [string]$Label)
    $params = @{ commands = @($Command) }
    $paramsJson = $params | ConvertTo-Json -Compress
    Write-Host "$Label (인스턴스: $($ids -join ', '))..." -ForegroundColor Cyan
    foreach ($instId in $ids) {
        try {
            $sendOut = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instId, "--document-name", "AWS-RunShellScript", "--parameters", $paramsJson, "--region", $region, "--output", "json") 2>$null
            $cmdId = $sendOut.Command.CommandId
            if (-not $cmdId) { Write-Host "  $instId : send-command failed" -ForegroundColor Red; continue }
            $wait = 0
            while ($wait -lt 120) {
                Start-Sleep -Seconds 3
                $wait += 3
                $inv = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cmdId, "--instance-id", $instId, "--region", $region, "--output", "json") 2>$null
                if ($inv.Status -eq "Success") {
                    Write-Host "  $instId : OK" -ForegroundColor Green
                    if ($inv.StandardOutputContent) { Write-Host $inv.StandardOutputContent -ForegroundColor Gray }
                    break
                }
                if ($inv.Status -eq "Failed" -or $inv.Status -eq "Cancelled") {
                    Write-Host "  $instId : $($inv.Status)" -ForegroundColor Red
                    if ($inv.StandardErrorContent) { Write-Host $inv.StandardErrorContent -ForegroundColor Red }
                    break
                }
            }
        } catch {
            Write-Host "  $instId : $_" -ForegroundColor Red
        }
    }
}

switch ($Action) {
    "On" {
        $cmd = "cd $repoPath && git fetch origin main && git reset --hard origin/main && bash scripts/auto_deploy_cron_on.sh"
        Invoke-RemoteCommand -Command $cmd -Label "자동 배포 ON (2분마다 git 기준 배포 + 구이미지 제거)"
    }
    "Off" {
        $cmd = "cd $repoPath && git fetch origin main && git reset --hard origin/main && bash scripts/auto_deploy_cron_off.sh"
        Invoke-RemoteCommand -Command $cmd -Label "자동 배포 OFF"
    }
    "Status" {
        $cmd = "crontab -l 2>/dev/null || echo 'No crontab'"
        Invoke-RemoteCommand -Command $cmd -Label "crontab 상태"
    }
    "Deploy" {
        $cmd = "cd $repoPath && git fetch origin main && git reset --hard origin/main && bash scripts/deploy_api_on_server.sh"
        Invoke-RemoteCommand -Command $cmd -Label "수동 배포 1회 (git pull + build + 구이미지 제거 + 재시작)"
    }
}

Write-Host "`n요약:" -ForegroundColor Cyan
Write-Host "  수동 배포(서버에서): cd $repoPath && bash scripts/deploy_api_on_server.sh" -ForegroundColor Gray
Write-Host "  자동 배포 ON (서버에서):  cd $repoPath && bash scripts/auto_deploy_cron_on.sh" -ForegroundColor Gray
Write-Host "  자동 배포 OFF (서버에서): cd $repoPath && bash scripts/auto_deploy_cron_off.sh" -ForegroundColor Gray
