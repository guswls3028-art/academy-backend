# ==============================================================================
# Messaging Worker Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서 복사.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

USER root
COPY academy ./academy
COPY apps ./apps
COPY libs ./libs
COPY manage.py ./
RUN chown -R appuser:appuser /app
USER appuser

# Messaging Worker 전용 Python 의존성 (-r ./common.txt만, api.txt 미사용)
COPY requirements/constraints.txt requirements/common.txt requirements/worker-messaging.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-messaging.txt

# 헬스체크
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 2>/dev/null || exit 1

# Messaging Worker 실행
CMD ["python", "-m", "apps.worker.messaging_worker.sqs_main"]
