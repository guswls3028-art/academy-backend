# ==============================================================================
# AI Worker GPU - 추론 속도 최적화
# ==============================================================================
# nvidia/cuda:runtime 기반, torch+cu121, onnxruntime-gpu
# 예상 크기: 2~3GB
# ==============================================================================

FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV PATH=/root/.local/bin:$PATH
ENV WORKER_TYPE=GPU

# Python 3.11 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    tesseract-ocr \
    tesseract-ocr-kor \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && ln -sf /usr/bin/python3.11 /usr/bin/python \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 공통 + AI GPU 의존성 (torch CUDA, onnxruntime-gpu)
COPY requirements/common.txt requirements/worker-ai-common.txt requirements/worker-ai-gpu.txt ./requirements/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements/worker-ai-gpu.txt

# 애플리케이션 코드
COPY src ./src
COPY apps ./apps
COPY libs ./libs
COPY scripts ./scripts
COPY manage.py ./

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

CMD ["python", "-m", "apps.worker.ai_worker.sqs_main_gpu"]
