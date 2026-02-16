#!/bin/bash
# API 서버 안에서: git pull → build → 재시작
# 사용: cd /home/ec2-user/academy && bash scripts/deploy_api_on_server.sh

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/home/ec2-user/.env}"

cd "$REPO_DIR"
git fetch origin
git reset --hard origin/main
git pull origin main
docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/api/Dockerfile -t academy-api:latest .
(docker stop academy-api 2>/dev/null; docker rm academy-api 2>/dev/null; true)
docker run -d --name academy-api --restart unless-stopped --env-file "$ENV_FILE" -p 8000:8000 academy-api:latest
echo "OK — API 재시작 완료"
