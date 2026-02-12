# ==============================================================================
# API 서버 Dockerfile
# ==============================================================================
# 베이스 이미지 사용 + API 전용 의존성
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} as base

# API 전용 의존성 설치
COPY requirements/api.txt ./requirements/api.txt
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
