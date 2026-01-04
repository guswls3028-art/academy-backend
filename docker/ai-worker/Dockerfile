# academy-backend/docker/ai-worker/Dockerfile

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

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
COPY docker/ai-worker/keys/google-vision.json /keys/google-vision.json

ENV GOOGLE_APPLICATION_CREDENTIALS=/keys/google-vision.json

# ✅ Django / Celery ❌
# ✅ 순수 AI Worker 실행
CMD ["python", "-m", "apps.worker.run"]
