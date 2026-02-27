# SSOT v4 구현 요약

**완료 일자:** 2025-02-27  
**진입점:** `scripts/v4/deploy.ps1` | `docs/00-SSOT/SSOT.md` + `params.yaml`

---

## 1. 추가/변경된 파일 목록

### docs/00-SSOT (정식 SSOT 세트)

| 파일 | 역할 |
|------|------|
| **SSOT.md** | 사람용 단일진실 (구성도, Canonical 표, 배포 순서, 멱등, PruneLegacy, Drift, Netprobe, Evidence, Quickstart) |
| **params.yaml** | 기계 SSOT 단일소스 (v4 전용; env/prod.ps1 대체) |
| **state-contract.md** | 멱등/Wait/Netprobe/Evidence/Legacy kill-switch/PruneLegacy 계약 |
| **runbook.md** | 장애·운영 점검 최소 커맨드셋 |
| **evidence.schema.md** | Evidence 표 컬럼 고정 |
| **reports/drift.latest.md** | Drift 표 (deploy 시마다 갱신) |
| **reports/audit.latest.md** | Evidence/감사 (deploy 종료 시 저장) |
| **reports/verify.latest.md** | verify.ps1 결과 (Batch/EventBridge/ASG/API/SSM PASS/FAIL) |
| **reports/history/** | 타임스탬프별 drift/audit/verify 보관 |

### scripts/v4

| 경로 | 역할 |
|------|------|
| **deploy.ps1** | 단일 진입점. -Plan, -PruneLegacy, **-PurgeAndRecreate**, **-DryRun**, -ForceRecreateAll, -SkipNetprobe, -Ci, -EcrRepoUri |
| **bootstrap.ps1** | 새 PC 준비 (UTF-8, aws --version, 인증, region, 최소 describe 테스트, params.yaml) |
| **verify.ps1** | 5단계 검증 + **verify.latest.md** 저장 (Batch/EventBridge/ASG/API/SSM 체크) |
| **core/reports.ps1** | **Save-DriftReport, Save-EvidenceReport, Save-VerifyReport** → docs/00-SSOT/v4/reports/ + history/ |
| **core/guard.ps1** | SSM 락 + **scripts/infra 및 scripts/archive** 실행 시 즉시 fail |
| **core/preflight.ps1** | AWS identity, VPC, SSM, ECR |
| **core/ssot.ps1** | params.yaml 로드 + script 변수 설정 |
| **core/aws.ps1** | Invoke-AwsJson / Invoke-Aws (Plan 시 mutating만 스킵) |
| **core/logging.ps1** | Write-Step/Ok/Warn/Fail |
| **core/wait.ps1** | Wait-CEDeleted, Wait-QueueDeleted 등 |
| **core/diff.ps1** | Drift (전체 목록 + 이름 필터) |
| **core/evidence.ps1** | Get-EvidenceSnapshot, Show-Evidence, Convert-EvidenceToMarkdown |
| **core/prune.ps1** | PruneLegacy + Get-PurgePlan, Invoke-PurgeAndRecreate |
| **plan.ps1** | deploy.ps1 -Plan 래퍼 |
| **resources/*.ps1** | network, iam, ssm, ecr, api, build, rds, redis, asg_ai, asg_messaging, batch, jobdef, eventbridge, netprobe |
| **templates/batch/*.json** | Video/Ops CE·Queue·JobDef (scripts/infra 복사) |
| **templates/iam/*.json** | Trust/Policy (scripts/infra 복사) |
| **templates/eventbridge/*.json** | reconcile/scan_stuck target (scripts/infra 복사) |

### CI

| 파일 | 변경 |
|------|------|
| **.github/workflows/video_batch_deploy.yml** | scripts_v3 → scripts/v4/deploy.ps1, paths에 scripts/v4·docs/00-SSOT 추가, Guard를 전체 워크플로 denylist로 확장 |

---

## 2. SSOT Canonical 리스트 (요약)

- **Batch CE:** academy-video-batch-ce-final, academy-video-ops-ce  
- **Batch Queue:** academy-video-batch-queue, academy-video-ops-queue  
- **Batch JobDef:** academy-video-batch-jobdef, academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe  
- **EventBridge Rule:** academy-reconcile-video-jobs, academy-video-scan-stuck-rate  
- **ASG:** academy-messaging-worker-asg, academy-ai-worker-asg — **Ensure-ASG:** LT drift·Desired/Min/Max drift 시 update 또는 instance-refresh, drift 표 반영  
- **RDS:** academy-db  
- **Redis:** academy-redis  
- **API:** EIP eipalloc-071ef2b5b5bec9428 — **Ensure-API:** Tag Name=academy-api, 없으면 create, health≠200/AMI/SSM drift 시 recreate, EIP·SSM·health 200 확인  
- **Build:** Tag Name=academy-build-arm64 — **Ensure-Build:** 없으면 create, AMI drift 시 recreate, stopped 허용  
- **IAM:** academy-batch-service-role, academy-batch-ecs-instance-role, academy-batch-ecs-task-execution-role, academy-video-batch-job-role, academy-eventbridge-batch-video-role  
- **SSM:** /academy/api/env, /academy/workers/env  
- **ECR:** academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu  

---

## 3. Quickstart (다른 PC에서 딸깍)

```powershell
git clone <repo>
cd academy
pwsh scripts/v4/bootstrap.ps1
pwsh scripts/v4/deploy.ps1 -Plan
pwsh scripts/v4/deploy.ps1 -Plan -PruneLegacy
pwsh scripts/v4/deploy.ps1 -PruneLegacy
pwsh scripts/v4/deploy.ps1
```

- **deploy -Plan:** AWS 변경 0, Drift 표·Evidence 출력·**drift.latest.md / audit.latest.md 저장**
- **deploy -Plan -PruneLegacy:** 삭제 후보 표만 출력
- **deploy -PurgeAndRecreate -DryRun:** SSOT Batch/EventBridge 삭제 예정 목록만 출력 후 종료
- **deploy -PurgeAndRecreate:** SSOT 범위 전부 삭제 후 풀스택 Ensure 재실행
- **deploy -PruneLegacy:** SSOT 외 삭제 후 FullStack Ensure
- **deploy:** 풀스택 Ensure (Batch + **API + ASG + Build**) → Netprobe → Evidence → **audit.latest.md 저장**
- **API/Build/ASG:** **완전 Ensure** (create if missing, AMI/LT/capacity drift 시 recreate 또는 update/instance-refresh). 재실행 시 No-op.
- **verify.ps1:** bootstrap → deploy -Plan → deploy -PruneLegacy → deploy → deploy (No-op) + **verify.latest.md** (Batch/EventBridge/ASG/**API/Build** PASS 조건 포함)

---

## 4. 운영/복구 Runbook

- **Runbook:** [docs/00-SSOT/runbook.md](runbook.md)  
- **Evidence 스키마:** [docs/00-SSOT/evidence.schema.md](evidence.schema.md)  
- **상태 계약:** [docs/00-SSOT/state-contract.md](state-contract.md)  

---

## 5. 기존 문서 이동

- **현재:** 기존 산재 문서(docs/01-ARCHITECTURE, 02-OPERATIONS, 03-REPORTS, 기존 INFRA-SSOT-V3.* 등)는 **이동하지 않음**.  
- **추후:** v4 검증 후 "정리 PR"에서 docs/archive로 이동하거나 링크만 정리할 수 있음.
