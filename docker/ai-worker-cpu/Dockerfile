# ==============================================================================
# AI Worker CPU - 경량 최적화
# ==============================================================================
# python:3.11-slim 기반, torch CPU only, CUDA 미포함
# 예상 크기: 700MB~1GB
# ==============================================================================

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV PATH=/root/.local/bin:$PATH
ENV WORKER_TYPE=CPU

# 시스템 의존성 (OCR, 비디오 - 최소화)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-kor \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 공통 + AI CPU 의존성 (torch CPU, onnxruntime CPU)
COPY requirements/common.txt requirements/worker-ai-common.txt requirements/worker-ai-cpu.txt ./requirements/
RUN pip install --user --no-cache-dir -r requirements/worker-ai-cpu.txt

# 애플리케이션 코드 (Hexagonal: src + apps + libs)
COPY src ./src
COPY apps ./apps
COPY libs ./libs
COPY scripts ./scripts
COPY manage.py ./

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

CMD ["python", "-m", "apps.worker.ai_worker.sqs_main_cpu"]
