#!/bin/bash
# ==============================================================================
# 빌드 서버(EC2 arm64)에서 Docker 이미지 풀빌드 + ECR 푸시
# build_and_push_ecr.ps1 와 동일 동작. Redis backlog 등 Worker 코드 반영 시 이 스크립트 실행 후
# 로컬에서: .\scripts\full_redeploy.ps1 -WorkersViaASG -SkipBuild
#
# 사용: repo 루트에서 ./scripts/build_and_push_ecr_on_ec2.sh
# 옵션:
#   NO_CACHE=1          docker build --no-cache
#   VIDEO_WORKER_ONLY=1 base + academy-video-worker 만 빌드/푸시 (로컬 Docker 불필요)
# ==============================================================================
set -e
cd "$(dirname "$0")/.."
REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
NO_CACHE="${NO_CACHE:-}"
DOCKER_EXTRA="${NO_CACHE:+--no-cache}"
VIDEO_WORKER_ONLY="${VIDEO_WORKER_ONLY:-}"

echo "ECR Registry: $ECR"
echo "Region: $REGION"
[ -n "$VIDEO_WORKER_ONLY" ] && echo "VIDEO_WORKER_ONLY=1 (base + video-worker only)"
echo ""

echo "[1/5] academy-base..."
docker build $DOCKER_EXTRA -f docker/Dockerfile.base -t academy-base:latest .

if [ -n "$VIDEO_WORKER_ONLY" ]; then
  echo "[2/5] academy-video-worker (skip api/messaging/ai)..."
  docker build $DOCKER_EXTRA -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
  docker tag academy-video-worker:latest "${ECR}/academy-video-worker:latest"
  echo "ECR login..."
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR"
  aws ecr create-repository --repository-name academy-video-worker --region "$REGION" 2>/dev/null || true
  echo "ECR push academy-video-worker..."
  docker push "${ECR}/academy-video-worker:latest"
  echo "Done (VideoWorker only). 로컬에서: .\\scripts\\full_redeploy.ps1 -WorkersViaASG -SkipBuild -DeployTarget video"
  exit 0
fi

echo "[2/5] academy-api..."
docker build $DOCKER_EXTRA -f docker/api/Dockerfile -t academy-api:latest .
docker tag academy-api:latest "${ECR}/academy-api:latest"

echo "[3/5] academy-messaging-worker..."
docker build $DOCKER_EXTRA -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .
docker tag academy-messaging-worker:latest "${ECR}/academy-messaging-worker:latest"

echo "[4/5] academy-video-worker..."
docker build $DOCKER_EXTRA -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
docker tag academy-video-worker:latest "${ECR}/academy-video-worker:latest"

echo "[5/5] academy-ai-worker-cpu..."
docker build $DOCKER_EXTRA -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest .
docker tag academy-ai-worker-cpu:latest "${ECR}/academy-ai-worker-cpu:latest"

echo "ECR login..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR"

for repo in academy-api academy-messaging-worker academy-video-worker academy-ai-worker-cpu; do
  aws ecr create-repository --repository-name "$repo" --region "$REGION" 2>/dev/null || true
done

echo "ECR push..."
docker push "${ECR}/academy-api:latest"
docker push "${ECR}/academy-messaging-worker:latest"
docker push "${ECR}/academy-video-worker:latest"
docker push "${ECR}/academy-ai-worker-cpu:latest"

echo "Done. 이제 로컬에서: .\\scripts\\full_redeploy.ps1 -WorkersViaASG -SkipBuild"
