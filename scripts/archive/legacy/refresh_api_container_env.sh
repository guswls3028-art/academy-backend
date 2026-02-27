#!/bin/bash
# API 서버(EC2)에서 실행. .env 수정 후 컨테이너를 재생성해서 새 env 반영.
# docker restart 는 생성 시점 env만 유지하므로, 반드시 stop+rm+run 해야 함.
set -e
ENV_FILE="${1:-/home/ec2-user/.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found: $ENV_FILE"
  exit 1
fi
IMG=$(docker inspect academy-api --format '{{.Config.Image}}' 2>/dev/null || true)
if [ -z "$IMG" ]; then
  IMG=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep academy-api | head -1)
fi
if [ -z "$IMG" ]; then
  echo "ERROR: academy-api image not found. Pull first or set IMG."
  exit 1
fi
echo "Image: $IMG"
echo "Env: $ENV_FILE"
docker stop academy-api 2>/dev/null || true
docker rm academy-api 2>/dev/null || true
docker run -d --name academy-api --restart unless-stopped --env-file "$ENV_FILE" -p 8000:8000 "$IMG"
echo "OK. academy-api recreated with current .env"
