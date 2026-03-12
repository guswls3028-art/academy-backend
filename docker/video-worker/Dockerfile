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

# /tmp transcode dirs (ECS_AL2023 default user may not be root)
RUN mkdir -p /tmp/video-worker /tmp/video-worker-locks && chown -R appuser:appuser /tmp/video-worker /tmp/video-worker-locks

USER appuser
# Shared requirements (API + Worker same file)
COPY requirements/requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt
# 헬스체크
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Video Worker 실행
# ENTRYPOINT: SSM /academy/workers/env fetch 후 워커 실행
# Daemon mode (기본): 상주 프로세스로 DB 폴링하여 30분 이하 영상 처리
# Batch mode: ["python", "-m", "apps.worker.video_worker.batch_main", "Ref::job_id"]
ENTRYPOINT ["python", "-m", "apps.worker.video_worker.batch_entrypoint"]
CMD ["python", "-m", "apps.worker.video_worker.daemon_main"]
