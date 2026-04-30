# ==============================================================================
# AI Worker CPU - academy-base 기반 (500 배포 + 10K 확장 대비)
# ==============================================================================
# Base = 런타임·공통 의존성만. 앱 코드·scripts는 여기서 복사.
#
# 레이어 순서 (캐시 최적화):
#   1. system deps (apt) — 거의 변경 없음
#   2. python deps (pip + requirements) — requirements 변경 시에만 재실행
#   3. ML 모델 사전 캐시 (CLIP, MiniLM) — pip 재실행 안 하면 캐시
#   4. 앱 코드 (academy, apps, libs) — 자주 변경, 캐시 무효화 영향 최소
#   5. import 검증 (academy/apps 의존)
#
# 사고(2026-04-29): COPY academy 가 모델 캐시 위에 있어 academy/ 변경 시 30분
# 빌드. 본 순서로 재배치하여 코드 변경 시 ~5분 빌드, requirements 변경 시에만
# CLIP/MiniLM 재다운로드.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

ENV WORKER_TYPE=CPU

# AI Worker CPU 전용 시스템 의존성 (OCR, 비디오) — 코드 변경과 무관
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-kor \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

USER appuser

# AI 전용 Python 의존성 (단일 RUN으로 레이어 최소화). requirements 변경 시에만 재설치.
COPY requirements/constraints.txt requirements/common.txt requirements/worker-ai-common.txt requirements/worker-ai-cpu.txt requirements/worker-ai-excel.txt requirements/worker-ai-tools.txt ./requirements/
RUN pip install --user --no-cache-dir \
    -r requirements/worker-ai-cpu.txt \
    -r requirements/worker-ai-excel.txt \
    -r requirements/worker-ai-tools.txt

# CLIP-ViT-B-32 + paraphrase-multilingual-MiniLM-L12-v2 가중치 사전 캐시.
# 사고(2026-04-29 1차): 워커 부팅 후 첫 잡 처리 시 huggingface에서 600MB 다운로드 →
# 진행률 85%에서 5~15분 stuck. 인스턴스 5대 동시 다운로드로 더 심함.
# 이미지 빌드 시 한 번 받아두면 부팅 즉시 polling 가능, 매 retry/배포마다 즉시 효과.
# 코드 변경(academy/apps)으로 invalidate되지 않도록 COPY 위에 배치 (2026-04-29 2차).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2'); SentenceTransformer('clip-ViT-B-32'); print('CLIP+MiniLM cached OK')" \
    || (echo "ERROR: model pre-cache failed" && exit 1)

# 앱 코드 (Base에 두지 않음) — 자주 변경되는 레이어를 가장 아래로
USER root
COPY --chown=appuser:appuser academy ./academy
COPY --chown=appuser:appuser apps ./apps
COPY --chown=appuser:appuser libs ./libs
COPY --chown=appuser:appuser manage.py ./
# AI Worker만 scripts 필요 (gate10 등)
COPY --chown=appuser:appuser scripts ./scripts

USER appuser

# Excel 파싱 워커 의존: apps.infrastructure.storage.r2_adapter (apps SSOT)
RUN python -c "from apps.infrastructure.storage.r2_adapter import R2ObjectStorageAdapter" || (echo "ERROR: R2ObjectStorageAdapter not importable" && exit 1)
# AI 파이프라인 의존: academy.adapters.ai.storage.downloader
RUN python -c "from academy.adapters.ai.storage.downloader import download_to_tmp" || (echo "ERROR: download_to_tmp not importable" && exit 1)

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 2>/dev/null || exit 1

CMD ["python", "-m", "apps.worker.ai_worker.sqs_main_cpu"]
