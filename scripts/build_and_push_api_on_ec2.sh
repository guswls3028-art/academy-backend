#!/bin/bash
# EC2(빌드 서버)에서 API 이미지 빌드 + ECR 푸시
# 사용: repo 루트에서 ./scripts/build_and_push_api_on_ec2.sh
set -e
cd "$(dirname "$0")/.."
REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "[1/3] 베이스 이미지 (캐시용)..."
docker build -f docker/Dockerfile.base -t academy-base:latest .

echo "[2/3] API 이미지 빌드..."
docker build -f docker/api/Dockerfile -t academy-api:latest .

echo "[3/3] ECR 로그인 + 태그 + 푸시..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR"
docker tag academy-api:latest "${ECR}/academy-api:latest"
docker push "${ECR}/academy-api:latest"

echo "완료. API 서버에서 pull 후 컨테이너 재시작하세요."
