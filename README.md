# Academy Backend

학원 관리 시스템 백엔드 API 서버

**프론트 구분 (SSOT)**: 학생 앱 = `academyfront/src/student/**` 전용. 그 외는 모두 관리자 앱 (`academyfront` 나머지 전체).

**Cursor 작업 시**: [docs_cursor/README.md](docs_cursor/README.md) — 문서만으로 필요한 정보를 얻을 수 있도록 정리된 SSOT.

---

## 🚀 빠른 시작

### 배포·문서

- **배포 절차**: [docs/배포.md](docs/배포.md)
- **문서 인덱스**: [docs/README.md](docs/README.md) — 전체 목록

---

## 📁 프로젝트 구조

```
academy/
├── apps/
│   ├── api/                # API 설정 (config/settings)
│   ├── core/               # Tenant, Program, TenantDomain, TenantMembership, 권한 (apps/core/CORE_SEAL.md)
│   ├── domains/            # 도메인 모듈 (students, lectures, exams, results, ...)
│   ├── support/            # video, messaging 등
│   └── worker/             # ai_worker, video_worker, messaging_worker
├── academy/                # adapters (repositories_core 등)
├── docker/
│   ├── Dockerfile.base
│   ├── api/Dockerfile
│   ├── video-worker/Dockerfile
│   ├── ai-worker/Dockerfile
│   ├── ai-worker-cpu/Dockerfile
│   ├── ai-worker-gpu/Dockerfile
│   ├── messaging-worker/Dockerfile
│   ├── build.ps1, build.sh
│   └── README-COMPOSE.md
├── docs/                   # 배포.md, 운영.md, 설계.md, 10K_기준.md, 30K_기준.md, adr/
├── requirements/
└── manage.py
```

---

## 🏗️ 인프라 아키텍처

### 스토리지
- **Cloudflare R2**: 모든 미디어·파일 저장 (S3-compatible)
- **버킷**: `academy-ai`, `academy-video`, `academy-excel`, `academy-storage` (설정: `.env.example`, `apps/api/config/settings/base.py`)

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

**설계·인프라**: [docs/설계.md](docs/설계.md)

---

## 💰 비용 예상치

### 현재 (500 DAU)
- **월 비용**: ~$108
- **주요 항목**: Compute ($60), RDS ($15), Storage ($10)

### 목표 (10k DAU)
- **월 비용**: ~$420
- **주요 항목**: Compute ($200), RDS ($80), Storage ($100)

**비용·기준**: [docs/10K_기준.md](docs/10K_기준.md), [docs/30K_기준.md](docs/30K_기준.md)

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

**문서 인덱스**: [docs/README.md](docs/README.md)

- [배포.md](docs/배포.md) — 500 배포 순서, Docker 빌드, EC2/ECR
- [운영.md](docs/운영.md) — 운영
- [설계.md](docs/설계.md) — 인프라·워커 설계
- [10K_기준.md](docs/10K_기준.md), [30K_기준.md](docs/30K_기준.md) — 기준점
- [apps/core/CORE_SEAL.md](apps/core/CORE_SEAL.md) — Core 봉인(테넌트·권한 규칙)
- [docs/adr/](docs/adr/) — ADR

---

## 🚀 배포 명령어 (요약)

### 프로덕션 배포

```bash
# 1. 환경 변수 설정
cp .env.example .env
nano .env

# 2. Docker 이미지 빌드 (권장: .\docker\build.ps1 한 번에 실행)
docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/api/Dockerfile -t academy-api:latest .
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
docker build -f docker/ai-worker/Dockerfile -t academy-ai-worker:latest .
docker build -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .

# 3. 서비스 시작
docker-compose up -d

# 4. 마이그레이션 실행
docker-compose exec api python manage.py migrate
```

**상세 배포**: [docs/배포.md](docs/배포.md)

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

**확장·기준**: [docs/10K_기준.md](docs/10K_기준.md), [docs/30K_기준.md](docs/30K_기준.md)

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

**최종 업데이트**: 2026-02-15
