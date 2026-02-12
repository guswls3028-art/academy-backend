#!/bin/bash
# ==============================================================================
# Docker 이미지 빌드 스크립트
# ==============================================================================
# 50명 원장 확장 대비: 멀티 스테이지 빌드로 이미지 크기 최소화
# ==============================================================================

set -e

echo "🔨 Building Docker images..."

# 공통 베이스 이미지 빌드
echo "📦 Building base image..."
docker build -f docker/Dockerfile.base -t academy-base:latest .

# API 서버 이미지 빌드
echo "📦 Building API server image..."
docker build -f docker/api/Dockerfile -t academy-api:latest .

# AI Worker 이미지 빌드
echo "📦 Building AI worker image..."
docker build -f docker/ai-worker/Dockerfile -t academy-ai-worker:latest .

# Video Worker 이미지 빌드
echo "📦 Building Video worker image..."
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .

echo "✅ All images built successfully!"
echo ""
echo "📋 Available images:"
docker images | grep academy
