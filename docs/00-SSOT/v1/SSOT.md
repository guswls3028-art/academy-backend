# Academy SSOT v1 — 단일진실 문서 (사람용)

**AI·Cursor 룰:** 본 문서를 포함한 리포지토리 내 **모든 문서·코드에 대해 AI(Cursor Agent)는 열람·수정 권한**이 있다. 배포·인프라·비용·문서 변경 시 **.cursor/rules/** 내 해당 룰(예: `07_deployment_orchestrator.mdc`, `04_cost_engine.mdc`)을 **적재적소에 항시 확인**한다.  
**배포 원칙:** 빌드 서버는 사용하지 않는다(0대). 이미지 빌드·ECR 푸시는 **GitHub Actions(OIDC)만 사용**한다. `deploy.ps1`는 pull/refresh만 수행한다.
**비용 최적화:** 불필요한 ECR 이미지를 남기지 않도록 배포 시 ECR 라이프사이클 정책이 자동 적용된다.

**역할:** 풀셋팅 인프라의 유일한 기준. 기계용 값은 `params.yaml`만 사용.

**배포:** `scripts/v1/deploy.ps1`  
**params:** `docs/00-SSOT/v1/params.yaml`  
**API ASG max:** 2 고정 (solo dev, medium reliability)

---

## 1. 시스템 구성 (v1 네이밍)

| # | 컴포넌트 | 형태 | 리소스 이름 |
|---|----------|------|-------------|
| 1 | API | EC2 ASG + ALB | academy-v1-api-asg, academy-v1-api-alb |
| 2 | Build | GitHub Actions(OIDC) only | 빌드 서버 0대. 이미지 빌드·ECR 푸시는 CI로만 수행 |
| 3 | Video Worker | AWS Batch | academy-v1-video-batch-ce, academy-v1-video-batch-queue |
| 4 | Ops Batch | Batch Ops CE/Queue + EventBridge | academy-v1-video-ops-*, academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate |
| 5 | AI Worker | ASG | academy-v1-ai-worker-asg, academy-v1-ai-queue |
| 6 | Messaging Worker | ASG | academy-v1-messaging-worker-asg, academy-v1-messaging-queue |
| 7 | RDS | PostgreSQL | academy-v1-db |
| 8 | Redis | ElastiCache | academy-v1-redis |
| 9 | Storage | R2 + CDN | 설정만 SSM/.env |

---

## 2. Canonical 리소스 (v1)

| 유형 | 이름 |
|------|------|
| VPC/네트워크 | academy-v1-vpc, academy-v1-sg-app, academy-v1-sg-batch, academy-v1-sg-data |
| API | academy-v1-api-asg, academy-v1-api-lt, academy-v1-api-alb, academy-v1-api-tg |
| ASG | academy-v1-messaging-worker-asg, academy-v1-ai-worker-asg |
| Batch CE/Queue | academy-v1-video-batch-ce, academy-v1-video-batch-long-ce, academy-v1-video-ops-ce, academy-v1-video-batch-queue, academy-v1-video-batch-long-queue, academy-v1-video-ops-queue |
| JobDef | academy-v1-video-batch-jobdef, academy-v1-video-batch-long-jobdef, academy-v1-video-ops-reconcile, scanstuck, netprobe |
| EventBridge | academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate |
| RDS | academy-v1-db |
| Redis | academy-v1-redis |
| DynamoDB | academy-v1-video-job-lock, academy-v1-video-upload-checkpoints |
| SSM | /academy/api/env, /academy/workers/env |
| ECR | academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu |

**Video Batch 정책 (1동영상 1작업)**  
- CE: minvCpus 0, maxvCpus 10.  
- 동영상 1개 = Batch Job 1개 제출. 사용자 동영상 5개 업로드 → Job 5개 제출 → 워커 최대 5대 기동, 각 1개 처리 후 종료.  
- **1 워커가 2개 이상 작업 처리 금지** — Job 정의상 1 Job = 1 컨테이너 = 1 동영상만 처리. N개 업로드 시 반드시 Job N개로 제출할 것.

**Video Ops (reconcile / scanstuck / netprobe)**  
- Ops CE: **minvCpus 0, maxvCpus 2** (인스턴스 1대만). 인스턴스 타입: **m6g.medium** (Batch가 ap-northeast-2에서 t4g 미지원이라 동일 2vCPU ARM인 m6g.medium 사용).  
- **워커(video batch) 안 떠 있으면 Ops도 떠 있으면 안 됨** — Ops CE min 0이라 작업 없으면 인스턴스 0대. Ops Job은 EventBridge 스케줄 시에만 제출되며, 그때만 1대 기동.

---

## 3. 배포 순서

1. Guard(동시 실행 락)
2. Load params.yaml (v1) + validate
3. Preflight
4. Drift 계산 → 표 출력
5. (옵션) PruneLegacy
6. Ensure: IAM → Network → RDS/Redis → SSM → ECR → DynamoDB → ASG Messaging/AI → Batch CE/Queue → JobDef → EventBridge → ALB → API → Build
7. Netprobe
8. Evidence
9. Lock 해제

---

## 4. Quickstart

```powershell
cd academy
pwsh scripts/v1/deploy.ps1 -Plan
pwsh scripts/v1/deploy.ps1 -Env prod
```

---

## 5. 참조

- **기계 SSOT:** `docs/00-SSOT/v1/params.yaml`
- **스펙 한눈에:** `docs/00-SSOT/v1/INFRA-AND-SPECS.md`
- **계약:** `state-contract.md`, **운영:** `runbook.md`
- **배포·검증·최종 보고:** `V1-DEPLOYMENT-PLAN.md`, `V1-DEPLOYMENT-VERIFICATION.md`, `V1-FINAL-REPORT.md`
