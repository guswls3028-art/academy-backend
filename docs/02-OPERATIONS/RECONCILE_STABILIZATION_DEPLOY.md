# Reconcile 안정화 — 변경 요약 및 배포/롤백 가이드

Video Batch Reconcile 폭주/오판 방지를 위한 변경 사항 요약, 배포 순서, 롤백 순서이다.  
기존 video 인코딩 로직(batch_main, batch_submit, video_encoding 등) 및 DB 스키마는 변경하지 않았으며, 모든 스크립트는 idempotent하다.

---

## 1. 변경 파일 목록

### 코드 (앱)

| 파일 | 변경 내용 |
|------|-----------|
| `apps/support/video/management/commands/reconcile_batch_video_jobs.py` | Redis 단일 락(`video:reconcile:lock`), DescribeJobs 실패 시 DB 변경 없음, not_found 보수 판정(연속 3회 또는 30분 초과 시에만 fail), 구조화 로그, `--skip-lock` 옵션 |

### 인프라 스크립트

| 파일 | 변경 내용 |
|------|-----------|
| `scripts/infra/batch_ops_setup.ps1` | 기존 유지. Ops CE/Queue 생성(있으면 스킵). |
| `scripts/infra/batch/ops_compute_env.json` | 기존 유지. t4g.small, min=0, max=2. |
| `scripts/infra/batch/ops_job_queue.json` | 기존 유지. |
| `scripts/infra/eventbridge_deploy_video_scheduler.ps1` | 기존 유지. rate(5 minutes), target=Ops queue. |
| `scripts/infra/iam_attach_batch_describe_jobs.ps1` | reconcile job definition에서 jobRoleArn 자동 추출 후 해당 role에 Managed Policy 부착. 없으면 academy-video-batch-job-role 사용. |
| `scripts/infra/infra_one_take_full_audit.ps1` | Region 기본값(aws configure get region), Batch/EventBridge/IAM/JobDef 진단, Ops CE instanceTypes·min/max 검사, revision pinning WARN, FixMode 시 자동 수정, 권장 다음 조치 출력. |

### 문서

| 파일 | 변경 내용 |
|------|-----------|
| `docs/video/reconcile_reconciliation_smoke_test.md` | **신규.** Reconcile 락/스킵/--skip-lock 로컬 스모크 테스트 가이드. |
| `docs/video/RECONCILE_STABILIZATION_VERIFICATION_COMMANDS.md` | **신규.** 운영 검증 체크 커맨드(RUNNING reconcile 수, AccessDenied, SUCCEEDED→READY, Ops CE scale down). |
| `docs/video_batch_production_runbook.md` | 원테이크 운영 점검(3b) 섹션 및 검증 문서 링크 추가. |
| `docs/RECONCILE_STABILIZATION_DEPLOY.md` | **본 문서.** 변경 요약, 배포/롤백 순서. |

---

## 2. 배포 순서

아래 순서로 적용하면 의존성 충돌 없이 반영된다.  
모든 PowerShell은 **저장소 루트**에서 실행하며, 필요 시 `$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()` 설정 후 실행한다.

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$Region = "ap-northeast-2"   # 사용 중인 리전으로 변경

# 1) Ops CE + Ops Queue (없으면 생성, 있으면 스킵)
.\scripts\infra\batch_ops_setup.ps1 -Region $Region

# 2) IAM: reconcile job role에 DescribeJobs/ListJobs 정책 부착
.\scripts\infra\iam_attach_batch_describe_jobs.ps1 -Region $Region

# 3) EventBridge: reconcile/scan-stuck rule rate(5분), target=Ops queue
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region $Region -OpsJobQueueName academy-video-ops-queue

# 4) 원테이크 감사 (ReadOnly) — 이상 있으면 -FixMode로 재실행 가능
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region

# 5) 코드 배포: Django 앱 배포 (reconcile_batch_video_jobs.py 포함)
#    - API/Worker 이미지 빌드·배포 또는 코드만 배포하는 방식에 맞게 수행
#    - Batch job definition은 기존 academy-video-ops-reconcile 사용 (이미지만 갱신하면 됨)
```

**검증 (배포 후):**

```powershell
# 감사 한 번 더
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region

# RUNNING reconcile 1개 이하, AccessDenied 없음 등은 아래 문서 참고
# docs/video/RECONCILE_STABILIZATION_VERIFICATION_COMMANDS.md
```

---

## 3. 롤백 순서

문제 발생 시 **역순**으로 롤백한다. 코드를 먼저 되돌리고, 인프라는 필요 시에만 조정한다.

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$Region = "ap-northeast-2"

# 1) 코드 롤백
#    - reconcile_batch_video_jobs.py 이전 버전으로 되돌리거나, 배포 파이프라인에서 이전 리비전 배포
#    - Redis 락/not_found 로직이 없던 버전으로 돌리면 폭주 가능성은 다시 생김 (가급적 코드 롤백은 짧은 기간만)

# 2) EventBridge (선택) — rate를 다시 2분으로 하거나 rule 비활성화하고 싶을 때만
#    aws events put-rule --name academy-reconcile-video-jobs --schedule-expression "rate(2 minutes)" --state ENABLED --region $Region
#    aws events put-rule --name academy-video-scan-stuck-rate --schedule-expression "rate(2 minutes)" --state ENABLED --region $Region
#    (target을 Video queue로 바꾸려면 eventbridge_deploy 스크립트를 수정해 실행하거나, 수동 put-targets)

# 3) IAM (선택) — 정책 detach만 필요할 때
#    aws iam detach-role-policy --role-name academy-video-batch-job-role --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/AcademyAllowBatchDescribeJobs
#    ACCOUNT_ID: aws sts get-caller-identity --query Account --output text

# 4) Ops 자원 사용 중지 (가장 마지막, 필요 시에만)
#    - Ops queue를 사용 중지하거나 삭제하면 reconcile/scan_stuck이 더 이상 실행되지 않음
#    - Queue 비활성: aws batch update-job-queue --job-queue academy-video-ops-queue --state DISABLED --region $Region
#    - CE 비활성: aws batch update-compute-environment --compute-environment academy-video-ops-ce --state DISABLED --region $Region
#    주의: 비활성화 후에도 기존 RUNNING job은 끝날 때까지 동작함. 완전 제거는 queue/ce 삭제(사용 안 함 권장).
```

**요약:**

| 순서 | 항목 | 조치 |
|------|------|------|
| 1 | 코드 | reconcile_batch_video_jobs.py 이전 리비전 배포 |
| 2 | EventBridge | 필요 시 schedule/target 수동 또는 스크립트로 복구 |
| 3 | IAM | 필요 시 AcademyAllowBatchDescribeJobs detach |
| 4 | Ops | 필요 시에만 Ops queue/CE DISABLED (가급적 유지) |

---

## 4. 원테이크 적용/검증/롤백 블록

**적용 (한 번에 복붙):**

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$Region = "ap-northeast-2"
.\scripts\infra\batch_ops_setup.ps1 -Region $Region
.\scripts\infra\iam_attach_batch_describe_jobs.ps1 -Region $Region
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region $Region -OpsJobQueueName academy-video-ops-queue
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region
# 이후 앱/코드 배포 진행
```

**검증:**

```powershell
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region
# Result: PASS 확인. FAIL/WARN이면 권장 다음 조치에 따라 수정 후 -FixMode 또는 수동 조치.
# 상세 검증: docs/video/RECONCILE_STABILIZATION_VERIFICATION_COMMANDS.md
```

**롤백 (코드 먼저, 인프라는 필요 시):**

```powershell
# 1) 코드만 이전 버전으로 배포
# 2) (선택) EventBridge schedule/target 복구
# 3) (선택) aws iam detach-role-policy --role-name academy-video-batch-job-role --policy-arn arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/AcademyAllowBatchDescribeJobs
# 4) (선택) Ops queue/CE DISABLED
```
