# ==============================================================================
# Hot Deploy 원격 제어 (API only — ECR image digest 기반)
# ==============================================================================
# ECR에 새 academy-api 이미지가 push된 경우에만 API 컨테이너를 pull/restart.
# git SHA 비교 방식(api-auto-deploy-remote.ps1)과 분리된 별도 메커니즘.
# Workers(ai/video/messaging)는 절대 건드리지 않는다.
#
# 사용:
#   Hot Deploy ON:     pwsh scripts/v1/hot-deploy-remote.ps1 -Action On  -AwsProfile default
#   Hot Deploy OFF:    pwsh scripts/v1/hot-deploy-remote.ps1 -Action Off -AwsProfile default
#   상태 확인:         pwsh scripts/v1/hot-deploy-remote.ps1 -Action Status -AwsProfile default
#
# ON 동작:
#   1) EC2에 repo가 없으면 clone, 있으면 git pull
#   2) bash scripts/hot_deploy_on.sh 실행 → cron 등록 (2분마다 ECR digest 체크)
#
# OFF 동작:
#   1) EC2에서 bash scripts/hot_deploy_off.sh 실행 → cron 제거
#
# Status 동작:
#   crontab -l + state file + 현재 실행 중인 컨테이너 이미지 정보 출력
#
# 기존 api-auto-deploy-remote.ps1(git SHA 기반)과 충돌 없음:
#   - 락 파일이 다름: /tmp/academy_hot_deploy.lock (신규) vs /tmp/academy_deploy.lock (기존)
#   - 두 메커니즘 동시 활성화 비권장 (중복 배포 가능)
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("On", "Off", "Status")]
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

# RepoUrl 미지정 시 로컬 origin URL 사용
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $RepoUrl -and $Action -eq "On") {
    try {
        $origin = & git -C $repoRoot remote get-url origin 2>$null
        if ($origin -and $origin.Trim()) { $RepoUrl = $origin.Trim() }
    } catch { }
}
if (-not $RepoUrl -and $Action -eq "On") {
    $RepoUrl = "https://github.com/guswls3028-art/academy-backend.git"
}

$ids = @(Get-APIASGInstanceIds)
if (-not $ids -or $ids.Count -eq 0) {
    Write-Host "API ASG 인스턴스 없음. ASG: $($script:ApiASGName)" -ForegroundColor Yellow
    exit 1
}

$region    = $script:Region
$repoPath  = $RepoPath.TrimEnd('/')

# ── On: ensure repo on EC2, then register cron ────────────────────────────────
$ensureRepoCmd = $null
if ($Action -eq "On") {
    $repoUrlEscaped = $RepoUrl -replace "'", "'\''"
    $remoteSetup = @"
set -e
REPO_PATH='$repoPath'
REPO_URL='$repoUrlEscaped'
command -v git >/dev/null 2>&1 || (yum install -y git 2>/dev/null || dnf install -y git 2>/dev/null || true)
command -v crontab >/dev/null 2>&1 || (yum install -y cronie 2>/dev/null && systemctl start crond 2>/dev/null; dnf install -y cronie 2>/dev/null && systemctl start crond 2>/dev/null; true)
if [ -n "`$REPO_URL" ] && [ ! -d "`$REPO_PATH/.git" ]; then
  echo 'Cloning repo...'
  mkdir -p "`$(dirname "`$REPO_PATH")"
  git clone --depth 1 -b main "`$REPO_URL" "`$REPO_PATH"
  git config --global --add safe.directory "`$REPO_PATH"
  chown -R ec2-user:ec2-user "`$REPO_PATH" 2>/dev/null || true
fi
git config --global --add safe.directory "`$REPO_PATH" 2>/dev/null || true
cd "`$REPO_PATH" && git fetch origin main && git reset --hard origin/main
"@
    $remoteSetup = $remoteSetup -replace "`r`n", "`n" -replace "`r", "`n"
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($remoteSetup))
    $ensureRepoCmd = "echo $b64 | base64 -d | bash"
}

# ── SSM RunCommand helper ─────────────────────────────────────────────────────
function Invoke-RemoteCommand {
    param([string[]]$Commands, [string]$Label, [int]$TimeoutSec = 180)
    $params = @{ commands = $Commands }
    $paramsJson = $params | ConvertTo-Json -Compress
    Write-Host "$Label (인스턴스: $($ids -join ', '))..." -ForegroundColor Cyan
    foreach ($instId in $ids) {
        try {
            $sendOut = Invoke-AwsJson @(
                "ssm", "send-command",
                "--instance-ids", $instId,
                "--document-name", "AWS-RunShellScript",
                "--parameters", $paramsJson,
                "--region", $region,
                "--output", "json"
            ) 2>$null
            $cmdId = $sendOut.Command.CommandId
            if (-not $cmdId) {
                Write-Host "  $instId : send-command 실패" -ForegroundColor Red
                continue
            }
            $waited = 0
            while ($waited -lt $TimeoutSec) {
                Start-Sleep -Seconds 3
                $waited += 3
                $inv = $null
                try {
                    $inv = Invoke-AwsJson @(
                        "ssm", "get-command-invocation",
                        "--command-id", $cmdId,
                        "--instance-id", $instId,
                        "--region", $region,
                        "--output", "json"
                    ) 2>$null
                } catch { }
                if (-not $inv -or -not $inv.Status) { continue }
                if ($inv.Status -eq "Success") {
                    Write-Host "  $instId : OK" -ForegroundColor Green
                    if ($inv.StandardOutputContent) {
                        Write-Host $inv.StandardOutputContent.Trim() -ForegroundColor Gray
                    }
                    break
                }
                if ($inv.Status -in @("Failed", "Cancelled", "TimedOut")) {
                    Write-Host "  $instId : $($inv.Status)" -ForegroundColor Red
                    if ($inv.StandardErrorContent) {
                        Write-Host $inv.StandardErrorContent.Trim() -ForegroundColor Red
                    }
                    break
                }
            }
            if ($waited -ge $TimeoutSec) {
                Write-Host "  $instId : Timeout (${TimeoutSec}s). 로그: /home/ec2-user/hot_deploy.log" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  $instId : $_" -ForegroundColor Red
        }
    }
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
switch ($Action) {
    "On" {
        $onCmd = "cd $repoPath && REPO_DIR=$repoPath bash scripts/hot_deploy_on.sh"
        Invoke-RemoteCommand `
            -Commands @($ensureRepoCmd, $onCmd) `
            -Label "Hot Deploy ON (ECR digest 기반, API only)" `
            -TimeoutSec 300
    }
    "Off" {
        # Off: update repo first so we have the latest off script, then disable
        $offSetup = "test -d $repoPath/.git && (cd $repoPath && git fetch origin main && git reset --hard origin/main) || true"
        $offCmd   = "test -f $repoPath/scripts/hot_deploy_off.sh && bash $repoPath/scripts/hot_deploy_off.sh || { crontab -l 2>/dev/null | grep -v hot_deploy_watch | crontab - ; echo 'OK — hot_deploy_watch removed from crontab.'; }"
        Invoke-RemoteCommand `
            -Commands @($offSetup, $offCmd) `
            -Label "Hot Deploy OFF" `
            -TimeoutSec 120
    }
    "Status" {
        $statusCmd = @"
echo '=== Crontab ===' && crontab -l 2>/dev/null || echo '(no crontab)'
echo ''
echo '=== Hot Deploy State ===' && cat /home/ec2-user/.academy-hot-deploy-state 2>/dev/null || echo '(no state file)'
echo ''
echo '=== Last Rapid Deploy ===' && cat /home/ec2-user/.academy-rapid-deploy-last 2>/dev/null || echo '(no rapid deploy info)'
echo ''
echo '=== Running Container ===' && docker inspect academy-api --format 'ID={{.Id}} Image={{.Config.Image}} Status={{.State.Status}}' 2>/dev/null || echo '(academy-api not running)'
"@
        Invoke-RemoteCommand `
            -Commands @($statusCmd) `
            -Label "Hot Deploy 상태" `
            -TimeoutSec 60
    }
}

Write-Host ""
Write-Host "Hot Deploy 요약:" -ForegroundColor Cyan
Write-Host "  ON:     ECR에 새 academy-api 이미지 push 시에만 2분 이내 자동 반영" -ForegroundColor Gray
Write-Host "  OFF:    자동 반영 중단" -ForegroundColor Gray
Write-Host "  STATUS: crontab + state file + 실행 중 컨테이너 확인" -ForegroundColor Gray
Write-Host "  로그:   /home/ec2-user/hot_deploy.log" -ForegroundColor Gray
Write-Host "  상태:   /home/ec2-user/.academy-hot-deploy-state" -ForegroundColor Gray
Write-Host "  주의:   api-auto-deploy-remote.ps1(git SHA 기반)과 동시 활성화 비권장" -ForegroundColor Yellow
