# Worker UserData: Messaging/AI ASG EC2 부팅 시 Docker + ECR pull + SSM env + run.
# SSM /academy/workers/env: base64(JSON) 저장 → 디코딩 후 KEY=VALUE env 파일 생성.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용.
$ErrorActionPreference = "Stop"

function Assert-ImmutableEcrImageUri {
    param([string]$ImageUri)
    if ($ImageUri -notmatch '@sha256:[0-9a-f]{64}$') {
        throw "Runtime image must be pinned by ECR digest: $ImageUri"
    }
}

function Get-ReleaseManifestImage {
    param([Parameter(Mandatory = $true)][string]$RepoName)
    $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    $manifestPath = Join-Path $repoRoot "docs\reports\release-manifest.latest.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Verified release manifest not found: $manifestPath. Run a successful full CI deployment first."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        [int]$manifest.schemaVersion -ne 1 -or
        -not [bool]$manifest.complete -or
        [string]$manifest.status -ne "successful" -or
        @($manifest.images.PSObject.Properties).Count -ne 6
    ) {
        throw "Release manifest is not a complete verified six-image release: $manifestPath"
    }
    $property = $manifest.images.PSObject.Properties[$RepoName]
    if (-not $property) { throw "Release manifest has no image entry for $RepoName" }
    $digest = [string]$property.Value.digest
    if ($digest -notmatch '^sha256:[0-9a-f]{64}$') {
        throw "Release manifest digest is invalid for ${RepoName}: $digest"
    }
    return [PSCustomObject]@{
        Digest = $digest.ToLowerInvariant()
        Tag = [string]$property.Value.tag
        GitSha = [string]$manifest.gitSha
        VerifiedAt = [string]$manifest.verifiedAt
    }
}

function Get-ImmutableEcrImageUri {
    param([string]$RepoName, [string]$ImageTag = "")
    if (-not $RepoName) { throw "ECR repository name is required." }
    if (-not $script:AccountId -or -not $script:Region) { throw "AWS account/region SSOT is required to resolve an immutable image." }

    $detail = $null
    if ($ImageTag) {
        if ($ImageTag -notmatch '^sha-(?:[0-9a-f]{8,40}|[0-9a-f]{40}-run-[0-9]+-[0-9]+)$') {
            throw "Only CI sha-* image tags may be resolved for deployment: $ImageTag"
        }
        $result = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $RepoName, "--image-ids", "imageTag=$ImageTag", "--region", $script:Region, "--output", "json") 2>$null
        if ($result -and $result.imageDetails) { $detail = @($result.imageDetails)[0] }
    } else {
        $releaseImage = Get-ReleaseManifestImage -RepoName $RepoName
        $result = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $RepoName, "--image-ids", "imageDigest=$($releaseImage.Digest)", "--region", $script:Region, "--output", "json") 2>$null
        if ($result -and $result.imageDetails) { $detail = @($result.imageDetails)[0] }
    }

    $digest = if ($detail) { [string]$detail.imageDigest } else { "" }
    if ($digest -notmatch '^sha256:[0-9a-f]{64}$') {
        $label = if ($ImageTag) { "${RepoName}:$ImageTag" } else { "$RepoName from verified release manifest" }
        throw "Immutable ECR image digest not found: $label"
    }
    $uri = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/${RepoName}@${digest}"
    Assert-ImmutableEcrImageUri -ImageUri $uri
    return $uri
}

function Get-LatestWorkerImageUri {
    param([string]$RepoName)
    if (-not $RepoName) { return $null }
    if ($script:EcrUseLatestTag) {
        if ($script:EcrImmutableTagRequired) { throw "SSOT conflict: immutableTagRequired cannot be combined with useLatestTag." }
        return "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/${RepoName}:latest"
    }
    return Get-ImmutableEcrImageUri -RepoName $RepoName
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
