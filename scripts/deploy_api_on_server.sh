#!/bin/bash
# API 서버에서: 정석 배포와 동일 — SSM → /opt/api.env, ECR pull, 재시작 (빌드 없음).
# 사용: bash scripts/deploy_api_on_server.sh
# 호출: api-auto-deploy-remote.ps1 -Action Deploy, 또는 cron(2분마다 main 변경 시).
# 정석 배포(api.ps1 UserData)와 동일한 결과: /opt/api.env + ECR 이미지.

set -e
REGION="${AWS_REGION:-ap-northeast-2}"
SSM_API_ENV="/academy/api/env"
API_ENV_FILE="/opt/api.env"
# 정석 배포와 동일 (params.yaml ecr.apiRepo + accountId, region)
ECR_URI="${ECR_API_IMAGE_URI:-809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest}"
ECR_HOST="${ECR_URI%%/*}"

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

# 5) 미사용 이미지 정리 (디스크 여유)
docker image prune -f
