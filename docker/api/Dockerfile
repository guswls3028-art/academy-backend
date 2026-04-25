# ==============================================================================
# API 서버 Dockerfile
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드는 여기서만 복사 (헥사고날·레이어 분리).
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

# 앱 코드 (Base에 두지 않음 → 코드 변경 시 base 재빌드 불필요)
USER root
# upload_complete ffprobe 검증용 (ffmpeg 패키지에 ffprobe 포함)
# 참고: Amazon Linux 등 yum 기반 이미지 사용 시: RUN yum install -y ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && (ffprobe -version > /dev/null 2>&1 || (echo "ffprobe not in PATH" && exit 1))
COPY academy ./academy
COPY apps ./apps
COPY libs ./libs
COPY manage.py ./
COPY scripts ./scripts
RUN chown -R appuser:appuser /app
USER appuser

# Shared requirements (API + Worker same file)
COPY requirements/constraints.txt requirements/requirements.txt requirements/api-extras.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/requirements.txt
# API-only ML deps (cross-encoder reranker for matchup similarity)
RUN pip install --user --no-cache-dir -r requirements/api-extras.txt

# Pre-download cross-encoder model so first runtime call is fast.
# Model: BAAI/bge-reranker-base (~280MB, 다국어).
# v2-m3 (~1.1GB)는 t4g.medium 8GB EBS에서 디스크 압박 → base로 다운사이즈.
# 추후 EBS 확장 또는 별도 모델 호스팅 시 v2-m3로 교체 가능.
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface
ENV HF_HOME=/app/.cache/huggingface
RUN mkdir -p /app/.cache/huggingface && \
    python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-base', max_length=512)" && \
    echo "RERANKER_MODEL_CACHED"

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health', timeout=5)" || exit 1

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
