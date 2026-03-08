#!/bin/bash
# API 서버에서: 정석 배포와 동일 — SSM → /opt/api.env, ECR pull, 재시작 (빌드 없음).
# DISABLED IN PRODUCTION: In-place container replace is disabled. Use CI/CD formal deploy only.
set -e
echo "Rapid deploy is disabled in production. Use CI/CD formal deploy (GitHub Actions → ECR push → ASG instance refresh)." >&2
exit 1

REGION="${AWS_REGION:-ap-northeast-2}"
SSM_API_ENV="/academy/api/env"
API_ENV_FILE="/opt/api.env"
# 정석 배포와 동일 (params.yaml ecr.apiRepo + accountId, region)
ECR_URI="${ECR_API_IMAGE_URI:-809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest}"
ECR_HOST="${ECR_URI%%/*}"
# Rapid Deploy: 마지막 배포 정보 기록 (최근 반영 버전 확인용)
LAST_DEPLOY_FILE="${LAST_DEPLOY_FILE:-/home/ec2-user/.academy-rapid-deploy-last}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/healthz}"

# 1) SSM → env 파일 (정석과 동일: /opt/api.env)
RAW_VALUE=$(aws ssm get-parameter --name "$SSM_API_ENV" --with-decryption --query Parameter.Value --output text --region "$REGION" 2>/dev/null) || true
if [ -z "$RAW_VALUE" ]; then
  echo "ERROR: Failed to get SSM $SSM_API_ENV. Create parameter or check IAM." >&2
  exit 1
fi

write_env_from_json() {
  printf '%s' "$1" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for k, v in d.items():
        if v is not None:
            print(k + '=' + str(v))
except (json.JSONDecodeError, Exception):
    sys.exit(1)
" 2>/dev/null
}

TMP_ENV=$(mktemp)
trap 'rm -f "$TMP_ENV"' EXIT

if write_env_from_json "$RAW_VALUE" > "$TMP_ENV" && [ -s "$TMP_ENV" ]; then
  : # OK
else
  DECODED=$(echo "$RAW_VALUE" | base64 -d 2>/dev/null) || true
  if [ -n "$DECODED" ] && write_env_from_json "$DECODED" > "$TMP_ENV" && [ -s "$TMP_ENV" ]; then
    : # OK
  else
    echo "$RAW_VALUE" | sed 's/\t/\n/g' | grep -v '^$' > "$TMP_ENV"
    if [ ! -s "$TMP_ENV" ]; then
      echo "ERROR: SSM value is not valid JSON and produced empty .env." >&2
      exit 1
    fi
  fi
fi

# /opt/api.env 에 기록 (정석 배포와 동일 경로). root 또는 sudo 필요
if [ -w /opt ] 2>/dev/null; then
  mkdir -p /opt
  cp "$TMP_ENV" "$API_ENV_FILE"
else
  sudo mkdir -p /opt
  sudo cp "$TMP_ENV" "$API_ENV_FILE"
fi
echo "OK. SSM $SSM_API_ENV -> $API_ENV_FILE"

# 2) 필수 키 검사
REQUIRED_KEYS="DB_HOST DB_NAME DB_USER R2_ACCESS_KEY R2_SECRET_KEY R2_ENDPOINT REDIS_HOST"
MISSING=""
for k in $REQUIRED_KEYS; do
  line=$(grep -E "^${k}=" "$API_ENV_FILE" 2>/dev/null | head -1)
  val="${line#*=}"
  if [ -z "$val" ]; then
    MISSING="${MISSING}${k} "
  fi
done
if [ -n "$MISSING" ]; then
  echo "ERROR: Required env missing in $API_ENV_FILE: $MISSING" >&2
  exit 1
fi

# 3) ECR 로그인 및 pull (정석과 동일 이미지)
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_HOST"
docker pull "$ECR_URI"

# 4) 기존 컨테이너 정리 후 재시작 (정석 UserData와 동일)
docker stop academy-api 2>/dev/null || true
docker rm academy-api 2>/dev/null || true
docker run -d --restart unless-stopped --name academy-api -p 8000:8000 --env-file "$API_ENV_FILE" "$ECR_URI"
echo "OK — API 재시작 완료 (ECR 이미지 + $API_ENV_FILE)"

# 5) 최소 health check (선택): 재시작 후 /healthz 200 확인
sleep 3
if curl -sf --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then
  echo "OK — health check $HEALTH_URL 200"
else
  echo "WARN — health check $HEALTH_URL 실패 또는 타임아웃 (컨테이너는 기동됨, 로그 확인 권장)" >&2
fi

# 6) 마지막 배포 정보 기록 (최근 반영 버전 확인용)
IMG_ID=$(docker inspect academy-api --format '{{.Id}}' 2>/dev/null || echo "unknown")
TS=$(date -Iseconds)
echo "deployed_at=$TS" > "$LAST_DEPLOY_FILE"
echo "image=$ECR_URI" >> "$LAST_DEPLOY_FILE"
echo "container_id=$IMG_ID" >> "$LAST_DEPLOY_FILE"
echo "OK — last deploy info written to $LAST_DEPLOY_FILE"

# 7) 미사용 이미지 정리 (디스크 여유)
docker image prune -f
