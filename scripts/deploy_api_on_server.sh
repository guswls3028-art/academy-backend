#!/bin/bash
# API 서버 안에서: SSM /academy/api/env → .env, git pull → build → 재시작
# 사용: cd /home/ec2-user/academy && bash scripts/deploy_api_on_server.sh
#
# 매 배포 시 academy-api:latest 로 다시 빌드하면, 이전 이미지는 태그가 빠져
# dangling(<none>:<none>)으로 쌓임. 정리하지 않으면 디스크 100%로 No space left 발생.

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/home/ec2-user/.env}"
REGION="${AWS_REGION:-ap-northeast-2}"
SSM_API_ENV="/academy/api/env"

# 1) Fetch API env from SSM (SSOT for API server)
if aws ssm get-parameter --name "$SSM_API_ENV" --with-decryption --query Parameter.Value --output text --region "$REGION" 2>/dev/null | sed 's/\t/\n/g' | grep -v '^$' > "$ENV_FILE"; then
  echo "OK. SSM $SSM_API_ENV -> $ENV_FILE"
else
  echo "ERROR: Failed to get SSM $SSM_API_ENV. Create parameter or check IAM." >&2
  exit 1
fi

# 2) Guard: required env for upload_complete / API (apps/api/config/settings/base.py)
# DB_NAME/DB_USER required for Django DATABASES; without them settings.DATABASES has null -> 500
REQUIRED_KEYS="DB_HOST DB_NAME DB_USER R2_ACCESS_KEY R2_SECRET_KEY R2_ENDPOINT REDIS_HOST"
MISSING=""
for k in $REQUIRED_KEYS; do
  line=$(grep -E "^${k}=" "$ENV_FILE" 2>/dev/null | head -1)
  val="${line#*=}"
  if [ -z "$val" ]; then
    MISSING="${MISSING}${k} "
  fi
done
if [ -n "$MISSING" ]; then
  echo "ERROR: Required env missing in $ENV_FILE: $MISSING" >&2
  exit 1
fi

cd "$REPO_DIR"
git fetch origin
git reset --hard origin/main
git pull origin main

# 사용 안 하는(태그 없는) 이미지 삭제 — 현재 사용 중인 academy-api:latest / academy-base:latest 는 유지
docker image prune -f

docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/api/Dockerfile -t academy-api:latest .

# 빌드 후 다시 한 번 dangling 정리 (multi-stage 등으로 생긴 중간 이미지)
docker image prune -f

(docker stop academy-api 2>/dev/null; docker rm academy-api 2>/dev/null; true)
docker run -d --name academy-api --restart unless-stopped --env-file "$ENV_FILE" -p 8000:8000 academy-api:latest
echo "OK — API 재시작 완료"
