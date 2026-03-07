# ==============================================================================
# API 서버 자동 배포 원격 제어 (SSM Send-Command)
# ==============================================================================
# API ASG 인스턴스에서 2분마다 git 기준 자동 배포 cron ON/OFF/상태 확인/수동 배포 1회
# RepoUrl 미지정 시 로컬 academy 리포의 git remote origin 사용 → 레포 없으면 자동 클론 후 진행.
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
    [string]$RepoUrl = "",
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

# RepoUrl 미지정 시 로컬 academy 리포 origin URL 사용 (On/Deploy 시 레포 없으면 클론에 사용)
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $RepoUrl -and ($Action -eq "On" -or $Action -eq "Deploy")) {
    try {
        $origin = & git -C $repoRoot remote get-url origin 2>$null
        if ($origin -and $origin.Trim()) { $RepoUrl = $origin.Trim() }
    } catch { }
}
if (-not $RepoUrl -and ($Action -eq "On" -or $Action -eq "Deploy")) {
    $RepoUrl = "https://github.com/guswls3028-art/academy-backend.git"
}

$ids = @(Get-APIASGInstanceIds)
if (-not $ids -or $ids.Count -eq 0) {
    Write-Host "API ASG 인스턴스 없음. ASG 이름: $($script:ApiASGName)" -ForegroundColor Yellow
    exit 1
}

$region = $script:Region
$repoPath = $RepoPath.TrimEnd('/')

# On/Deploy: SSM 전달 시 멀티라인/따옴표 이스케이프 문제 회피 → 스크립트를 base64로 인코딩 후 원격에서 디코딩 실행
$ensureAndFetchCmd = $null
if ($Action -eq "On" -or $Action -eq "Deploy") {
    $repoUrlEscaped = $RepoUrl -replace "'", "'\''"
    $remoteScript = @"
set -e
REPO_PATH='$repoPath'
REPO_URL='$repoUrlEscaped'
command -v git >/dev/null 2>&1 || (yum install -y git 2>/dev/null || dnf install -y git 2>/dev/null || true)
if [ -n "`$REPO_URL" ] && [ ! -d "`$REPO_PATH/.git" ]; then
  echo 'Cloning repo...'
  mkdir -p "`$(dirname "`$REPO_PATH")"
  git clone --depth 1 -b main "`$REPO_URL" "`$REPO_PATH"
  chown -R ec2-user:ec2-user "`$REPO_PATH" 2>/dev/null || true
  echo 'Clone done.'
fi
cd "`$REPO_PATH" && git fetch origin main && git reset --hard origin/main
"@
    $remoteScript = $remoteScript -replace "`r`n", "`n" -replace "`r", "`n"
    $scriptB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($remoteScript))
    $ensureAndFetchCmd = "echo $scriptB64 | base64 -d | bash"
}

function Invoke-RemoteCommand {
    param([string[]]$Commands, [string]$Label)
    $params = @{ commands = $Commands }
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
        Invoke-RemoteCommand -Commands @($ensureAndFetchCmd, "cd $repoPath && bash scripts/auto_deploy_cron_on.sh") -Label "자동 배포 ON (2분마다 git 기준 배포 + 구이미지 제거)"
    }
    "Off" {
        $cmd = "test -d $repoPath || { echo 'No repo at $repoPath (skip).'; exit 0; }; cd $repoPath && git fetch origin main && git reset --hard origin/main && bash scripts/auto_deploy_cron_off.sh"
        Invoke-RemoteCommand -Commands @($cmd) -Label "자동 배포 OFF"
    }
    "Status" {
        $cmd = "crontab -l 2>/dev/null || echo 'No crontab'"
        Invoke-RemoteCommand -Commands @($cmd) -Label "crontab 상태"
    }
    "Deploy" {
        Invoke-RemoteCommand -Commands @($ensureAndFetchCmd, "cd $repoPath && bash scripts/deploy_api_on_server.sh") -Label "수동 배포 1회 (git pull + build + 구이미지 제거 + 재시작)"
    }
}

Write-Host "`n요약:" -ForegroundColor Cyan
Write-Host "  수동 배포(서버에서): cd $repoPath && bash scripts/deploy_api_on_server.sh" -ForegroundColor Gray
Write-Host "  자동 배포 ON (서버에서):  cd $repoPath && bash scripts/auto_deploy_cron_on.sh" -ForegroundColor Gray
Write-Host "  자동 배포 OFF (서버에서): cd $repoPath && bash scripts/auto_deploy_cron_off.sh" -ForegroundColor Gray
