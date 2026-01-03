# academy/docker/ai-worker/Dockerfile

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    ffmpeg \
    libgl1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements/worker-ai.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY apps ./apps
COPY libs ./libs

ENV PYTHONPATH=/app

# ✅ AI Worker 전용 Celery
CMD ["celery", "-A", "apps.shared.tasks", "worker", "-Q", "ai", "-l", "info"]
