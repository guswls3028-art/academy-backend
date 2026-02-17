# ==============================================================================
# AI Worker CPU - academy-base 기반 (500 배포 + 10K 확장 대비)
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드·scripts는 여기서 복사.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

ENV WORKER_TYPE=CPU

USER root
# 앱 코드 (Base에 두지 않음)
COPY src ./src
COPY academy ./academy
COPY apps ./apps
COPY libs ./libs
COPY manage.py ./
# AI Worker만 scripts 필요 (gate10 등)
COPY scripts ./scripts
RUN chown -R appuser:appuser /app

# AI Worker CPU 전용 시스템 의존성 (OCR, 비디오)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-kor \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

USER appuser
# AI 전용 Python 의존성
COPY requirements/common.txt requirements/worker-ai-common.txt requirements/worker-ai-cpu.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-ai-cpu.txt

COPY requirements/worker-ai-excel.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-ai-excel.txt

# Excel 파싱 워커 의존: apps.infrastructure.storage.r2_adapter (apps SSOT)
RUN python -c "from apps.infrastructure.storage.r2_adapter import R2ObjectStorageAdapter" || (echo "ERROR: R2ObjectStorageAdapter not importable" && exit 1)
# AI 파이프라인 의존: apps.worker.ai_worker.storage.downloader
RUN python -c "from apps.worker.ai_worker.storage.downloader import download_to_tmp" || (echo "ERROR: download_to_tmp not importable" && exit 1)

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

CMD ["python", "-m", "apps.worker.ai_worker.sqs_main_cpu"]
