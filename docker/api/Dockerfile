# ==============================================================================
# API 서버 Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서만 복사 (헥사고날·레이어 분리).
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

# 시스템·Python 의존성을 앱 소스보다 먼저 확정한다. 앱 코드 변경이
# apt/pip 레이어를 무효화하지 않아야 일반 릴리스가 캐시를 재사용한다.
USER root
# Problem Studio transfer packages use bounded local OCR for scanned pages.
# upload_complete source probing is fail-open; authoritative validation/transcoding
# remains isolated in the AWS Batch Video worker, so the API does not ship FFmpeg.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/* \
    && (tesseract --version > /dev/null 2>&1 || (echo "tesseract not in PATH" && exit 1))
USER appuser

# Shared requirements와 API-only deps를 한 번에 해석한다. api.txt는
# common.txt를, 두 파일은 constraints.txt를 참조하므로 모두 먼저 복사한다.
COPY --chown=appuser:appuser requirements/constraints.txt requirements/requirements.txt requirements/api.txt requirements/common.txt ./requirements/
RUN pip install --user --no-cache-dir \
    -r requirements/requirements.txt \
    -r requirements/api.txt

# Note: Phase 2 cross-encoder ML deps (torch + sentence-transformers + 모델)는
# 직접 평가에서 한국어 시험 문제에 부적합 확인 → 컨테이너에서 제거.
# 코드(apps/domains/matchup/reranker.py) + requirements/api-extras.txt는 보존.
# 향후 활성화 시 아래 두 줄 복원 + EBS 확장(8GB→20GB) 권장:
#   COPY requirements/api-extras.txt ./requirements/
#   RUN pip install --user --no-cache-dir -r requirements/api-extras.txt
#   RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('<모델명>', max_length=512)"

# 앱 코드는 가장 자주 바뀌는 마지막 캐시 경계다. --chown으로 별도
# metadata-only chown 레이어가 전체 소스를 다시 기록하지 않게 한다.
COPY --chown=appuser:appuser academy ./academy
COPY --chown=appuser:appuser apps ./apps
COPY --chown=appuser:appuser libs ./libs
COPY --chown=appuser:appuser manage.py ./
COPY --chown=appuser:appuser scripts ./scripts

# 헬스체크: 컨테이너/ALB 생존성은 DB readiness와 분리한다.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/healthz', timeout=5)" || exit 1

# 포트 노출
EXPOSE 8000

# Gevent worker로 실행 (50명 원장 확장 대비)
# Migration은 CI/CD run-migrations job에서 실행됨 — 컨테이너 시작 시간 최소화
# 환경 변수로 오버라이드 가능:
# - GUNICORN_WORKERS: 워커 수 (기본값: 4)
# - GUNICORN_WORKER_CONNECTIONS: 연결 수 (기본값: 1000)
CMD ["sh", "-c", \
    "exec gunicorn \
    --bind 0.0.0.0:8000 \
    --workers ${GUNICORN_WORKERS:-4} \
    --worker-class gevent \
    --worker-connections ${GUNICORN_WORKER_CONNECTIONS:-1000} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    apps.api.config.wsgi:application"]
