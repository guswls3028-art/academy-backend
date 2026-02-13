# ==============================================================================
# Messaging Worker Dockerfile
# ==============================================================================
# SQS + Solapi SMS/알림톡 (Django ORM 사용)
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

# Messaging Worker 전용 Python 의존성 (-r ./common.txt만, api.txt 미사용)
COPY requirements/common.txt requirements/worker-messaging.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-messaging.txt

# 헬스체크
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Messaging Worker 실행
CMD ["python", "-m", "apps.worker.messaging_worker.sqs_main"]
