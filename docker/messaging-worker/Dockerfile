# ==============================================================================
# Messaging Worker Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서 복사.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

USER appuser

# Messaging Worker 전용 Python 의존성 (-r ./common.txt만, api.txt 미사용)
COPY --chown=appuser:appuser requirements/constraints.txt requirements/common.txt requirements/worker-messaging.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-messaging.txt

# 코드 변경은 entrypoint 검증만 다시 실행하고 Python 의존성 캐시는 유지한다.
COPY --chown=appuser:appuser academy ./academy
COPY --chown=appuser:appuser apps ./apps
COPY --chown=appuser:appuser libs ./libs
COPY --chown=appuser:appuser manage.py ./

# Candidate contract: the exact worker entrypoint and its queue/config imports
# must load before an immutable image can be published.
RUN python -c "import apps.worker.messaging_worker.sqs_main; print('messaging entrypoint import OK')"

# 헬스체크
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 2>/dev/null || exit 1

# Messaging Worker 실행
CMD ["python", "-m", "apps.worker.messaging_worker.sqs_main"]
