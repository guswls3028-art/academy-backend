# ==============================================================================
# API 서버 Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서만 복사 (헥사고날·레이어 분리).
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

# 앱 코드 (Base에 두지 않음 → 코드 변경 시 base 재빌드 불필요)
USER root
COPY src ./src
COPY src/infrastructure ./src/infrastructure
COPY academy ./academy
COPY apps ./apps
COPY libs ./libs
COPY manage.py ./
COPY scripts ./scripts
RUN chown -R appuser:appuser /app
USER appuser

# API 전용 의존성 설치 (-r common.txt 참조)
COPY requirements/common.txt requirements/api.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/api.txt

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health', timeout=5)" || exit 1

# 포트 노출
EXPOSE 8000

# Gevent worker로 실행 (50명 원장 확장 대비)
# 환경 변수로 오버라이드 가능:
# - GUNICORN_WORKERS: 워커 수 (기본값: 4)
# - GUNICORN_WORKER_CONNECTIONS: 연결 수 (기본값: 1000)
CMD ["sh", "-c", \
    "gunicorn \
    --bind 0.0.0.0:8000 \
    --workers ${GUNICORN_WORKERS:-4} \
    --worker-class gevent \
    --worker-connections ${GUNICORN_WORKER_CONNECTIONS:-1000} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    apps.api.config.wsgi:application"]
