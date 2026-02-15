# ==============================================================================
# AI Worker Dockerfile
# ==============================================================================
# 베이스 이미지(Dockerfile.base) 사용. src, apps, libs는 베이스에서 상속.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

# apt는 root 필요 (설치 후 pip은 appuser로)
USER root
# AI Worker 전용 시스템 의존성 (OCR, 이미지 처리)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-kor \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

USER appuser
# AI Worker 전용 Python 의존성 (-r ./common.txt만, api.txt 미사용)
COPY requirements/common.txt requirements/worker-ai-common.txt requirements/worker-ai-cpu.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-ai-cpu.txt

# 헬스체크
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Worker 모드에 따라 다른 진입점 실행
# 환경 변수:
# - AI_WORKER_MODE: cpu (기본값) 또는 gpu
# - EC2_IDLE_STOP_THRESHOLD: 자동 종료 임계값 (기본값: 5)
CMD ["sh", "-c", \
    "if [ \"$AI_WORKER_MODE\" = \"gpu\" ]; then \
        python -m apps.worker.ai_worker.sqs_main_gpu; \
    else \
        python -m apps.worker.ai_worker.sqs_main_cpu; \
    fi"]
