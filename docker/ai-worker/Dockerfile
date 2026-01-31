FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# ✅ 운영 기준: Worker → API 는 무조건 VPC 내부 통신
# (Cloudflare / HTTPS / 외부 LB 절대 통과 안 함)
ENV API_BASE_URL=http://172.31.32.253:8000

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    ffmpeg \
    libgl1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pip 안정화
RUN pip install --upgrade pip

# 🔥 캐시 최적화 포인트
COPY requirements/common.txt ./requirements/common.txt
COPY requirements/worker-ai.txt ./requirements/worker-ai.txt
RUN pip install --no-cache-dir -r requirements/worker-ai.txt

# 코드
COPY apps ./apps
COPY libs ./libs

# Google Vision Key
COPY docker/ai-worker/keys/google-vision.json /keys/google-vision.json
ENV GOOGLE_APPLICATION_CREDENTIALS=/keys/google-vision.json

CMD ["python", "-m", "apps.worker.ai_worker.run"]
