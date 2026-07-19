# ==============================================================================
# Video Worker Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서 복사.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

USER root
# 앱 코드 (Base에 두지 않음)
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
# Video Worker dependency manifest. worker-video.txt includes api.txt ->
# common.txt, so copy the complete include chain and install the worker SSOT.
COPY requirements/constraints.txt requirements/common.txt requirements/api.txt requirements/worker-video.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-video.txt
# 헬스체크
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 2>/dev/null || exit 1

# Video Worker 실행
# ENTRYPOINT: SSM /academy/workers/env fetch 후 batch_main exec.
# AWS Batch는 jobdef containerProperties.command로 항상 ["python","-m","...batch_main","Ref::job_id"]를 명시 → CMD는 fallback일 뿐.
ENTRYPOINT ["python", "-m", "apps.worker.video_worker.batch_entrypoint"]
CMD ["python", "-m", "apps.worker.video_worker.batch_main"]
