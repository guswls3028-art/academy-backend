# ==============================================================================
# 원격 빌드 서버(academy-build-arm64)에서 Docker 이미지 빌드 + ECR 푸시
# 로컬에 Docker 불필요. SSM으로 빌드 서버에 명령 전달.
#
# 사용:
#   .\scripts\build_and_push_ecr_remote.ps1 -ApiOnly
#   .\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly
#   .\scripts\build_and_push_ecr_remote.ps1 -ApiOnly -GitRepoUrl "https://github.com/..."
#   .\scripts\build_and_push_ecr_remote.ps1 -NoCache
# ==============================================================================

param(
    [switch]$ApiOnly = $false,
    [switch]$VideoWorkerOnly = $false,
    [switch]$NoCache = $false,
    [switch]$SkipPrune = $false,   # true면 푸시 후 낡은 이미지/캐시 정리 스킵 (기본: 정리함)
    [string]$GitRepoUrl = "https://github.com/guswls3028-art/academy-backend.git",   # 빌드 서버에서 clone/pull 할 레포. 기본값 사용 시 생략.
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

Write-Host "`n=== ECR Build/Push (remote build server) ===" -ForegroundColor Cyan
if ($ApiOnly) { Write-Host "  ApiOnly" -ForegroundColor Gray }
if ($VideoWorkerOnly) { Write-Host "  VideoWorkerOnly" -ForegroundColor Gray }
if ($NoCache) { Write-Host "  NoCache" -ForegroundColor Gray }
Write-Host ""

# 1) 빌드 인스턴스 찾기 (running or stopped)
$existing = aws ec2 describe-instances --region $Region `
    --filters "Name=tag:Name,Values=academy-build-arm64" "Name=instance-state-name,Values=running,stopped" `
    --query "Reservations[].Instances[].[InstanceId,State.Name]" --output text 2>&1
$buildInstanceId = $null
$buildState = $null
if ($existing -match "i-\S+\s+(running|stopped)") {
    $parts = $existing.Trim() -split "\s+", 2
    $buildInstanceId = $parts[0]
    $buildState = $parts[1]
}

if (-not $buildInstanceId) {
    Write-Host "ERROR: academy-build-arm64 인스턴스를 찾을 수 없습니다 (running/stopped)." -ForegroundColor Red
    Write-Host "  먼저 full_redeploy 한 번 실행하거나, launch_build_instance.ps1 로 빌드 서버를 만드세요." -ForegroundColor Yellow
    exit 1
}

Write-Host "[1] Build instance: $buildInstanceId (state: $buildState)" -ForegroundColor Cyan
if ($buildState -eq "stopped") {
    Write-Host "     Starting instance..." -ForegroundColor Gray
    aws ec2 start-instances --instance-ids $buildInstanceId --region $Region 2>&1 | Out-Null
    aws ec2 wait instance-running --instance-ids $buildInstanceId --region $Region
    Start-Sleep -Seconds 20
}

# 2) SSM Online 대기
Write-Host "[2] Waiting for SSM Online (max 3 min)..." -ForegroundColor Cyan
$ssmReady = $false
for ($i = 0; $i -lt 18; $i++) {
    Start-Sleep -Seconds 10
    $info = aws ssm describe-instance-information --region $Region --filters "Key=InstanceIds,Values=$buildInstanceId" --query "InstanceInformationList[0].PingStatus" --output text 2>$null
    if ($info -eq "Online") { $ssmReady = $true; break }
}
if (-not $ssmReady) {
    Write-Host "ERROR: SSM Online이 되지 않습니다. 인스턴스 IAM 역할에 SSM 권한이 있는지 확인하세요." -ForegroundColor Red
    exit 1
}
Start-Sleep -Seconds 5

# 3) 원격에서 실행할 명령 구성 (SSM 파라미터 4KB 제한 회피: 짧은 명령만 전달)
$envParts = @()
if ($ApiOnly) { $envParts += "API_ONLY=1" }
if ($VideoWorkerOnly) { $envParts += "VIDEO_WORKER_ONLY=1" }
if ($NoCache) { $envParts += "NO_CACHE=1" }
if ($SkipPrune) { $envParts += "DOCKER_SKIP_PRUNE=1" }
$envLine = if ($envParts.Count -gt 0) { "export " + ($envParts -join " ") } else { "" }

# 깃 레포: 있으면 pull, 없으면 clone (기본값으로 레포 사용)
$repoLine = "cd /home/ec2-user/build && (test -d academy && (cd academy && git fetch && git reset --hard origin/main && git pull)) || (git clone '" + $GitRepoUrl + "' academy && cd academy)"

# 한 줄로 합쳐서 SSM commands 배열을 1개 요소만 쓰기 (JSON 이스케이프 단순화)
$oneLine = "set -e; " + $repoLine + "; cd /home/ec2-user/build/academy; "
if ($envLine) { $oneLine += $envLine + "; " }
$oneLine += "./scripts/build_and_push_ecr_on_ec2.sh; echo REMOTE_BUILD_OK"

# 4) SSM Send Command (full_redeploy/check_worker_docker 스타일: 백틱 연속, parameters는 JSON 이스케이프)
$cmdEscaped = $oneLine -replace '\\', '\\\\' -replace '"', '\"'

Write-Host "[3] Running build on remote (timeout 60 min)..." -ForegroundColor Cyan
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
# PowerShell 더블쿼트 안에서 " 를 백틱 이스케이프해 한 인자로 전달 (프로젝트 SSM 호출 스타일 유지)
$cmdResult = aws ssm send-command --region $Region `
    --instance-ids $buildInstanceId `
    --document-name "AWS-RunShellScript" `
    --parameters "commands=[`"$cmdEscaped`"]" `
    --timeout-seconds 3600 `
    --output json 2>&1
$ErrorActionPreference = $prevErr
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "ERROR: SSM send-command failed (exit $exitCode)" -ForegroundColor Red
    Write-Host $cmdResult -ForegroundColor Red
    exit 1
}

try {
    $cmdObj = $cmdResult | ConvertFrom-Json
    $cmdId = $cmdObj.Command.CommandId
} catch {
    Write-Host "ERROR: Could not parse AWS response. Raw output:" -ForegroundColor Red
    Write-Host $cmdResult -ForegroundColor Red
    exit 1
}
if (-not $cmdId) {
    Write-Host "ERROR: CommandId not found in response" -ForegroundColor Red
    Write-Host $cmdResult -ForegroundColor Red
    exit 1
}

# 5) 완료 대기
Write-Host "     CommandId: $cmdId (polling every 30s)" -ForegroundColor Gray
$done = $false
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 30
    $status = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $buildInstanceId --query "Status" --output text 2>&1
    if ($status -eq "Success") { $done = $true; break }
    if ($status -eq "Failed" -or $status -eq "Cancelled") {
        $detail = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $buildInstanceId --output text 2>&1
        Write-Host "ERROR: Remote build failed: $detail" -ForegroundColor Red
        exit 1
    }
    Write-Host "     ... $status ($($i * 30)s)" -ForegroundColor Gray
}

if (-not $done) {
    Write-Host "ERROR: Timeout. Check AWS Console > SSM > Run Command > $cmdId" -ForegroundColor Red
    exit 1
}

Write-Host "`nDone. 원격 빌드/푸시 완료." -ForegroundColor Green
if ($ApiOnly) {
    Write-Host "  이제 배포: .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget api" -ForegroundColor Gray
} elseif ($VideoWorkerOnly) {
    Write-Host "  Video = Batch 전용. 이미지만 ECR에 푸시됨. API/워커 배포: .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget api" -ForegroundColor Gray
} else {
    Write-Host "  이제 배포: .\scripts\full_redeploy.ps1 -SkipBuild" -ForegroundColor Gray
}
Write-Host ""
