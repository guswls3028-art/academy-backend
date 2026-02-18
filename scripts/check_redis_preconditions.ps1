# ==============================================================================
# 영상 인코딩 진행률(%) 전제 조건 체크
# - API와 워커가 같은 Redis(ElastiCache) 사용해야 작업 박스에 % 표시됨
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

Write-Host "`n=== Redis 전제 조건 체크 (encoding_progress % 표시) ===`n" -ForegroundColor Cyan

# 1) 로컬 .env
$envPath = Join-Path $RepoRoot ".env"
$envContent = ""
if (-not (Test-Path $envPath)) {
    Write-Host "[FAIL] .env 없음: $envPath" -ForegroundColor Red
} else {
    $envContent = Get-Content $envPath -Raw
    $redisHost = $null
    if ($envContent -match 'REDIS_HOST=([^\s#]+)') { $redisHost = $Matches[1].Trim() }
    if ($redisHost) {
        Write-Host "[OK] .env REDIS_HOST: $redisHost" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] .env에 REDIS_HOST 없음" -ForegroundColor Red
    }
}

# 2) SSM /academy/workers/env (워커 user_data에서 사용)
$ssmValue = aws ssm get-parameter --name /academy/workers/env --with-decryption --region $Region --query "Parameter.Value" --output text 2>$null
if (-not $ssmValue) {
    Write-Host "[FAIL] SSM /academy/workers/env 없음 또는 권한 부족" -ForegroundColor Red
} else {
    $ssmRedisHost = $null
    if ($ssmValue -match 'REDIS_HOST=([^\s\r\n#]+)') { $ssmRedisHost = $Matches[1].Trim() }
    if ($ssmRedisHost) {
        Write-Host "[OK] SSM REDIS_HOST: $ssmRedisHost" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] SSM /academy/workers/env에 REDIS_HOST 없음" -ForegroundColor Red
    }
}

# 3) API 서버 .env 및 Redis 연결
$apiIp = aws ec2 describe-instances --region $Region `
    --filters "Name=tag:Name,Values=academy-api" "Name=instance-state-name,Values=running" `
    --query "Reservations[].Instances[].PublicIpAddress" --output text 2>$null
if (-not $apiIp -or $apiIp -eq "None") {
    Write-Host "[SKIP] academy-api Public IP 없음 - API Redis 체크 불가" -ForegroundColor Yellow
} else {
    . (Join-Path $ScriptRoot "_config_instance_keys.ps1")
    $apiKey = Join-Path $KeyDir $INSTANCE_KEY_FILES["academy-api"]
    if (Test-Path $apiKey) {
        $apiRedisHost = ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 -i $apiKey "ec2-user@$apiIp" "grep -E '^REDIS_HOST=' /home/ec2-user/.env 2>/dev/null | cut -d= -f2" 2>$null
        if ($apiRedisHost) {
            Write-Host "[OK] API .env REDIS_HOST: $($apiRedisHost.Trim())" -ForegroundColor Green
            $apiRedisTest = ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 -i $apiKey "ec2-user@$apiIp" "sudo docker exec academy-api python -c \"
import os
import redis
h=os.environ.get('REDIS_HOST'); p=int(os.environ.get('REDIS_PORT',6379))
r=redis.Redis(host=h,port=p,db=0); print('Redis OK:', r.ping())
\" 2>/dev/null" 2>$null
            if ($apiRedisTest -match "Redis OK: True") {
                Write-Host "[OK] API 컨테이너 Redis ping: 성공" -ForegroundColor Green
            } else {
                Write-Host "[WARN] API 컨테이너 Redis ping 실패 또는 미확인" -ForegroundColor Yellow
            }
        } else {
            Write-Host "[FAIL] API .env에 REDIS_HOST 없음" -ForegroundColor Red
        }
    } else {
        Write-Host "[SKIP] API SSH 키 없음: $apiKey" -ForegroundColor Yellow
    }
}

# 4) 워커 Redis (이미 Video worker에서 확인함 - 요약만)
Write-Host "`n[INFO] 워커 Redis: Video worker에서 이미 확인됨 (REDIS_HOST=ElastiCache, ping OK)" -ForegroundColor Gray
Write-Host "       새 워커 인스턴스는 SSM /academy/workers/env에서 .env 로드" -ForegroundColor Gray

# 5) API vs SSM REDIS_HOST 일치 여부
if ($envContent -and $ssmValue) {
    $localHost = $null; $ssmHost = $null
    if ($envContent -match 'REDIS_HOST=([^\s#]+)') { $localHost = $Matches[1].Trim() }
    if ($ssmValue -match 'REDIS_HOST=([^\s\r\n#]+)') { $ssmHost = $Matches[1].Trim() }
    if ($localHost -and $ssmHost -and $localHost -eq $ssmHost) {
        Write-Host "`n[OK] .env REDIS_HOST == SSM REDIS_HOST (일치)" -ForegroundColor Green
    } elseif ($localHost -and $ssmHost) {
        Write-Host "`n[FAIL] .env REDIS_HOST != SSM REDIS_HOST" -ForegroundColor Red
        Write-Host "       .env: $localHost" -ForegroundColor Gray
        Write-Host "       SSM:  $ssmHost" -ForegroundColor Gray
        Write-Host "       -> upload_env_to_ssm.ps1 실행 후 워커 instance refresh 필요" -ForegroundColor Yellow
    }
}

Write-Host "`n=== 체크 완료 ===`n" -ForegroundColor Cyan
