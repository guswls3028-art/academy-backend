# ==============================================================================
# Video Worker Dockerfile
# ==============================================================================
# 베이스 이미지 사용 + Video Worker 전용 의존성
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} as base

# Video Worker 전용 시스템 의존성 (비디오 처리)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Video Worker 전용 Python 의존성
COPY requirements/worker-video.txt ./requirements/worker-video.txt
RUN pip install --user --no-cache-dir -r requirements/worker-video.txt

# 헬스체크
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Video Worker 실행
# 환경 변수:
# - EC2_IDLE_STOP_THRESHOLD: 자동 종료 임계값 (기본값: 5)
CMD ["python", "-m", "apps.worker.video_worker.sqs_main"]
