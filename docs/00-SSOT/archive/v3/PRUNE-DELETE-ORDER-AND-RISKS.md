# PruneLegacy 삭제 순서 및 리스크

**진입:** `.\scripts_v3\deploy_fullstack.ps1 -DryRun` → DELETE CANDIDATE 표만 출력.  
**실행:** `.\scripts_v3\deploy_fullstack.ps1 -PruneLegacy` → 삭제 후 FullStack Ensure → Netprobe → Evidence.

---

## 1. SSOT Canonical 리스트 (삭제 대상 아님)

| 유형 | 개수 | 이름(예시) |
|------|------|------------|
| Batch CE | 2 | academy-video-batch-ce-final, academy-video-ops-ce |
| Batch Queue | 2 | academy-video-batch-queue, academy-video-ops-queue |
| Batch JobDef | 4 | academy-video-batch-jobdef, academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe |
| EventBridge Rule | 2 | academy-reconcile-video-jobs, academy-video-scan-stuck-rate |
| ASG | 2 | academy-messaging-worker-asg, academy-ai-worker-asg |
| RDS | 1 | academy-db |
| Redis | 1 | academy-redis |
| API EC2 | 1 | EIP eipalloc-071ef2b5b5bec9428 로 식별 |
| Build EC2 | 1 | Tag Name=academy-build-arm64 |
| IAM Role | 5 | academy-batch-service-role, academy-batch-ecs-instance-role, academy-batch-ecs-task-execution-role, academy-video-batch-job-role, academy-eventbridge-batch-video-role |
| SSM | 2 | /academy/api/env, /academy/workers/env |
| ECR | 4 | academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu |
| EIP | 1 | eipalloc-071ef2b5b5bec9428 |

위에 **없는** 리소스가 DELETE CANDIDATE.

---

## 2. 삭제 순서 다이어그램

**순서:** EventBridge → Queue → CE → JobDef → **ASG** → **ECS cluster** → **IAM** → EIP  
**대기:** 각 delete 후 **describe 폴링 Wait**만 사용. 고정 sleep 금지.

```
[EventBridge Rule]  ── remove-targets ──► delete-rule ── Wait-EventBridgeRuleDeleted
        │
        ▼
[Batch Job Queue]   ── state=DISABLED ──► delete-job-queue ── Wait-QueueDeleted
        │
        ▼
[Batch CE]          ── state=DISABLED ──► delete-compute-environment ── Wait-CEDeleted
        │
        ▼
[Batch JobDef]      ── deregister-job-definition (SSOT 외 이름만)
        │
        ▼
[ASG]               ── min=0 desired=0 ──► force-delete ── Wait-ASGDeleted
        │
        ▼
[ECS Cluster]       ── delete-cluster ── Wait-ECSClusterDeleted
        │
        ▼
[IAM Role]          ── detach / delete inline / remove from instance-profile ──► delete-role ── Wait-IAMRoleDeleted
        │
        ▼
[EIP]               ── release-address (미연결만)
```

**의존성:** EventBridge가 Queue를 target 하므로 Rule 먼저 제거 → Queue 사용 중단 후 Queue 삭제 → CE 사용 중단 후 CE 삭제. JobDef는 CE/Queue 삭제 후 deregister. **ASG**는 독립적으로 min=0 후 삭제. **ECS** 클러스터 삭제 후 **IAM** 역할 삭제(참조 해제 후). EIP는 마지막(미연결만 release). 모든 삭제 후 대기는 describe 폴링만 사용.

---

## 3. 리스크 요약

| 단계 | 리스크 | 완화 |
|------|--------|------|
| EventBridge | 스케줄 잔재 제거 시 해당 rule로 동작하던 Batch 호출 중단 | Canonical 2개(academy-reconcile-video-jobs, academy-video-scan-stuck-rate)는 유지. 삭제 대상은 SSOT 외 rule만. |
| Queue | 삭제 시 해당 큐에 대기 중인 Job 실패 | Prune 전에 RUNNABLE/STARTING Job 완료 대기 권장. Canonical 2개는 유지. |
| CE | 삭제 중인 CE에서 실행 중인 Job 실패 가능 | DISABLED 후 삭제. Canonical 2개는 유지. |
| JobDef | deregister 시 해당 revision 사용 중인 Job 실패 | **SSOT 외 이름**만 삭제(예: academy-video-ops-jobdef 등 구 이름). Canonical 4개 이름의 구 revision은 유지(삭제 안 함). |
| IAM | academy-* 역할 삭제 시 해당 역할을 쓰는 리소스 오류 | Canonical 5개만 유지. **API/Build가 academy-ec2-role 등 academy-*를 쓰면** SSOT_IAMRoles에 포함되지 않아 DELETE CANDIDATE가 됨. 제외하려면 core/ssot_canonical.ps1의 SSOT_IAMRoles에 추가. |
| ASG | force-delete 시 인스턴스 강제 종료 | Batch CE용 ASG는 이름 패턴(*academy-video-batch-ce-final*, *academy-video-ops-ce*)으로 제외. Messaging/AI 외 academy ASG만 대상. |
| ECS | delete-cluster 실패 가능 | Batch 소유 클러스터는 삭제 불가(에러 무시). 수동 생성 클러스터만 삭제. |
| EIP | release 시 해당 IP 재사용 불가 | **연결된 EIP는 목록에서 제외.** 미연결만 release. |

---

## 4. Drift 표 (구조 비교)

`-DryRun` 시 **Get-StructuralDrift** / **Show-StructuralDriftTable** 로 SSOT 기준 Expected vs Actual 비교.

| ResourceType | Name | Expected | Actual | Action |
|--------------|------|----------|--------|--------|
| Batch CE | academy-video-batch-ce-final | instanceTypes=c6g.large, maxvCpus=32 | (실제 값) | NoOp / Recreate |
| Batch Queue | academy-video-batch-queue | priority=1, order | (실제) | NoOp / Recreate |
| Batch JobDef | academy-video-batch-jobdef | vcpus=2 memory=3072 | (실제) | NoOp / Recreate |

- **CE:** instanceTypes, maxvCpus, subnets, securityGroupIds
- **Queue:** computeEnvironmentOrder, priority
- **JobDef:** vcpus, memory (최신 ACTIVE revision)
- **ASG:** LaunchTemplate 존재, Min/Max

Drift 시 Ensure 단계에서 delete+recreate 또는 update로 수렴.

---

## 5. FullStack 멱등 증명

1. **1회차:** `.\scripts_v3\deploy_fullstack.ps1` 실행 → 리소스 생성/수정 시 `ChangesMade` 플래그 설정.
2. **2회차:** 동일 명령 재실행 → 변경 없으면 **"Idempotent: No changes required."** 만 출력.
3. **증명 로그:** 2회차 stdout을 저장해 두면 멱등 검증 완료.
   ```powershell
   .\scripts_v3\deploy_fullstack.ps1 2>&1 | Tee-Object -FilePath deploy_run2.log
   # deploy_run2.log 끝에 "Idempotent: No changes required." 포함 확인
   ```

---

## 6. 실행 예시

```powershell
# 삭제 대상만 확인 (변경 없음)
.\scripts_v3\deploy_fullstack.ps1 -DryRun

# SSOT 외 삭제 후 전체 Ensure + Netprobe + Evidence
.\scripts_v3\deploy_fullstack.ps1 -PruneLegacy -SkipNetprobe
```
