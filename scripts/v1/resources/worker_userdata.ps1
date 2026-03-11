# Worker UserData: Messaging/AI ASG EC2 부팅 시 Docker + ECR pull + SSM env + run.
# SSM /academy/workers/env: base64(JSON) 저장 → 디코딩 후 KEY=VALUE env 파일 생성.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용.
$ErrorActionPreference = "Stop"

function Get-LatestWorkerImageUri {
    param([string]$RepoName)
    if (-not $RepoName) { return $null }
    if ($script:EcrUseLatestTag) {
        $acc = $script:AccountId
        $reg = $script:Region
        return "${acc}.dkr.ecr.${reg}.amazonaws.com/$RepoName" + ":latest"
    }
    $list = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $RepoName, "--region", $script:Region, "--output", "json") 2>$null
    if (-not $list -or -not $list.imageDetails -or $list.imageDetails.Count -eq 0) { return $null }
    $nonLatest = @($list.imageDetails | Where-Object { $_.imageTags -and ($_.imageTags | Where-Object { $_ -ne "latest" }) } | ForEach-Object {
        $tag = ($_.imageTags | Where-Object { $_ -ne "latest" } | Select-Object -First 1)
        if ($tag) { [PSCustomObject]@{ Tag = $tag; Pushed = $_.imagePushedAt } }
    } | Where-Object { $_ })
    $tagToUse = $null
    if ($nonLatest.Count -gt 0) {
        $latest = $nonLatest | Sort-Object { $_.Pushed } -Descending | Select-Object -First 1
        $tagToUse = $latest.Tag
    } else {
        $withLatest = $list.imageDetails | Where-Object { $_.imageTags -and ($_.imageTags -contains "latest") } | Select-Object -First 1
        if ($withLatest) { $tagToUse = "latest" }
    }
    if (-not $tagToUse) { return $null }
    $acc = $script:AccountId
    $reg = $script:Region
    return "${acc}.dkr.ecr.${reg}.amazonaws.com/$RepoName" + ":" + $tagToUse
}

function Get-WorkerLaunchTemplateUserData {
    param([string]$ImageUri, [string]$Region, [string]$SsmParam, [string]$ContainerName)
    if (-not $ImageUri -or -not $Region -or -not $ContainerName) { return "" }
    $ecrHost = $ImageUri.Split("/")[0]
    $script = @"
#!/bin/bash
set -e
export AWS_REGION="$Region"
LOG=/var/log/academy-worker-userdata.log
touch "`$LOG"
log() { echo "`$(date -Iseconds) `$*" >> "`$LOG"; }
# 0) 네트워크/IMDS 준비 대기
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf --connect-timeout 2 http://169.254.169.254/latest/meta-data/instance-id >/dev/null 2>&1; then break; fi
  sleep 3
done
# 1) Docker 설치 및 기동
if command -v dnf &>/dev/null; then
  dnf install -y docker
else
  yum install -y docker
fi
systemctl start docker
systemctl enable docker
# 2) ECR 로그인 및 이미지 Pull
ecr_ok=false
for attempt in 1 2 3 4 5; do
  if aws ecr get-login-password --region $Region 2>>"`$LOG" | docker login --username AWS --password-stdin $ecrHost 2>>"`$LOG"; then
    if docker pull $ImageUri 2>>"`$LOG"; then ecr_ok=true; break; fi
  fi
  log "ECR attempt `$attempt failed, retrying in 15s"
  sleep 15
done
if [ "`$ecr_ok" != "true" ]; then
  log "ECR login/pull failed. Image: ${ImageUri}"
  exit 1
fi
# 3) Workers env (SSM base64(JSON) -> KEY=VALUE)
WORKER_ENV_FILE=""
if [ -n "$SsmParam" ]; then
  ENV_B64="`$(aws ssm get-parameter --name "$SsmParam" --with-decryption --query Parameter.Value --output text --region $Region 2>/dev/null)" || true
  if [ -n "`$ENV_B64" ]; then
    mkdir -p /opt
    echo "`$ENV_B64" | base64 -d 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); [print(k+'='+str(v)) for k,v in d.items()]" 2>/dev/null > /opt/workers.env || true
    [ -s /opt/workers.env ] && WORKER_ENV_FILE="--env-file /opt/workers.env"
  fi
fi
# 4) 기존 컨테이너 정리 후 실행
docker stop $ContainerName 2>/dev/null || true
docker rm $ContainerName 2>/dev/null || true
if ! docker run -d --restart unless-stopped --name $ContainerName -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker `$WORKER_ENV_FILE ${ImageUri} 2>>"`$LOG"; then
  log "docker run failed. Image: ${ImageUri}"
  exit 1
fi
"@
    return $script.Trim()
}
