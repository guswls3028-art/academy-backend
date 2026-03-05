# 인프라 및 스펙 한눈에 보기

**SSOT:** `docs/00-SSOT/v4/params.yaml`  
**배포:** `scripts/v4/deploy.ps1`  
**리전:** ap-northeast-2 · **계정:** 809466760795

---

## 1. 컴포넌트 요약 (한 페이지)

| 구분 | API 서버 | 빌드 서버 | AI Worker ASG | Messaging Worker ASG | Video Batch |
|------|----------|------------|---------------|----------------------|-------------|
| **역할** | Django API (Gunicorn) | Docker 이미지 빌드·ECR 푸시 | AI 작업(Lite/Basic/Premium SQS) | SMS/알림톡(SQS) | 영상 인코딩(FFmpeg HLS) |
| **유형** | ALB + ASG | EC2 1대 (태그 기반) | ASG | ASG | Batch CE + Job Queue |
| **인스턴스 타입** | t4g.medium | t4g.medium | t4g.medium | t4g.medium | c6g.large |
| **아키텍처** | ARM64 (Graviton) | ARM64 | ARM64 | ARM64 | ARM64 |
| **최소/최대** | min=1, max=2 | 1대 고정 | min=1, max=10 | min=1, max=10 | minvCpus=0, maxvCpus=10 |
| **기본 desired** | 1 | — | 1 | 1 | 0 (작업 없을 때 스케일 다운) |
| **네트워크** | Private 서브넷 | Public/Private(선택) | Private 서브넷 | Private 서브넷 | Private 서브넷 |
| **보안 그룹** | sg-app | (params: batch 또는 app) | sg-app | sg-app | sg-batch |
| **헬스체크** | ALB `/health` | — | — | — | Batch CE 상태 |
| **이미지/컨테이너** | ECR academy-api | 로컬 빌드 후 ECR 푸시 | ECR academy-ai-worker-cpu | ECR academy-messaging-worker | ECR academy-video-worker |

---

## 2. API 서버

| 항목 | 값 |
|------|-----|
| **ALB** | academy-v4-api-alb (internet-facing, Public 서브넷) |
| **Target Group** | academy-v4-api-tg, port 8000 |
| **헬스 경로** | /health |
| **ASG** | academy-v4-api-asg |
| **Launch Template** | academy-v4-api-lt |
| **인스턴스 태그** | Name=academy-v4-api |
| **AMI** | params.api.amiId (예: ami-0c55b159cbfafe1f0) |
| **Instance Profile** | academy-api-instance-profile |
| **컨테이너명** | academy-api |
| **EIP** | 사용 안 함 (ALB DNS로만 접근) |

---

## 3. 빌드 서버

| 항목 | 값 |
|------|-----|
| **식별** | EC2 태그 Name=academy-build-arm64 |
| **용도** | Docker 이미지 빌드(ARM64) 후 ECR 푸시 |
| **인스턴스 타입** | t4g.medium |
| **AMI** | params.build.amiId |
| **Instance Profile** | academy-build-instance-profile |
| **서브넷/SG** | params.build.subnetId, securityGroupId (비어 있으면 스크립트에서 결정) |
| **상태** | Stopped 허용 — 필요 시 기동 후 SSM RunCommand로 빌드 실행 |
| **배포 시** | Ensure-Build: 없으면 생성, AMI drift 시 재생성 |

---

## 4. AI Worker ASG

| 항목 | 값 |
|------|-----|
| **ASG** | academy-v4-ai-worker-asg |
| **Launch Template** | academy-v4-ai-worker-lt |
| **인스턴스 타입** | t4g.medium |
| **min / max / desired** | 1 / 10 / 1 |
| **Scale-in protection** | ON |
| **SQS 큐** | academy-v4-ai-queue |
| **큐 URL** | https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-v4-ai-queue |
| **스케일 아웃 임계값** | ApproximateNumberOfMessagesVisible > 20 |
| **스케일 인 임계값** | 0 |
| **Scale-out cooldown** | 300초 |
| **Scale-in cooldown** | 900초 |
| **ECR 이미지** | academy-ai-worker-cpu |

---

## 5. Messaging Worker ASG

| 항목 | 값 |
|------|-----|
| **ASG** | academy-v4-messaging-worker-asg |
| **Launch Template** | academy-v4-messaging-worker-lt |
| **인스턴스 타입** | t4g.medium |
| **min / max / desired** | 1 / 10 / 1 |
| **Scale-in protection** | ON |
| **SQS 큐** | academy-v4-messaging-queue |
| **큐 URL** | https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-v4-messaging-queue |
| **스케일 아웃 임계값** | ApproximateNumberOfMessagesVisible > 20 |
| **스케일 인 임계값** | 0 |
| **Scale-out cooldown** | 300초 |
| **Scale-in cooldown** | 900초 |
| **ECR 이미지** | academy-messaging-worker |

---

## 6. Video Batch

| 항목 | 값 |
|------|-----|
| **Compute Environment** | academy-v4-video-batch-ce |
| **Video Job Queue** | academy-v4-video-batch-queue |
| **Worker Job Definition** | academy-v4-video-batch-jobdef |
| **인스턴스 타입** | t4g.medium |
| **minvCpus / maxvCpus** | 0 / 10 |
| **Ops CE** | academy-v4-video-ops-ce |
| **Ops Queue** | academy-v4-video-ops-queue |
| **Ops Job Definitions** | reconcile, scanstuck, netprobe |
| **EventBridge 규칙** | academy-v4-reconcile-video-jobs, academy-v4-video-scan-stuck-rate |
| **DynamoDB Lock 테이블** | academy-v4-video-job-lock (videoId 중복 제출 방지) |
| **ECR 이미지** | academy-video-worker (immutable tag 권장) |

---

## 7. 공통·네트워크

| 항목 | 값 |
|------|-----|
| **VPC** | academy-v4-vpc (10.0.0.0/16) |
| **Public 서브넷** | 2개 AZ (예: 10.0.1.0/24, 10.0.2.0/24) |
| **Private 서브넷** | 2개 AZ (예: 10.0.11.0/24, 10.0.12.0/24) |
| **NAT** | 1개 (Public 1AZ) |
| **보안 그룹** | academy-v4-sg-app, academy-v4-sg-batch, academy-v4-sg-data |
| **RDS** | academy-v4-db (PostgreSQL 15.16, db.t4g.medium, 20GB) |
| **Redis** | academy-v4-redis (cache.t4g.small, 7.1) |
| **SSM** | /academy/api/env, /academy/workers/env, /academy/deploy-lock |

---

## 8. ECR 리포지토리

| 리포지토리 | 용도 |
|------------|------|
| academy-base | 공통 베이스 이미지 |
| academy-api | API 서버 |
| academy-ai-worker-cpu | AI Worker ASG |
| academy-messaging-worker | Messaging Worker ASG |
| academy-video-worker | Video Batch 작업 |

**레지스트리:** `809466760795.dkr.ecr.ap-northeast-2.amazonaws.com`  
**배포 정책:** immutable tag 사용 권장, `:latest` 배포 금지(Strict).

---

## 9. 배포 Ensure 순서 (참고)

```
Ensure-BatchIAM
Ensure-Network
Confirm-RDSState
Confirm-RedisState
Confirm-SSMEnv
Ensure-ECRRepos
Ensure-DynamoLockTable
Ensure-ASGMessaging
Ensure-ASGAi
Ensure-VideoCE / Ensure-OpsCE / Ensure-VideoQueue / Ensure-OpsQueue
Ensure-VideoJobDef / Ensure-OpsJobDef*
Ensure-EventBridgeRules
Ensure-ALBStack
Ensure-API
Ensure-Build
```

---

## 10. 관련 문서

- **params.yaml:** `docs/00-SSOT/v4/params.yaml` — 스펙의 단일 소스
- **설계:** `docs/00-SSOT/v4/ARCHITECTURE.md`
- **배포 요약:** `docs/00-SSOT/v4/reports/FD1-INFRA-SUMMARY.md`
- **배포 시나리오:** `docs/00-SSOT/v4/ONE-CLICK-DEPLOY-SCENARIOS.md`
