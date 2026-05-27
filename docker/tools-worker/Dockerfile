# ==============================================================================
# Tools Worker Dockerfile
# ==============================================================================
# Lightweight worker for deterministic document conversion and spreadsheet
# tasks. Keep this separate from AI worker model caches and OCR/ML deps.
# ==============================================================================

ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

ENV WORKER_TYPE=TOOLS

USER root
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
