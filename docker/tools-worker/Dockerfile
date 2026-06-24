# ==============================================================================
# Tools Worker Dockerfile
# ==============================================================================
# Lightweight worker for deterministic document conversion, bounded OCR, and
# spreadsheet tasks. Keep this separate from AI worker model caches and ML deps.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

ENV WORKER_TYPE=TOOLS

USER root
# Problem Studio 원본 이관은 스캔 PDF/이미지의 제한적 OCR까지 tools worker에서
# 끝내야 API 60초 경계를 피하면서도 산출물 품질이 API와 일치한다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/* \
    && (tesseract --version > /dev/null 2>&1 || (echo "tesseract not in PATH" && exit 1))
COPY --chown=appuser:appuser academy ./academy
COPY --chown=appuser:appuser apps ./apps
COPY --chown=appuser:appuser libs ./libs
COPY --chown=appuser:appuser manage.py ./

USER appuser

COPY requirements/constraints.txt requirements/common.txt requirements/worker-tools.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-tools.txt

RUN python -c "from academy.application.use_cases.tools.worker_dispatcher import handle_tools_job; from academy.application.services.excel_parsing_service import ExcelParsingService; print(handle_tools_job.__name__, ExcelParsingService.__name__)"

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD kill -0 1 2>/dev/null || exit 1

CMD ["python", "-m", "apps.worker.tools_worker.sqs_main"]
