# Academy Backend

학원 관리 시스템 백엔드 API 서버

**프론트 구분 (SSOT)**: 학생 앱 = `academyfront/src/student/**` 전용. 그 외는 모두 관리자 앱 (`academyfront` 나머지 전체).

---

## 🚀 빠른 시작

### 배포 가이드

**⭐ 배포 전 필수 문서**: [`docs/DEPLOYMENT_MASTER_GUIDE.md`](docs/DEPLOYMENT_MASTER_GUIDE.md)

이 문서 하나만 보면 프로덕션 배포가 가능합니다:
- 인프라 아키텍처 (R2, SQS, EC2, RDS)
- 비용 방어 전략 (Self-stop, Long Polling, Budgets)
- 배포 절차 (Docker build, Migration, 실행)
- 환경 변수 리스트 (모든 필수 ENV)
- 확장 로드맵 (3명 → 50명 원장)

---

## 📁 프로젝트 구조

```
academy/
├── apps/                    # Django 애플리케이션
│   ├── api/                # API 서버 설정
│   ├── core/               # 공통 모델 및 유틸리티
│   ├── domains/            # 도메인별 모듈
│   │   ├── ai/            # AI 작업 처리
│   │   ├── students/       # 학생 관리
│   │   ├── lectures/      # 강의 관리
│   │   └── ...
│   ├── support/            # 지원 모듈
│   │   ├── video/         # 비디오 처리
│   │   └── ai/            # AI 서비스
│   └── worker/            # Worker 프로세스
│       ├── ai_worker/     # AI Worker
│       └── video_worker/   # Video Worker
├── docker/                 # Docker 설정
│   ├── Dockerfile.base    # 공통 베이스 이미지
│   ├── api/               # API 서버 Dockerfile
│   ├── ai-worker/         # AI Worker Dockerfile
│   └── video-worker/      # Video Worker Dockerfile
├── docs/                   # 문서
│   ├── DEPLOYMENT_MASTER_GUIDE.md  ⭐ 메인 문서
│   ├── INFRASTRUCTURE.md
│   ├── COST_FORECAST.md
│   └── ...
├── libs/                   # 공통 라이브러리
├── requirements/           # Python 의존성
├── docker-compose.yml      # Docker Compose 설정
└── .env.example            # 환경 변수 템플릿
```

---

## 🏗️ 인프라 아키텍처

### 스토리지
- **Cloudflare R2**: 모든 미디어 파일 저장 (S3-compatible)
- **버킷**: `academy-ai`, `academy-video`

### CDN
- **Cloudflare CDN**: `pub-*.r2.dev` 도메인 사용
- **Signed URL**: Cloudflare Worker 검증 (조건부 활성화)

### 큐 시스템
- **AWS SQS**: 모든 비동기 작업 처리
- **Video Queue**: `academy-video-jobs`
- **AI Queues**: `academy-ai-jobs-{lite,basic,premium}`

### 데이터베이스
- **RDS PostgreSQL**: db.t4g.micro → db.t4g.medium (확장 시)
- **Connection Pooling**: PgBouncer 권장 (10k DAU 시)

### 컴퓨팅
- **API 서버**: Docker Container (Gunicorn + Gevent)
- **Video Worker**: Docker Container (EC2/Fargate)
- **AI Worker CPU**: Docker Container (EC2/Fargate)
- **AI Worker GPU**: Docker Container (EC2 g4dn.xlarge, 향후)

**상세 아키텍처**: [`docs/INFRASTRUCTURE.md`](docs/INFRASTRUCTURE.md)

---

## 💰 비용 예상치

### 현재 (500 DAU)
- **월 비용**: ~$108
- **주요 항목**: Compute ($60), RDS ($15), Storage ($10)

### 목표 (10k DAU)
- **월 비용**: ~$420
- **주요 항목**: Compute ($200), RDS ($80), Storage ($100)

**상세 비용 분석**: [`docs/COST_FORECAST.md`](docs/COST_FORECAST.md)

---

## 🔧 개발 환경 설정

### 필수 요구사항
- Python 3.11+
- Docker & Docker Compose
- PostgreSQL 15+

### 로컬 개발 환경 실행

```bash
# 환경 변수 설정
cp .env.example .env
nano .env  # 필수 값 입력

# Docker Compose로 실행
docker-compose up -d

# 마이그레이션 실행
docker-compose exec api python manage.py migrate

# API 서버 접속
curl http://localhost:8000/health
```

---

## 📚 문서 (SSOT)

**문서 인덱스**: [docs/README.md](docs/README.md) — 최소 구성 유지

- **[DEPLOYMENT_MASTER_GUIDE.md](docs/DEPLOYMENT_MASTER_GUIDE.md)** — 배포·인프라·ENV (프론트/백 공통)
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 아키텍처 개요
- [INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) — AWS·R2·SQS 설정
- [COST_FORECAST.md](docs/COST_FORECAST.md) — 비용 예측

---

## 🚀 배포 명령어 (요약)

### 프로덕션 배포

```bash
# 1. 환경 변수 설정
cp .env.example .env
nano .env

# 2. Docker 이미지 빌드
docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/api/Dockerfile -t academy-api:latest .
docker build -f docker/ai-worker/Dockerfile -t academy-ai-worker:latest .
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .

# 3. 서비스 시작
docker-compose up -d

# 4. 마이그레이션 실행
docker-compose exec api python manage.py migrate
```

**상세 배포 가이드**: [`docs/DEPLOYMENT_MASTER_GUIDE.md`](docs/DEPLOYMENT_MASTER_GUIDE.md)

---

## 🔍 주요 기능

### 학생 관리
- 학생 정보 관리
- 출석 관리
- 성적 관리

### 강의 관리
- 강의 생성 및 관리
- 세션 관리
- 출석 체크

### 비디오 처리
- HLS 스트리밍
- 썸네일 생성
- 재생 모니터링 (PROCTORED_CLASS)

### AI 작업 처리
- OCR (문자 인식)
- OMR (마킹 인식)
- 상태 감지

---

## 📊 확장 로드맵

### 현재 (3명 원장)
- **트래픽**: ~100-500 DAU
- **비용**: ~$108/월
- **인프라**: t4g.micro, db.t4g.micro

### 중간 단계 (10-20명 원장)
- **트래픽**: ~1,000-2,000 DAU
- **비용**: ~$200-300/월
- **인프라**: t4g.small 2대, db.t4g.small

### 목표 단계 (50명 원장)
- **트래픽**: ~5,000-10,000 DAU
- **비용**: ~$400-500/월
- **인프라**: t4g.small 4-8대, db.t4g.medium, PgBouncer

**상세 확장 계획**: [`docs/DEPLOYMENT_MASTER_GUIDE.md#5-확장-로드맵`](docs/DEPLOYMENT_MASTER_GUIDE.md#5-확장-로드맵)

---

## 🛠️ 기술 스택

- **Framework**: Django 4.x
- **API**: Django REST Framework
- **Database**: PostgreSQL 15
- **Queue**: AWS SQS
- **Storage**: Cloudflare R2
- **CDN**: Cloudflare CDN
- **Container**: Docker
- **WSGI Server**: Gunicorn + Gevent

---

## 📝 라이선스

프로젝트 라이선스 정보

---

## 📞 문의

DevOps 팀 또는 프로젝트 관리자에게 문의

---

**최종 업데이트**: 2026-02-12
