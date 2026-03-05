# V1 총괄 보고서 (한 장) — 스펙·인프라 일괄

**갱신:** 2026-03-06 · **SSOT:** `docs/00-SSOT/v1/params.yaml` · **배포:** `scripts/v1/deploy.ps1` · **리전:** ap-northeast-2 · **계정:** 809466760795  
**V1:** 1인 운영(장애 10~60분 대응), 1k~1.5k 사용자·동시 50~300 버스트. API 최소 HA 2/2/4.

---

## 아키텍처 요약

코드 푸시 → **빌드 서버**(EC2)에서 이미지 빌드·ECR 푸시 → **API**(ALB+ASG) · **Video Batch**(CE+Queue+JobDef) · **AI/Messaging**(ASG+SQS) 오케스트레이션 → RDS·Redis·SSM·R2(Cloudflare). ARM64(Graviton), S3 미사용·R2만 사용.

---

## 1. API 서버

| 항목 | 스펙 |
|------|------|
| **역할** | Django API (Gunicorn), 컨테이너 `academy-api` |
| ASG | academy-v1-api-asg |
| ALB / Target Group | academy-v1-api-alb / academy-v1-api-tg |
| Launch Template | academy-v1-api-lt |
| 인스턴스 타입 | t4g.medium |
| **min / desired / max** | 2 / 2 / 4 (V1 최소 HA) |
| 롤링 배포 | MinHealthyPercentage=100, InstanceWarmup=300s (새 인스턴스 healthy 후 구 인스턴스 제거) |
| AMI | ami-0885e191a9bcf28b0 |
| Instance Profile | academy-ec2-role |
| Security Group | sg-088fa3315c12754d0 (network.securityGroupApp) |
| Health | /health → 200 정상 |
| SSM(API env) | /academy/api/env |
| ECR | academy-api |

---

## 2. 빌드 서버

| 항목 | 스펙 |
|------|------|
| **역할** | Docker 이미지 빌드·ECR 푸시 (배포 시 경유, 로컬 빌드 없음) |
| 식별 | 태그 Name=academy-build-arm64 (EC2 1대) |
| 인스턴스 타입 | t4g.medium |
| AMI | ami-0885e191a9bcf28b0 |
| Instance Profile | academy-ec2-role |
| **동작** | Spot 요청 → 실패 시 온디맨드 재시도. `/opt/academy` 또는 `$HOME/academy`에 리포 클론 필요. |
| ECR 푸시 대상 | academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu, academy-base |

---

## 3. Video Batch (standard / long 2-tier)

| 항목 | 스펙 |
|------|------|
| **역할** | 영상 인코딩(FFmpeg HLS), 1동영상 1 Job·1워커. **standard:** 3h 이하 / **long:** 3h 초과(On-Demand, Spot 회피) |
| **Standard CE** | academy-v1-video-batch-ce (minvCpus=0, maxvCpus=40, c6g.xlarge) |
| **Standard Queue/JobDef** | academy-v1-video-batch-queue, academy-v1-video-batch-jobdef (timeout 6h, stuck heartbeat 20분) |
| **Long CE** | academy-v1-video-batch-long-ce (minvCpus=0, maxvCpus=80, c6g.xlarge, On-Demand) |
| **Long Queue/JobDef** | academy-v1-video-batch-long-queue, academy-v1-video-batch-long-jobdef (timeout 12h, stuck 45분) |
| **Ops CE/Queue** | academy-v1-video-ops-ce, academy-v1-video-ops-queue |
| **EventBridge** | academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate |
| **DynamoDB** | academy-v1-video-job-lock, academy-v1-video-upload-checkpoints (R2 multipart resume) |
| **ECR** | academy-video-worker |

---

## 4. AI Worker ASG (CPU)

| 항목 | 스펙 |
|------|------|
| **역할** | AI 작업 처리 (SQS 소비) |
| ASG | academy-v1-ai-worker-asg |
| Launch Template | academy-v1-ai-worker-lt |
| 인스턴스 타입 | t4g.medium |
| **min / desired / max** | 1 / 1 / 10 |
| Scale In Protection | true |
| SQS Queue | academy-v1-ai-queue (URL: …/academy-v1-ai-queue) |
| 스케일 정책 | ScaleOut Cooldown 300s, ScaleIn 900s / Threshold 20 / 0 |
| AMI | ami-0885e191a9bcf28b0 |
| ECR | academy-ai-worker-cpu |

---

## 5. Messaging Worker ASG

| 항목 | 스펙 |
|------|------|
| **역할** | SMS/알림톡 등 메시징 (SQS 소비) |
| ASG | academy-v1-messaging-worker-asg |
| Launch Template | academy-v1-messaging-worker-lt |
| 인스턴스 타입 | t4g.medium |
| **min / desired / max** | 1 / 1 / 10 |
| Scale In Protection | true |
| SQS Queue | academy-v1-messaging-queue (URL: …/academy-v1-messaging-queue) |
| 스케일 정책 | ScaleOut Cooldown 300s, ScaleIn 900s / Threshold 20 / 0 |
| AMI | ami-0885e191a9bcf28b0 |
| ECR | academy-messaging-worker |

---

## 6. 공통·데이터·스토어

| 항목 | 스펙 |
|------|------|
| VPC | vpc-0831a2484f9b114c2 (172.30.0.0/16), academy-v1-* 네이밍 |
| RDS | academy-db (PostgreSQL 15.16, db.t4g.medium, 20GB). V1: Performance Insights 7일, Multi-AZ toggle. master SSM: /academy/rds/master_password |
| Observability | 로그 retention 30일. 알람 threshold SSOT(observability.*). API 5xx/Target Unhealthy, SQS/Batch/RDS/Redis (평가 5~15분) |
| Redis | academy-v1-redis (cache.t4g.small, 7.1), academy-v1-redis-subnets |
| SSM(워커 env) | /academy/workers/env |
| ECR(immutable) | v1-*, bootstrap-* 접두사 최신 20개 유지, 라이프사이클 배포 시 자동 적용 |

---

## 7. 상태·참조

- **Evidence/Drift:** `docs/00-SSOT/v1/reports/audit.latest.md`, `drift.latest.md`
- **배포 룰:** `.cursor/rules/07_deployment_orchestrator.mdc` · **인증:** `.cursor/rules/08_deployment_env_credentials.mdc` (에이전트 .env 직접 사용)
- **상세 플랜·검증:** `V1-DEPLOYMENT-PLAN.md`, `V1-DEPLOYMENT-VERIFICATION.md`, `V1-FINAL-REPORT.md`, `INFRA-AND-SPECS.md`
- **V1 운영:** `V1-OPERATIONS-GUIDE.md` (설계 요약, 기본값, 확장 경로, 검증 시나리오)
