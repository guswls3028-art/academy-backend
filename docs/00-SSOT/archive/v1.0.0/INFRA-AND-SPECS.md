# 인프라 및 스펙 한눈에 보기 (V1)

**SSOT:** `docs/00-SSOT/v1/params.yaml`  
**배포:** `scripts/v1/deploy.ps1`  
**리전:** ap-northeast-2 · **계정:** 809466760795  
**V1:** API min=1/desired=1/max=2 (런칭 전 비용 절감), 롤링 배포 무중단, RDS PI, Observability SSOT. 첫 배포는 V1 기준만 사용.

---

## 1. 컴포넌트 요약

| 구분 | API 서버 | 빌드 | AI Worker ASG | Messaging Worker ASG | Video Batch |
|------|----------|------------|----------------|----------------------|-------------|
| **역할** | Django API (Gunicorn) | GitHub Actions(OIDC) 빌드·ECR 푸시 | AI 작업(SQS) | SMS/알림톡(SQS) | 영상 인코딩(FFmpeg HLS) |
| **유형** | ALB + ASG | CI (EC2 빌드 서버 없음) | ASG | ASG | Batch CE + Job Queue |
| **인스턴스** | t4g.medium | - | t4g.medium | t4g.medium | c6g.xlarge (standard/long) |
| **스케일** | **min=1, desired=1, max=2** (V1 비용 절감) | - | min=1, max=5 | min=1, max=3 | standard: max40 vCPU / long: max80 vCPU |
| **리소스 이름** | academy-v1-api-asg | - | academy-v1-ai-worker-asg | academy-v1-messaging-worker-asg | academy-v1-video-batch-ce, academy-v1-video-batch-long-ce |

---

## 2. API 서버

| 항목 | 값 |
|------|-----|
| ASG | academy-v1-api-asg |
| ALB | academy-v1-api-alb |
| Target Group | academy-v1-api-tg |
| Launch Template | academy-v1-api-lt |
| 인스턴스 타입 | t4g.medium |
| **min / desired / max** | **1 / 1 / 2** (V1 비용 절감, params.yaml 기준) |
| 롤링 배포 | MinHealthyPercentage=100, InstanceWarmup=300s |

---

## 3. Messaging Worker ASG

| 항목 | 값 |
|------|-----|
| ASG | academy-v1-messaging-worker-asg |
| 큐 | academy-v1-messaging-queue |
| DLQ | academy-v1-messaging-queue-dlq (Bootstrap 연동) |
| VisibilityTimeout | 900초 (처리 최악 시간보다 크게) |
| 인스턴스 | t4g.medium, min=1 max=3 |

---

## 4. AI Worker ASG

| 항목 | 값 |
|------|-----|
| ASG | academy-v1-ai-worker-asg |
| 큐 | academy-v1-ai-queue |
| DLQ | academy-v1-ai-queue-dlq (Bootstrap 연동) |
| VisibilityTimeout | 1800초 (엑셀 worst-case 30분, in-flight 유실 방지) |
| 인스턴스 | t4g.medium, min=1 max=5 |

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

## 7. IAM 역할 및 주요 권한

| 역할 | 사용 주체 | 주요 권한 |
|------|-----------|-----------|
| `academy-video-batch-job-role` | Batch 컨테이너(video worker) | SSM GetParameter, ECR pull, CloudWatch Logs, CloudWatch PutMetric, Batch(SubmitJob/TerminateJob/DescribeJobs), **DynamoDB(PutItem/DeleteItem/GetItem/UpdateItem/ConditionCheckItem** on job-lock + upload-checkpoints) |
| `academy-ec2-role` (API EC2) | API EC2 인스턴스 | SSM(등록), ECR pull, Batch SubmitJob(upload_complete용), **DynamoDB(video-job-lock lock_acquire)** |
| `academy-batch-service-role` | AWS Batch 서비스 | AWSBatchServiceRole |
| `academy-batch-ecs-instance-role` | Batch EC2 인스턴스 | AmazonEC2ContainerServiceforEC2Role |
| `academy-batch-ecs-task-execution-role` | ECS Task 실행 | AmazonECSTaskExecutionRolePolicy |
| `academy-eventbridge-batch-video-role` | EventBridge | Batch SubmitJob (ops queue) |

> **중요:** `academy-video-batch-job-role`에 DynamoDB 권한이 없으면 job 완료 후 `lock_release()`가 AccessDeniedException → stale lock → 다음 upload_complete 503.
> 인프라 재설치 시 반드시 `scripts/v1/templates/iam/policy_video_job_role.json` 기준으로 inline policy 적용할 것 (`scripts/v1/resources/iam.ps1`의 `Ensure-BatchIAM`이 자동 적용).

---

## 8. 관련 문서

- **params.yaml:** `docs/00-SSOT/v1/params.yaml`
- **SSOT:** `docs/00-SSOT/v1/SSOT.md`
