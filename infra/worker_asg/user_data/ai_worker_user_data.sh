#!/bin/bash
# ASG AI Worker: Docker + ECR pull + run academy-ai-worker-cpu
# EC2_IDLE_STOP_THRESHOLD=0 → self-stop 비활성화 (ASG가 scale-in으로 종료)
# yum update 생략: 기동 시간 단축·상태검사 통과 안정화 (켜졌다 꺼짐 반복 방지)
set -e
yum install -y docker
systemctl start docker && systemctl enable docker

ENV_FILE="/opt/academy/.env"
SECRETS_DIR="/opt/academy/secrets"
GOOGLE_JSON="$SECRETS_DIR/google-vision.json"
mkdir -p "$SECRETS_DIR"
aws ssm get-parameter --name /academy/workers/env --with-decryption --query Parameter.Value --output text --region ap-northeast-2 > "$ENV_FILE" 2>/dev/null || true

# Google Vision OCR credentials (optional)
aws ssm get-parameter --name /academy/google-vision-credentials --with-decryption --query Parameter.Value --output text --region ap-northeast-2 > "$GOOGLE_JSON" 2>/dev/null && chmod 600 "$GOOGLE_JSON" || true

ECR="{{ECR_REGISTRY}}"
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin "$ECR"
docker pull "$ECR/academy-ai-worker-cpu:latest"
docker stop academy-ai-worker-cpu 2>/dev/null || true
docker rm academy-ai-worker-cpu 2>/dev/null || true

# docker run 재시도 (실패 시 10초 후 최대 3회)
for i in 1 2 3; do
  docker rm -f academy-ai-worker-cpu 2>/dev/null || true
  if docker run -d --name academy-ai-worker-cpu --restart unless-stopped \
    --env-file "$ENV_FILE" \
    -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker \
    -e EC2_IDLE_STOP_THRESHOLD=0 \
    "$ECR/academy-ai-worker-cpu:latest"; then
    break
  fi
  echo "docker run attempt $i failed, retrying in 10s..."
  sleep 10
done

# 결과 확인 (cloud-init-output.log에 남음, 디버깅용)
docker ps -a
echo "AI worker user data done"
