# docker/ai-worker/Dockerfile

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

# ✅ requirements 폴더 전체 복사 (핵심)
COPY requirements ./requirements

# ✅ worker-ai.txt 기준으로 설치
RUN pip install --no-cache-dir -r requirements/worker-ai.txt

COPY apps ./apps
COPY libs ./libs

# ✅ Google Vision Key
COPY docker/ai-worker/keys/google-vision.json /keys/google-vision.json
ENV GOOGLE_APPLICATION_CREDENTIALS=/keys/google-vision.json

CMD ["python", "-m", "apps.worker.run"]
