# 배포 마스터 가이드 (Deployment Master Guide)

**최종 업데이트**: 2026-02-12  
**목적**: 프로덕션 배포를 위한 단일 진실의 원천 (Single Source of Truth)  
**대상**: DevOps 엔지니어, 인프라 관리자, 배포 담당자

---

## 📋 목차

1. [인프라 아키텍처](#1-인프라-아키텍처)
2. [비용 방어 전략](#2-비용-방어-전략)
3. [배포 절차](#3-배포-절차)
4. [환경 변수 리스트](#4-환경-변수-리스트)
5. [확장 로드맵](#5-확장-로드맵)
6. [모니터링 및 검증](#6-모니터링-및-검증)
7. [트러블슈팅](#7-트러블슈팅)

---

## 1. 인프라 아키텍처

### 1.1 전체 아키텍처 개요

```
Internet
   │
   ▼
Cloudflare CDN (pub-*.r2.dev)
   │
   ├─── Frontend (Static Assets)
   │
   └─── API Server (Docker Container)
         │
         ├─── RDS PostgreSQL (db.t4g.micro → db.t4g.medium)
         ├─── Cloudflare R2 Storage (academy-ai, academy-video)
         └─── AWS SQS (Video + AI 3-Tier Queues)
                │
                ├─── Video Worker (EC2/Fargate)
                ├─── AI Worker CPU (EC2/Fargate)
                └─── AI Worker GPU (EC2 g4dn.xlarge, 향후)
```

### 1.2 스토리지 계층 (Storage Layer)

#### Cloudflare R2
- **SDK**: boto3 (S3-compatible API)
- **버킷**:
  - `academy-ai`: AI 작업 결과 저장
  - `academy-video`: 비디오 파일 및 HLS 세그먼트 저장
- **엔드포인트**: 환경 변수 `R2_ENDPOINT`로 설정
- **Public URL**: `https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev`
- **비용**: S3 대비 ~60% 절감

**설정 위치**: `apps/api/config/settings/base.py`
```python
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL")
R2_AI_BUCKET = os.getenv("R2_AI_BUCKET", "academy-ai")
R2_VIDEO_BUCKET = os.getenv("R2_VIDEO_BUCKET", "academy-video")
```

**확인 사항**:
- ✅ AWS S3 사용 안 함 (모든 boto3 클라이언트가 R2_ENDPOINT 사용)
- ✅ 하드코딩된 `s3.amazonaws.com` 없음
- ✅ Stateless 환경: 컨테이너 내부 파일 저장 없음 (모든 파일은 R2)

### 1.3 CDN 계층 (CDN Layer)

#### Cloudflare CDN
- **Base URL**: `https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev`
- **Signed URL**: Cloudflare Worker 검증 (조건부 활성화)
- **설정**: `CDN_HLS_SIGNING_SECRET` 환경 변수로 활성화

**확인 사항**:
- ✅ CloudFront 코드 제거됨 (deprecated)
- ✅ Cloudflare signed URL 사용 (query parameter 기반)

### 1.4 큐 시스템 (Queue System)

#### AWS SQS
- **Video Queue**: `academy-video-jobs` + DLQ
- **AI Lite Queue**: `academy-ai-jobs-lite` + DLQ
- **AI Basic Queue**: `academy-ai-jobs-basic` + DLQ
- **AI Premium Queue**: `academy-ai-jobs-premium` + DLQ

**특징**:
- ✅ Long Polling 사용 (20초, 비용 절감)
- ✅ Redis/Celery 제거됨 (SQS만 사용)
- ✅ Dead Letter Queue (DLQ) 자동 설정

**큐 생성 스크립트**:
```bash
# Video Queue
python scripts/create_sqs_resources.py ap-northeast-2

# AI Queues (3-Tier)
python scripts/create_ai_sqs_resources.py ap-northeast-2
```

### 1.5 데이터베이스 (Database)

#### RDS PostgreSQL
- **현재**: db.t4g.micro (87 max_connections)
- **10k DAU**: db.t4g.medium (Multi-AZ 권장)
- **Connection Pooling**: PgBouncer 권장 (10k DAU 시 필수)

**현재 설정**:
- `CONN_MAX_AGE`: 60초 (기본값, 환경 변수로 조정 가능)
- `ENGINE`: `django.db.backends.postgresql`
- **예상 연결 수**: 4 workers × 10 = 40 connections (현재 안전)

**10k DAU 시나리오**:
- 필요한 workers: 8-16
- 예상 연결 수: 80-160 connections
- **조치 필요**: PgBouncer 도입 또는 RDS 인스턴스 업그레이드

### 1.6 컴퓨팅 리소스 (Compute)

#### API 서버
- **Runtime**: Docker Container (Gunicorn + Gevent)
- **Workers**: 4 (기본값, 환경 변수로 조정)
- **Worker Class**: `gevent` (동시 처리량 10-20배 증가)
- **Worker Connections**: 1000 (기본값)
- **배포**: EC2 또는 ECS Fargate

#### Video Worker
- **Runtime**: Docker Container
- **Queue**: `academy-video-jobs`
- **배포**: EC2 (Self-stop 로직 포함) 또는 ECS Fargate Spot

#### AI Worker CPU
- **Runtime**: Docker Container
- **Queues**: `academy-ai-jobs-lite`, `academy-ai-jobs-basic`
- **Weighted Polling**: Basic 3:1 Lite
- **배포**: EC2 (Self-stop 로직 포함) 또는 ECS Fargate Spot

#### AI Worker GPU (향후)
- **Runtime**: Docker Container
- **Queue**: `academy-ai-jobs-premium`
- **배포**: EC2 g4dn.xlarge

---

## 2. 비용 방어 전략

### 2.1 EC2 Self-Stop 로직

**목적**: Idle 상태 EC2 인스턴스 자동 종료로 비용 절감

**구현 위치**:
- `apps/worker/ai_worker/sqs_main.py`
- `apps/worker/ai_worker/sqs_main_cpu.py`
- `apps/worker/ai_worker/sqs_main_gpu.py`
- `apps/worker/video_worker/sqs_main.py`

**동작 방식**:
1. SQS 큐가 연속으로 비어있을 때 카운터 증가
2. `EC2_IDLE_STOP_THRESHOLD` (기본값: 5회) 초과 시 자동 종료
3. IMDSv2를 사용한 안전한 인스턴스 ID 조회
4. boto3를 통한 인스턴스 종료

**비용 절감 효과**: 월 $30-50 절감

**IAM 권한 필요**:
```json
{
  "Effect": "Allow",
  "Action": [
    "ec2:StopInstances",
    "ec2:DescribeInstances"
  ],
  "Resource": "*"
}
```

**환경 변수**:
```bash
EC2_IDLE_STOP_THRESHOLD=5  # 연속 빈 폴링 횟수
```

### 2.2 SQS Long Polling

**목적**: SQS API 호출 비용 절감

**설정**:
- **Wait Time**: 20초 (기본값)
- **환경 변수**: `SQS_WAIT_TIME_SECONDS=20`

**비용 절감 효과**: Short Polling 대비 ~60% 절감

**구현 위치**:
- `libs/queue/client.py`
- `apps/support/video/services/sqs_queue.py`
- `apps/support/ai/services/sqs_queue.py`

### 2.3 AWS Budgets 알림 설정

**목적**: 비용 폭탄 사전 감지

**권장 임계값**:
- **500 DAU**: Warning $150/월, Critical $200/월
- **10k DAU**: Warning $800/월, Critical $1000/월

**설정 명령어**:
```bash
aws budgets create-budget \
  --account-id <account-id> \
  --budget '{
    "BudgetName": "academy-monthly-budget",
    "BudgetLimit": {"Amount": "200", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }' \
  --notifications-with-subscribers '[
    {
      "Notification": {
        "NotificationType": "ACTUAL",
        "ComparisonOperator": "GREATER_THAN",
        "Threshold": 80
      },
      "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "admin@example.com"}]
    }
  ]'
```

### 2.4 비용 예상치

#### 현재 (500 DAU)
| 항목 | 월 비용 |
|------|---------|
| Compute (API + Workers) | $60 |
| ALB | $20 |
| RDS | $15 |
| R2 Storage | $10 |
| Cloudflare CDN | $0 (무료 tier) |
| SQS | $2 |
| CloudWatch | $1 |
| **총계** | **~$108/월** |

#### 목표 (10k DAU)
| 항목 | 월 비용 |
|------|---------|
| Compute (API + Workers) | $200 |
| ALB | $20 |
| RDS (Multi-AZ) | $80 |
| R2 Storage | $100 |
| Cloudflare CDN | $0 (무료 tier) |
| SQS | $10 |
| CloudWatch | $10 |
| **총계** | **~$420/월** |

**비용 최적화 후 예상 절감**: 월 $60-80 (EC2 Self-stop + Gevent 전환)

---

## 3. 배포 절차

### 3.1 사전 준비

#### 1. 환경 변수 설정
```bash
# .env 파일 생성
cp .env.example .env
nano .env  # 필수 환경 변수 입력
```

**필수 입력 항목**:
- `SECRET_KEY`: Django secret key (최소 50자)
- `DB_HOST`: RDS 엔드포인트
- `DB_PASSWORD`: DB 비밀번호
- `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`: R2 자격 증명
- `INTERNAL_WORKER_TOKEN`: Worker 통신 토큰 (최소 32자)

#### 2. 인프라 리소스 생성
```bash
# SQS 큐 생성
python scripts/create_sqs_resources.py ap-northeast-2
python scripts/create_ai_sqs_resources.py ap-northeast-2

# RDS 인스턴스 생성 (AWS Console 또는 Terraform)
# R2 버킷 생성 (Cloudflare Dashboard)
```

### 3.2 Docker 이미지 빌드

#### 방법 1: 빌드 스크립트 사용 (권장)
```bash
chmod +x docker/build.sh
./docker/build.sh
```

#### 방법 2: 수동 빌드
```bash
# 베이스 이미지 빌드
docker build -f docker/Dockerfile.base -t academy-base:latest .

# 서비스별 이미지 빌드
docker build -f docker/api/Dockerfile -t academy-api:latest .
docker build -f docker/ai-worker/Dockerfile -t academy-ai-worker:latest .
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
```

**예상 시간**:
- 베이스 이미지: 2-3분
- API 서버: 1-2분
- AI Worker: 1-2분
- Video Worker: 1-2분

**이미지 크기**:
- 베이스: ~500MB
- API: ~600MB
- AI Worker: ~2GB (ML 라이브러리 포함)
- Video Worker: ~800MB

### 3.3 서비스 시작

#### Docker Compose 사용 (개발/테스트)
```bash
# 전체 서비스 시작
docker-compose up -d

# 특정 서비스만 시작
docker-compose up -d api video-worker ai-worker-cpu

# 로그 확인
docker-compose logs -f api
```

#### 프로덕션 배포 (EC2)
```bash
# 컨테이너 실행
docker run -d \
  --name academy-api \
  --env-file .env \
  -p 8000:8000 \
  academy-api:latest

docker run -d \
  --name academy-video-worker \
  --env-file .env \
  academy-video-worker:latest

docker run -d \
  --name academy-ai-worker-cpu \
  --env-file .env \
  academy-ai-worker:latest
```

### 3.4 데이터베이스 마이그레이션

```bash
# API 컨테이너에서 마이그레이션 실행
docker-compose exec api python manage.py migrate

# 또는 프로덕션 환경
docker exec academy-api python manage.py migrate
```

**주의사항**:
- 마이그레이션은 API 서버에서만 실행
- 프로덕션 배포 전 백업 필수
- 롤백 계획 준비

### 3.5 배포 검증

#### 1. 헬스체크 확인
```bash
curl http://localhost:8000/health
```

#### 2. 컨테이너 상태 확인
```bash
docker-compose ps
# 또는
docker ps
```

#### 3. 로그 확인
```bash
# 모든 서비스 로그
docker-compose logs -f

# 특정 서비스 로그
docker-compose logs -f api
docker-compose logs -f video-worker
docker-compose logs -f ai-worker-cpu

# 구조화된 로그 확인 (SQS 메시지 수명 추적)
docker-compose logs api | grep "SQS_MESSAGE_RECEIVED\|SQS_JOB_COMPLETED"

# Graceful shutdown 로그 확인
docker-compose logs video-worker | grep "Graceful shutdown"
```

#### 4. R2 연결 확인
```bash
# API 컨테이너에서 테스트
docker-compose exec api python manage.py shell
>>> from apps.infrastructure.storage.r2 import get_r2_client
>>> client = get_r2_client()
>>> client.list_buckets()  # 버킷 목록 확인
```

#### 5. SQS 연결 확인
```bash
# Worker 로그에서 확인
docker-compose logs ai-worker-cpu | grep "SQS_MESSAGE_RECEIVED"
```

---

## 4. 환경 변수 리스트

### 4.1 Django 기본 설정

```bash
SECRET_KEY=your-secret-key-change-in-production-min-50-chars
DEBUG=false
DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod
```

### 4.2 Database 설정

```bash
DB_NAME=academy_db
DB_USER=academy_user
DB_PASSWORD=your-database-password
DB_HOST=your-rds-endpoint.rds.amazonaws.com
DB_PORT=5432
DB_CONN_MAX_AGE=60  # PgBouncer 사용 시 0으로 설정
```

### 4.3 Cloudflare R2 Storage 설정

```bash
R2_ACCESS_KEY=your-r2-access-key
R2_SECRET_KEY=your-r2-secret-key
R2_ENDPOINT=https://your-account-id.r2.cloudflarestorage.com
R2_PUBLIC_BASE_URL=https://pub-xxxxx.r2.dev
R2_AI_BUCKET=academy-ai
R2_VIDEO_BUCKET=academy-video
R2_PREFIX=media/hls/videos
R2_REGION=auto
```

### 4.4 CDN 설정

```bash
CDN_HLS_BASE_URL=https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev
CDN_HLS_SIGNING_SECRET=your-signing-secret-for-signed-urls
CDN_HLS_SIGNING_KEY_ID=v1
```

### 4.5 AWS SQS 설정

```bash
AWS_REGION=ap-northeast-2
VIDEO_SQS_QUEUE_NAME=academy-video-jobs
AI_SQS_QUEUE_NAME_LITE=academy-ai-jobs-lite
AI_SQS_QUEUE_NAME_BASIC=academy-ai-jobs-basic
AI_SQS_QUEUE_NAME_PREMIUM=academy-ai-jobs-premium
SQS_WAIT_TIME_SECONDS=20  # Long Polling 대기 시간
```

### 4.6 Worker 설정

```bash
INTERNAL_WORKER_TOKEN=your-internal-worker-token-min-32-chars
API_BASE_URL=https://api.hakwonplus.com

# Worker ID
VIDEO_WORKER_ID=video-worker-1
AI_WORKER_ID_CPU=ai-worker-cpu-1
AI_WORKER_ID_GPU=ai-worker-gpu-1

# EC2 Self-Stop 설정
EC2_IDLE_STOP_THRESHOLD=5  # 연속 빈 폴링 횟수

# AI Worker 우선순위 설정
AI_WORKER_BASIC_POLL_WEIGHT=3
AI_WORKER_LITE_POLL_WEIGHT=1
```

### 4.7 Gunicorn 설정 (API 서버 확장성)

```bash
GUNICORN_WORKERS=4  # 기본값
GUNICORN_WORKER_CONNECTIONS=1000  # 기본값
```

### 4.8 Video Worker 설정

```bash
VIDEO_WORKER_TEMP_DIR=/tmp
FFMPEG_BIN=ffmpeg
FFPROBE_BIN=ffprobe
HLS_TIME_SECONDS=6
MIN_SEGMENTS_PER_VARIANT=3
THUMBNAIL_AT_SECONDS=5
UPLOAD_MAX_CONCURRENCY=4
RETRY_MAX_ATTEMPTS=5
BACKOFF_BASE_SECONDS=0.5
BACKOFF_CAP_SECONDS=10.0
```

### 4.9 Site 설정

```bash
SITE_URL=https://hakwonplus.com
```

### 4.10 Google Vision (선택사항)

```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/google-vision.json
```

**전체 환경 변수 템플릿**: `.env.example` 파일 참조

---

## 5. 확장 로드맵

### 5.1 현재 상태 (3명 원장)

**인프라 구성**:
- API 서버: t4g.micro 1대 (4 workers)
- Video Worker: t4g.small 1대 (Self-stop)
- AI Worker CPU: t4g.medium 1대 (Self-stop)
- RDS: db.t4g.micro (87 max_connections)
- 예상 트래픽: ~100-500 DAU

**비용**: ~$108/월

### 5.2 중간 단계 (10-20명 원장)

**필요 액션**:
1. ✅ **코드 레벨 준비 완료**:
   - Gevent worker 전환 (동시 처리량 10-20배 증가)
   - Graceful shutdown (안전한 배포)
   - 구조화된 로깅 (request_id 추적)
   - EC2 Self-stop (비용 절감)

2. ⚠️ **인프라 조정 필요**:
   - API 서버: t4g.small 2대 (고가용성)
   - RDS: db.t4g.small (연결 수 증가 대비)
   - Worker: 수평 확장 (트래픽에 따라)

**예상 트래픽**: ~1,000-2,000 DAU  
**예상 비용**: ~$200-300/월

### 5.3 목표 단계 (50명 원장)

**필요 액션**:

#### 1. 데이터베이스 확장
- **PgBouncer 도입** (필수)
  - Connection pooling으로 연결 수 제한
  - 비용: t4g.small 추가 (~$15/월)
  - 설정: `CONN_MAX_AGE=0` (PgBouncer가 풀링 담당)

- **RDS 업그레이드**
  - db.t4g.medium (Multi-AZ 권장)
  - 비용: ~$80/월

#### 2. API 서버 확장
- **수평 확장**: t4g.small 4-8대
- **로드 밸런서**: ALB 설정
- **환경 변수 조정**:
  ```bash
  GUNICORN_WORKERS=8
  GUNICORN_WORKER_CONNECTIONS=2000
  ```

#### 3. Worker 확장
- **Video Worker**: 2-4 인스턴스
- **AI Worker CPU**: 2-4 인스턴스
- **Auto Scaling**: CloudWatch 기반 자동 확장

#### 4. 모니터링 강화
- **CloudWatch Alarms**: DLQ 깊이, 큐 깊이, 에러율
- **로그 집계**: CloudWatch Logs Insights
- **성능 모니터링**: APM 도구 고려

**예상 트래픽**: ~5,000-10,000 DAU  
**예상 비용**: ~$400-500/월

### 5.4 확장 체크리스트

#### 코드 레벨 (✅ 완료)
- [x] Stateless 환경 (R2 사용)
- [x] Graceful shutdown
- [x] 구조화된 로깅
- [x] EC2 Self-stop
- [x] Gevent worker 전환
- [x] 부분 인덱스 최적화

#### 인프라 레벨 (단계별 진행)
- [ ] PgBouncer 도입 (10명 원장 시)
- [ ] RDS 업그레이드 (20명 원장 시)
- [ ] ALB 설정 (20명 원장 시)
- [ ] Auto Scaling 설정 (30명 원장 시)
- [ ] CloudWatch Alarms 설정 (즉시)

---

## 6. 모니터링 및 검증

### 6.1 필수 모니터링 지표

#### 데이터베이스
- **연결 수**: `SELECT count(*) FROM pg_stat_activity;`
- **쿼리 성능**: 느린 쿼리 로그 활성화
- **디스크 사용량**: RDS CloudWatch 메트릭

#### API 서버
- **응답 시간**: P95, P99
- **에러율**: 5xx 에러 비율
- **동시 요청 수**: Active connections
- **메모리 사용량**: Container memory usage

#### Worker
- **큐 깊이**: SQS 메시지 수
- **처리 시간**: Job duration
- **에러율**: Failed jobs
- **DLQ 깊이**: Dead Letter Queue 메시지 수

#### SQS
- **큐 깊이**: `ApproximateNumberOfMessages`
- **메시지 연령**: `ApproximateAgeOfOldestMessage`
- **DLQ 깊이**: `ApproximateNumberOfMessages` (DLQ)

### 6.2 CloudWatch Alarms 설정

#### DLQ 깊이 알람
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name academy-dlq-depth \
  --alarm-description "Alert when DLQ has messages" \
  --metric-name ApproximateNumberOfMessages \
  --namespace AWS/SQS \
  --statistic Average \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1
```

#### 큐 깊이 알람
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name academy-queue-depth \
  --alarm-description "Alert when queue depth is high" \
  --metric-name ApproximateNumberOfMessages \
  --namespace AWS/SQS \
  --statistic Average \
  --period 300 \
  --threshold 1000 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2
```

### 6.3 로그 검증

#### 구조화된 로그 확인
```bash
# SQS 메시지 수명 추적
docker-compose logs api | grep "SQS_MESSAGE_RECEIVED\|SQS_JOB_COMPLETED"

# Graceful shutdown 확인
docker-compose logs video-worker | grep "Graceful shutdown"

# EC2 Self-stop 확인
docker-compose logs ai-worker-cpu | grep "EC2 instance stopped"
```

#### 로그 형식 예시
```
SQS_MESSAGE_RECEIVED | request_id=abc123 | queue_wait_sec=5.2 | message_id=msg-123
SQS_JOB_COMPLETED | request_id=abc123 | processing_duration=2.5 | total_duration=7.7
```

---

## 7. 트러블슈팅

### 7.1 이미지 빌드 실패

**증상**: Docker 빌드 중 에러 발생

**해결**:
```bash
# 캐시 없이 재빌드
docker build --no-cache -f docker/Dockerfile.base -t academy-base:latest .
```

### 7.2 컨테이너 시작 실패

**증상**: 컨테이너가 시작되지 않음

**해결**:
```bash
# 로그 확인
docker-compose logs api

# 환경 변수 확인
docker-compose exec api env | grep DB_

# 컨테이너 재시작
docker-compose restart api
```

### 7.3 데이터베이스 연결 실패

**증상**: `django.db.utils.OperationalError: could not connect to server`

**해결**:
```bash
# DB 연결 테스트
docker-compose exec api python manage.py dbshell

# 연결 수 확인
docker-compose exec api python manage.py shell
>>> from django.db import connection
>>> connection.queries  # 쿼리 로그 확인
```

### 7.4 R2 연결 실패

**증상**: `botocore.exceptions.ClientError: Access Denied`

**해결**:
1. 환경 변수 확인: `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`
2. 버킷 이름 확인: `R2_AI_BUCKET`, `R2_VIDEO_BUCKET`
3. R2 권한 확인 (Cloudflare Dashboard)

### 7.5 SQS 메시지 처리 실패

**증상**: Worker가 메시지를 받지 못함

**해결**:
```bash
# 큐 깊이 확인
aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/.../academy-video-jobs \
  --attribute-names ApproximateNumberOfMessages

# Worker 로그 확인
docker-compose logs video-worker | grep "SQS_MESSAGE_RECEIVED"

# IAM 권한 확인
aws iam get-role-policy --role-name academy-worker-role --policy-name SQS-Policy
```

### 7.6 Graceful Shutdown 실패

**증상**: 배포 시 작업이 중단됨

**해결**:
1. 로그 확인: `docker-compose logs video-worker | grep "Graceful shutdown"`
2. SQS Visibility Timeout 확인 (기본값: 300초)
3. 작업 처리 시간 확인 (Visibility Timeout보다 짧아야 함)

### 7.7 EC2 Self-Stop 작동 안 함

**증상**: Idle 상태인데 인스턴스가 종료되지 않음

**해결**:
1. IAM 권한 확인: `ec2:StopInstances`, `ec2:DescribeInstances`
2. 환경 변수 확인: `EC2_IDLE_STOP_THRESHOLD=5`
3. 로그 확인: `docker-compose logs ai-worker-cpu | grep "EC2 instance stopped"`

---

## 8. 배포 체크리스트

### 배포 전
- [ ] `.env` 파일 생성 및 모든 환경 변수 설정
- [ ] 베이스 이미지 빌드 완료
- [ ] 서비스별 이미지 빌드 완료
- [ ] SQS 큐 생성 완료
- [ ] R2 버킷 생성 완료
- [ ] RDS 인스턴스 생성 완료
- [ ] IAM 역할 및 권한 설정 완료
- [ ] AWS Budgets 알림 설정 완료

### 배포 중
- [ ] 마이그레이션 실행 완료
- [ ] 헬스체크 통과 확인
- [ ] 로그 출력 확인 (stdout/stderr)
- [ ] R2 연결 확인
- [ ] SQS 연결 확인
- [ ] 데이터베이스 연결 확인

### 배포 후 (첫 주)
- [ ] Graceful shutdown 테스트
- [ ] 로그 가시성 확인 (request_id 추적)
- [ ] EC2 Self-stop 테스트
- [ ] Gevent worker 성능 확인
- [ ] 데이터베이스 연결 수 모니터링
- [ ] 큐 깊이 모니터링
- [ ] DLQ 모니터링

---

## 9. 참고 문서

- **Docker 배포 가이드**: `docs/DOCKER_DEPLOYMENT_GUIDE.md`
- **인프라 아키텍처**: `docs/INFRASTRUCTURE.md`
- **비용 예측**: `docs/COST_FORECAST.md`
- **큐 시스템**: `docs/QUEUE_SYSTEM.md`
- **최적화 리포트**: `docs/FINAL_OPTIMIZATION_REPORT.md`

---

**작성일**: 2026-02-12  
**최종 검토**: 배포 전 필수 확인  
**문의**: DevOps 팀
