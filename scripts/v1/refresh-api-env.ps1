# ==============================================================================
# API 인스턴스에 최신 SSM env 적용 후 컨테이너 재시작
# ==============================================================================
# 기존 인스턴스는 부팅 시에만 SSM을 읽음. SSM 갱신 후 컨테이너만 재시작하려면 이 스크립트 사용.
# 사용: pwsh scripts/v1/refresh-api-env.ps1 -AwsProfile default
# ==============================================================================
param([string]$AwsProfile = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\core\env.ps1")
if ($AwsProfile) { $env:AWS_PROFILE = $AwsProfile; if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" } }

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"

$ids = @(Get-APIASGInstanceIds)
if (-not $ids -or $ids.Count -eq 0) {
    Write-Host "API ASG 인스턴스 없음." -ForegroundColor Yellow
    exit 1
}

$ssmParam = $script:SsmApiEnv
$region = $script:Region
$containerName = "academy-api"

# SSM에서 env 가져와서 /opt/api.env 갱신 후 컨테이너 재시작
$script = @"
set -e
export AWS_REGION='$region'
ENV_JSON='$(aws ssm get-parameter --name "$ssmParam" --with-decryption --query Parameter.Value --output text --region $region 2>/dev/null)' || true
if [ -n "$ENV_JSON" ]; then
  echo "$ENV_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(k+'='+str(v)) for k,v in d.items()]" 2>/dev/null > /opt/api.env || true
  if [ -s /opt/api.env ]; then
    echo "VIDEO_BATCH from api.env:"; grep VIDEO_BATCH /opt/api.env || true
    docker stop $containerName 2>/dev/null || true
    docker rm $containerName 2>/dev/null || true
    API_IMG='$(docker images -q academy-api 2>/dev/null | head -1)'
    if [ -z "$API_IMG" ]; then
      API_IMG='$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep academy-api | head -1)'
    fi
    if [ -z "$API_IMG" ]; then
      API_IMG='809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest'
    fi
    docker run -d --restart unless-stopped --name $containerName -p 8000:8000 --env-file /opt/api.env $API_IMG 2>&1 || echo "docker run failed"
    echo "Container restarted."
  fi
else
  echo "SSM fetch failed"
fi
"@

# JSON 이스케이프 (PowerShell에서 SSM 값에 따옴표 포함 시)
$script = $script -replace '"', '\"'
$script = $script -replace "`n", "\\n"

$params = @{ commands = @($script) }
$paramsJson = $params | ConvertTo-Json -Compress

Write-Host "API 인스턴스 $($ids -join ', ') 에 SSM env 적용 및 컨테이너 재시작 중..." -ForegroundColor Cyan
Write-Host "SSM param: $ssmParam" -ForegroundColor Gray

foreach ($instId in $ids) {
    try {
        $sendOut = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instId, "--document-name", "AWS-RunShellScript", "--parameters", $paramsJson, "--region", $region, "--output", "json") 2>$null
        $cmdId = $sendOut.Command.CommandId
        if (-not $cmdId) { Write-Host "  $instId : send-command failed" -ForegroundColor Red; continue }
        $wait = 0
        while ($wait -lt 60) {
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

Write-Host "`nhealthz 확인 후 업로드 테스트 진행 권장." -ForegroundColor Cyan
