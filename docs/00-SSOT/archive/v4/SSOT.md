# Academy SSOT v4 — 단일진실 문서 (사람용)

**역할:** 전체 인프라의 유일한 사람용 기준. 기계용 값은 `params.yaml`만 사용한다.

---

## 1. 시스템 구성도

| # | 컴포넌트 | 형태 | 비고 |
|---|----------|------|------|
| 1 | **API** | EC2 1대 + Elastic IP, Docker `academy-api` | Public Subnet. SSM `/academy/api/env`. |
| 2 | **Build** | EC2 Tag `academy-build-arm64` | 이미지 빌드·ECR 푸시. |
| 3 | **Video Worker** | AWS Batch | CE `academy-video-batch-ce-final`, Queue, JobDef. 영상→R2. |
| 4 | **Ops Batch** | Batch Ops CE/Queue + EventBridge | reconcile(15분), scan_stuck(5분), netprobe 검증. |
| 5 | **AI Worker** | ASG `academy-ai-worker-asg` | EC2, SQS, Launch Template. |
| 6 | **Messaging Worker** | ASG `academy-messaging-worker-asg` | EC2, SQS, Launch Template. |
| 7 | **RDS** | `academy-db` | PostgreSQL. Validate only(삭제 금지 기본). |
| 8 | **Redis** | ElastiCache `academy-redis` | Validate only(삭제 금지 기본). |
| 9 | **Storage** | R2 + CDN | 레포 밖. 설정만 SSM/.env. |

---

## 2. Canonical 리소스 표

| 유형 | 이름 | 역할 |
|------|------|------|
| Batch CE | academy-video-batch-ce-final, academy-video-ops-ce | Video/Ops Compute Environment |
| Batch Queue | academy-video-batch-queue, academy-video-ops-queue | |
| Batch JobDef | academy-video-batch-jobdef, academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe | |
| EventBridge Rule | academy-reconcile-video-jobs, academy-video-scan-stuck-rate | |
| ASG | academy-messaging-worker-asg, academy-ai-worker-asg | |
| RDS | academy-db | |
| Redis | academy-redis | |
| API | EIP eipalloc-071ef2b5b5bec9428 | |
| Build | Tag Name=academy-build-arm64 | |
| IAM | academy-batch-service-role, academy-batch-ecs-instance-role, academy-batch-ecs-task-execution-role, academy-video-batch-job-role, academy-eventbridge-batch-video-role | |
| SSM | /academy/api/env, /academy/workers/env | |
| ECR | academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu | |

---

## 3. 배포 순서 (Fullstack one-take)

1. Guard(동시 실행 락)
2. Load params.yaml + validate
3. Preflight(AWS identity, region, describe 권한)
4. Drift 계산 → 표 출력
5. (옵션) PruneLegacy 실행
6. Ensure: IAM → Network Validate → RDS/Redis Validate+SG → SSM → ECR → ASG AI/Messaging → Batch CE/Queue → JobDef → EventBridge → API Ensure → Build Ensure
7. Netprobe(Ops Queue → SUCCEEDED 게이트)
8. Evidence 표 출력
9. Lock 해제

---

## 4. 멱등 규칙

- 모든 변경: **Describe → Decision → Update/Create**. 동일 절차 2회 실행 시 2회차는 No-op.
- Batch CE: 없으면 Create. INVALID면 Queue DISABLED → CE DISABLED → Delete → **Wait 삭제** → Create → **Wait VALID/ENABLED** → Queue 재연결.
- ASG: DesiredCapacity **0으로 덮어쓰기 금지**. describe 후 동일 값 유지.
- EventBridge: Rule 있으면 **Target만** put-targets. Rule 삭제 후 재생성 금지.
- JobDef: drift 시에만 새 revision 등록.
- 삭제 후 고정 Sleep 금지. **describe 기반 Wait loop**만 사용.

---

## 5. 완전 청산 모드 (PruneLegacy)

- **대상:** SSOT canonical 리스트에 **없는** academy-* 리소스.
- **삭제 순서:** EventBridge targets → rules → Batch queues → compute env → jobdef deregister(SSOT 외만) → ASG → ECS cluster → IAM → ECR(SSOT 외) → SSM(SSOT 외) → EIP(미연결만).
- **보호:** RDS/Redis 삭제 금지(기본). API EIP allocationId canonical 보호. params.yaml 정의 리소스 보호.
- **-Plan -PruneLegacy:** 삭제 후보 표만 출력, 변경 0.

---

## 6. Drift 규칙

- 구조 비교: CE(instanceTypes, maxvCpus, subnets, securityGroupIds), Queue(priority, computeEnvironmentOrder), JobDef(vcpus, memory), ASG(LaunchTemplate).
- 분류: Updatable | Recreate required | Manual check.
- 출력: `docs/00-SSOT/reports/drift.latest.md`.

---

## 7. Netprobe / Healthcheck 게이트

- **Netprobe:** Ops Queue에 netprobe Job 제출 → status=SUCCEEDED 대기. FAILED/TIMEOUT/RUNNABLE 정체 시 **throw**(배포 실패).
- **API health:** GET /health 200 확인.

---

## 8. Evidence 표 스키마

- 리소스별 필드: `evidence.schema.md` 참조.
- Netprobe jobId/status 포함. 컬럼 고정.

---

## 9. Quickstart (다른 PC 딸깍)

```powershell
git clone <repo> && cd academy
pwsh scripts/v4/bootstrap.ps1
pwsh scripts/v4/deploy.ps1 -Plan
pwsh scripts/v4/deploy.ps1 -PruneLegacy
```

- bootstrap: aws cli, pwsh, 인증, region·권한 확인.
- deploy -Plan: AWS 변경 0, 표/리포트만 출력.

---

## 10. 참조

- **기계 SSOT:** `params.yaml`
- **계약:** `state-contract.md`
- **운영:** `runbook.md`
- **Evidence 컬럼:** `evidence.schema.md`
