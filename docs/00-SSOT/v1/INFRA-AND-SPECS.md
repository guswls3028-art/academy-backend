# 인프라 및 스펙 한눈에 보기 (v1.1)

**SSOT:** `docs/00-SSOT/v1/params.yaml`  
**배포:** `scripts/v1/deploy.ps1`  
**리전:** ap-northeast-2 · **계정:** 809466760795  
**V1.1:** API 2/2/4 최소 HA, 롤링 배포 무중단, RDS PI, Observability SSOT.

---

## 1. 컴포넌트 요약

| 구분 | API 서버 | 빌드 서버 | AI Worker ASG | Messaging Worker ASG | Video Batch |
|------|----------|------------|----------------|----------------------|-------------|
| **역할** | Django API (Gunicorn) | Docker 이미지 빌드·ECR 푸시 | AI 작업(SQS) | SMS/알림톡(SQS) | 영상 인코딩(FFmpeg HLS) |
| **유형** | ALB + ASG | EC2 1대 (태그 기반) | ASG | ASG | Batch CE + Job Queue |
| **인스턴스** | t4g.medium | t4g.medium | t4g.medium | t4g.medium | c6g.xlarge (standard/long) |
| **스케일** | **min=1, max=2** | 1대 고정 | min=1, max=10 | min=1, max=10 | standard: max40 / long: max80 vCPU |
| **리소스 이름** | academy-v1-api-asg | academy-build-arm64 | academy-v1-ai-worker-asg | academy-v1-messaging-worker-asg | academy-v1-video-batch-ce, academy-v1-video-batch-long-ce |

---

## 2. API 서버

| 항목 | 값 |
|------|-----|
| ASG | academy-v1-api-asg |
| ALB | academy-v1-api-alb |
| Target Group | academy-v1-api-tg |
| Launch Template | academy-v1-api-lt |
| 인스턴스 타입 | t4g.medium |
| **min / max** | **2 / 4** (V1.1 최소 HA) |
| desired | 2 |
| 롤링 배포 | MinHealthyPercentage=100, InstanceWarmup=300s |

---

## 3. Messaging Worker ASG

| 항목 | 값 |
|------|-----|
| ASG | academy-v1-messaging-worker-asg |
| 큐 | academy-v1-messaging-queue |
| DLQ | academy-v1-messaging-queue-dlq (Bootstrap 연동) |
| VisibilityTimeout | 900초 (처리 최악 시간보다 크게) |
| 인스턴스 | t4g.medium, min=1 max=10 |

---

## 4. AI Worker ASG

| 항목 | 값 |
|------|-----|
| ASG | academy-v1-ai-worker-asg |
| 큐 | academy-v1-ai-queue |
| DLQ | academy-v1-ai-queue-dlq (Bootstrap 연동) |
| VisibilityTimeout | 3600초 (inference 최대 60분 대비) |
| 인스턴스 | t4g.medium, min=1 max=10 |

---

## 5. Video Batch (standard / long 2-tier)

| 항목 | standard | long |
|------|----------|------|
| **용도** | 3시간 이하 일반 작업 | 3시간 초과·장시간 작업 (Spot 중단 회피) |
| CE | academy-v1-video-batch-ce | academy-v1-video-batch-long-ce |
| Queue | academy-v1-video-batch-queue | academy-v1-video-batch-long-queue |
| JobDef | academy-v1-video-batch-jobdef | academy-v1-video-batch-long-jobdef |
| **maxvCpus** | 40 | 80 |
| **Job timeout** | 6h (21600s) | 12h (43200s) |
| **Stuck(heartbeat_age)** | 20분 | 45분 |
| **할당** | Spot 혼합 (BEST_FIT_PROGRESSIVE) | On-Demand (BEST_FIT) |
| Ops CE/Queue | academy-v1-video-ops-ce, academy-v1-video-ops-queue | (동일) |
| EventBridge | academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate | (동일) |
| DynamoDB Lock | academy-v1-video-job-lock (PK=videoId, TTL 12h, heartbeat 연장) | (동일) |
| DynamoDB Checkpoint | academy-v1-video-upload-checkpoints (R2 multipart resume) | (동일) |

---

## 6. 공통·네트워크

| 항목 | 값 |
|------|-----|
| VPC | academy-v1-vpc |
| 보안 그룹 | academy-v1-sg-app, academy-v1-sg-batch, academy-v1-sg-data |
| RDS | academy-v1-db (PostgreSQL 15, db.t4g.medium) |
| Redis | academy-v1-redis (cache.t4g.small, 7.1) |

---

## 7. 관련 문서

- **params.yaml:** `docs/00-SSOT/v1/params.yaml`
- **SSOT:** `docs/00-SSOT/v1/SSOT.md`
