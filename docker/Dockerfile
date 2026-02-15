# ==============================================================================
# 메인 Dockerfile (공통 베이스 이미지 빌드)
# ==============================================================================
# 사용법: docker build -f docker/Dockerfile.base -t academy-base:latest .
# ==============================================================================

FROM python:3.11-slim as builder

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 빌드 도구 설치 (Gevent 등 C extension 컴파일용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libc6-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# pip 업그레이드
RUN pip install --upgrade pip

# 공통 의존성 설치
COPY requirements/common.txt ./requirements/common.txt
RUN pip install --user --no-cache-dir -r requirements/common.txt

# ==============================================================================
# Runtime Stage
# ==============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV PATH=/root/.local/bin:$PATH

# 런타임 의존성만 설치 (빌드 도구 제외)
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpq5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Builder에서 설치한 Python 패키지 복사
COPY --from=builder /root/.local /root/.local

# 애플리케이션 코드 복사 (Hexagonal: src + apps + libs)
COPY src ./src
COPY apps ./apps
COPY libs ./libs
COPY manage.py ./

# 기본 명령어는 서비스별로 오버라이드
CMD ["python", "--version"]
