# ==============================================================================
# Video Worker Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서 복사.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

USER root
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
COPY --chown=appuser:appuser requirements/constraints.txt requirements/requirements.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/requirements.txt

# 자주 바뀌는 앱 소스는 apt/pip 뒤에 둬 의존성 캐시를 보존한다.
COPY --chown=appuser:appuser academy ./academy
COPY --chown=appuser:appuser apps ./apps
COPY --chown=appuser:appuser libs ./libs
COPY --chown=appuser:appuser manage.py ./
# Batch must remain the only video entry boundary; validate the SSM bootstrap
# module without starting a job or touching production resources.
RUN python -c "from apps.worker.video_worker.batch_entrypoint import REQUIRED_KEYS, load_env_from_ssm_json; assert 'DJANGO_SETTINGS_MODULE' in REQUIRED_KEYS; print('video Batch entrypoint import OK')"
# 헬스체크
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 2>/dev/null || exit 1

# Video Worker 실행
# ENTRYPOINT: SSM /academy/workers/env fetch 후 batch_main exec.
# AWS Batch는 jobdef containerProperties.command로 항상 ["python","-m","...batch_main","Ref::job_id"]를 명시 → CMD는 fallback일 뿐.
ENTRYPOINT ["python", "-m", "apps.worker.video_worker.batch_entrypoint"]
CMD ["python", "-m", "apps.worker.video_worker.batch_main"]
