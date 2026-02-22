# ==============================================================================
# Video Worker Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서 복사.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

USER root
# 앱 코드 (Base에 두지 않음)
COPY src ./src
COPY academy ./academy
COPY apps ./apps
COPY libs ./libs
COPY manage.py ./
RUN chown -R appuser:appuser /app

# Video Worker 전용 시스템 의존성 (비디오 처리)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

USER appuser
# Video Worker 전용 Python 의존성 (-r ./common.txt만, api.txt 미사용)
COPY requirements/common.txt requirements/worker-video.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-video.txt
# 헬스체크
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Video Worker 실행 (AWS Batch)
# ENTRYPOINT: SSM /academy/workers/env fetch 후 batch_main 실행
# Batch command: ["python", "-m", "apps.worker.video_worker.batch_main", "Ref::job_id"]
ENTRYPOINT ["python", "-m", "apps.worker.video_worker.batch_entrypoint"]
CMD ["python", "-m", "apps.worker.video_worker.batch_main"]
