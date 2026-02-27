#!/bin/bash
# API 서버 **내부**에서 실행. (SSH 접속 후)
# 사용: bash api_server_git_pull.sh
# .env 위치가 다르면 아래 REPO_DIR, ENV_FILE 수정 후 실행.

set -e
REPO_DIR="${REPO_DIR:-/home/ec2-user/academy}"
ENV_FILE="${ENV_FILE:-/home/ec2-user/.env}"

if [ ! -d "$REPO_DIR" ]; then
  echo "ERROR: Repo not found at $REPO_DIR. Clone first: git clone <url> $REPO_DIR"
  exit 1
fi
cd "$REPO_DIR"
git fetch origin
git reset --hard origin/main
git pull origin main
docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/api/Dockerfile -t academy-api:latest .
(docker stop academy-api 2>/dev/null; docker rm academy-api 2>/dev/null; true)
docker run -d --name academy-api --restart unless-stopped --env-file "$ENV_FILE" -p 8000:8000 academy-api:latest
echo DONE
