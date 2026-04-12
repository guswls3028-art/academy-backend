#!/bin/bash
# ==============================================================================
# Docker 이미지 빌드 스크립트
# ==============================================================================
# 사용법: ./docker/build.sh          (전체 빌드)
#         ./docker/build.sh base     (베이스만)
#         ./docker/build.sh api      (API만)
# ==============================================================================

set -e

cd "$(dirname "$0")/.."

build_base() {
    echo "[1/5] academy-base..."
    docker build -f docker/Dockerfile.base -t academy-base:latest .
}

build_api() {
    echo "[2/5] academy-api..."
    docker build -f docker/api/Dockerfile -t academy-api:latest .
}

build_video() {
    echo "[3/5] academy-video-worker..."
    docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
}

build_ai_cpu() {
    echo "[4/5] academy-ai-worker-cpu..."
    docker build -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest .
}

build_messaging() {
    echo "[5/5] academy-messaging-worker..."
    docker build -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .
}

if [ -n "$1" ]; then
    case "$1" in
        base)       build_base ;;
        api)        build_base && build_api ;;
        video)      build_base && build_video ;;
        ai-cpu)     build_base && build_ai_cpu ;;
        messaging)  build_base && build_messaging ;;
        *)          echo "Unknown target: $1"; echo "Available: base, api, video, ai-cpu, messaging"; exit 1 ;;
    esac
else
    build_base
    build_api
    build_video
    build_ai_cpu
    build_messaging
fi

echo ""
echo "Done. Images:"
docker images | grep academy
