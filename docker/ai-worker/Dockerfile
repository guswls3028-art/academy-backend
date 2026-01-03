# academy/docker/ai-worker/Dockerfile

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 시스템 의존성
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    ffmpeg \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 코드 복사
COPY apps ./apps
COPY libs ./libs
COPY pyproject.toml .
COPY poetry.lock .

# 의존성
RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi

ENV PYTHONPATH=/app

CMD ["python", "-m", "apps.worker.run"]
