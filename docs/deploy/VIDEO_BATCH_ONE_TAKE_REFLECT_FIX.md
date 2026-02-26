# 1) 검색 결과 인덱스 (파일 경로 목록)

**Video 인프라 스펙·리소스 이름·성공 조건의 최종 SSOT:** [VIDEO_WORKER_INFRA_SSOT_V1.md](VIDEO_WORKER_INFRA_SSOT_V1.md)

## academy-video-job-def / jobDefinition / revision / vcpus / memory / 3072 / c6g / instanceTypes
- `docs/deploy/VIDEO_BATCH_PRODUCTION_FIX_FINAL.md`
- `docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md`
- `scripts/infra/video_batch_production_one_take.ps1`
- `scripts/infra/batch/video_job_definition.json`
- `scripts/infra/batch/video_compute_env.json`

## VIDEO_BATCH_JOB_QUEUE / OpsJobQueueName / JobQueueName / academy-video-ops-queue / academy-video-batch-queue
- `docs/deploy/VIDEO_BATCH_PRODUCTION_FIX_FINAL.md`
- `docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md`
- `scripts/infra/video_batch_production_one_take.ps1`
- `scripts/infra/batch_video_setup.ps1`
- `scripts/infra/eventbridge_deploy_video_scheduler.ps1`
- `scripts/infra/one_shot_video_ce_final.ps1`
- `scripts/infra/reconcile_video_batch_production.ps1`
- `scripts/infra/verify_video_batch_ssot.ps1`
- `docs/deploy/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md`
- `docs/AI_BATCH_WORKER_VS_OPS.md`

## reconcile_batch_video_jobs / academy-reconcile-video-jobs / scan_stuck / netprobe
- `docs/deploy/VIDEO_BATCH_PRODUCTION_FIX_FINAL.md`
- `docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md`
- `scripts/infra/video_batch_production_one_take.ps1`
- `scripts/infra/batch_video_setup.ps1`
- `scripts/infra/eventbridge_deploy_video_scheduler.ps1`
- `scripts/infra/reconcile_video_batch_production.ps1`
- `scripts/infra/verify_video_batch_ssot.ps1`
- `scripts/infra/infra_one_take_full_audit.ps1`
- `scripts/fix_batch_runnable_orphan_one_take.ps1`
- `scripts/diagnose_batch_deep.ps1`
- `apps/support/video/management/commands/reconcile_batch_video_jobs.py`

## desiredvCpus / maxvCpus / computeEnvironmentOrder / academy-video-batch-ce-final / ce-v2 / ce-v3 / ce-public
- `docs/deploy/VIDEO_BATCH_PRODUCTION_FIX_FINAL.md`
- `docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md`
- `scripts/infra/video_batch_production_one_take.ps1`
- `scripts/infra/batch_video_setup.ps1`
- `scripts/infra/one_shot_video_ce_final.ps1`
- `scripts/infra/reconcile_video_batch_production.ps1`

## VIDEO_BATCH_JOB_DEFINITION / submit / job_def_name
- `apps/api/config/settings/base.py` (L353)
- `apps/support/video/services/batch_submit.py` (L36-44, L55-58)
- `scripts/infra/ssm_bootstrap_video_worker.ps1`
- `.env.example`
- 기타 문서 다수

---

# 2) 반영 실패 원인 확정 (A~E)

## A. submit 쪽이 jobDefinition을 ':revision'으로 하드코딩하는지
**X.**  
- `apps/support/video/services/batch_submit.py` L44: `job_def_name = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "academy-video-batch-jobdef")`  
- L58: `jobDefinition=job_def_name` — 이름만 전달.  
- `base.py` L353: `VIDEO_BATCH_JOB_DEFINITION = os.getenv("VIDEO_BATCH_JOB_DEFINITION", "academy-video-batch-jobdef")`  
- 레포 전역에 `:revision` 또는 revision 숫자 붙여서 쓰는 코드 없음. 반영 안 됨 원인은 여기 아님.

## B. recreate_batch_in_api_vpc.ps1 또는 다른 스크립트가 instanceTypes를 넓게 쓰는지
**O (부분).**  
- `recreate_batch_in_api_vpc.ps1`는 `batch_video_setup.ps1` 호출 시 `-ComputeEnvName academy-video-batch-ce` 사용.  
- `batch_video_setup.ps1`는 `scripts/infra/batch/video_compute_env.json` 사용 — JSON에는 `"instanceTypes":["c6g.large"]` 만 있음.  
- 따라서 **새로 CE를 만드는 경로는 c6g.large만** 사용.  
- **반영 실패 원인:** `reconcile_video_batch_production.ps1` L163: `update-compute-environment`에 `minvCpus=0,maxvCpus=32`만 넘기고 **instanceTypes는 변경하지 않음**. 이미 콘솔/과거 스크립트로 xlarge·2xlarge가 들어간 CE는 그대로 유지됨.  
- 결론: **기존 CE의 instanceTypes를 좁히는 코드가 없어서** “2 vCPU/3072 반영해도 1인스턴스에 2 job”이 유지될 수 있음.

## C. Queue가 단일 CE가 아니라 CE 여러 개를 바라보게 업데이트되는 루트가 있는지
**X.**  
- `batch_video_setup.ps1`에서 Queue update 실패 시 **fallback으로 `academy-video-batch-queue-ce` 생성하던 분기**는 이미 제거됨 (실패 시 exit 1).  
- `reconcile_video_batch_production.ps1`, `video_batch_production_one_take.ps1`, `one_shot_video_ce_final.ps1`는 모두 **computeEnvironmentOrder를 단일 CE ARN으로** 설정.  
- Queue를 여러 CE에 연결하도록 바꾸는 코드 없음.

## D. EventBridge rule이 이전 실행 완료 여부를 안 보고 계속 submit해서 겹치는지 + 겹침 막는 락이 있는지
**O (겹침) + O (락 있음).**  
- EventBridge: `eventbridge_deploy_video_scheduler.ps1`에서 reconcile 규칙을 **rate(15 minutes)** 로 이미 변경됨.  
- 규칙은 **주기마다 SubmitJob만 호출**하며, 이전 reconcile job 완료 여부는 보지 않음 → RUNNABLE이 여러 개 쌓일 수 있음.  
- **락:** `reconcile_batch_video_jobs.py` L46-58 `_acquire_reconcile_lock()`, L154-161 `handle()` 진입 시 Redis `video:reconcile:lock` SETNX TTL=600s. 락 실패 시 skip.  
- 결론: **겹침 원인** = EventBridge가 주기마다 무조건 제출. **완화** = 주기 15분 + reconcile 코드 내 Redis 락으로 동시 실행 1개로 제한.

## E. audit/FixMode/one_shot 스크립트가 재실행 시 새 CE를 만들거나 Queue를 다시 엮어서 증식하는지
**X.**  
- `infra_one_take_full_audit.ps1` FixMode (`Invoke-FixMode`): EventBridge put-rule/put-targets, Ops 큐 job terminate, IAM 스크립트 호출만 함. **CE/Queue create 호출 없음.**  
- `one_shot_video_ce_final.ps1`: 기존 CE DISABLED 후 `academy-video-batch-ce-final` 생성은 **없을 때만** 수행. 이미 있으면 스킵.  
- `batch_video_ce_horizontal_scale.ps1`는 별도 스케일 용도로 새 CE를 만들 수 있으나, audit/one_shot 재실행과 무관.  
- **CE/ASG 증식**은 과거에 **다른 CE 이름**(v2/v3/public/queue-ce) 사용 + **batch_video_setup의 fallback 큐 생성**이 원인이었고, fallback 제거로 재발 방지됨.

---

# 3) 원테이크 실행 스크립트

**파일:** `scripts/infra/video_batch_production_one_take.ps1`  
SSOT 이름: VideoQ=`academy-video-batch-queue`, OpsQ=`academy-video-ops-queue`, VideoCE=`academy-video-batch-ce-final`, OpsCE=`academy-video-ops-ce`, Video JobDef=`academy-video-batch-jobdef`.  
(레포 SSOT는 `academy-video-batch-jobdef` — `academy-video-job-def` 아님.)

이미 구현된 스크립트가 위 경로에 있으며, 다음이 적용돼 있음:
- EventBridge reconcile: rate(15 minutes), DISABLED로 put-rule.
- Video CE: 없으면 기존 academy-video-batch* CE에서 VPC/역할 복제 후 c6g.large만으로 생성; 있으면 instanceTypes 검사만(API는 instanceTypes 변경 불가).
- Video Queue: computeEnvironmentOrder = 해당 Video CE 1개만.
- Video JobDef: 최신 ACTIVE가 2 vCPU/3072/14400이 아니면 기존 최신 기준으로 재등록(3072/2/14400).
- EventBridge put-targets: Ops 큐, JobDefinition 이름만(academy-video-ops-reconcile).
- Evidence: CE instanceTypes, Queue computeEnvironmentOrder, EventBridge rule state/schedule, **JobDef 최신 revision vcpus/memory**.

PowerShell 코드는 해당 파일을 그대로 사용. (이미 앞 단계에서 Evidence에 JobDef 출력 추가됨.)

---

# 4) PR 변경 요약 (diff 요약)

| 파일 | 변경 |
|------|------|
| `scripts/infra/video_batch_production_one_take.ps1` | Evidence에 JobDef 최신 revision vcpus/memory 출력 추가. |
| `scripts/infra/infra_one_take_full_audit.ps1` | Reconcile 규칙 기대 스케줄을 `rate(5 minutes)` → `rate(15 minutes)` 로 변경. ScanStuck은 `rate(5 minutes)` 유지. FixMode 시 reconcile이 15분으로 고정되도록. |

**이미 반영된 항목 (이전 PR):**
- `scripts/infra/batch/video_job_definition.json`: memory 3072.
- `scripts/infra/eventbridge_deploy_video_scheduler.ps1`: reconcile rate(15 minutes).
- `scripts/infra/batch_video_setup.ps1`: Queue update 실패 시 fallback 큐 생성 제거, exit 1.
- `apps/support/video/services/video_encoding.py`: `create_job_and_submit_batch`에서 video `select_for_update` 후 active job 재확인.

**반영 위치 요약:**
- **Submit 경로 revision 하드코딩 제거:** 해당 없음(이미 이름만 사용). 추가 변경 없음.
- **instanceTypes 고정:** `video_batch_production_one_take.ps1`에서 Video CE 생성 시 `instanceTypes=c6g.large`만 사용; 기존 CE는 API 제약으로 스크립트에서 변경 불가 → 신규 CE만 보장.
- **reconcile 겹침 방지:** EventBridge reconcile `rate(15 minutes)` + `reconcile_batch_video_jobs.py` Redis 락 유지. audit 기대값을 15분으로 변경해 FixMode가 5분으로 되돌리지 않도록 함.

**실행 순서:**
1. `.\scripts\infra\video_batch_production_one_take.ps1 -Region ap-northeast-2`
2. (선택) EventBridge 규칙 활성화: `aws events put-rule --name academy-reconcile-video-jobs --schedule-expression "rate(15 minutes)" --state ENABLED --region ap-northeast-2`
3. (선택) `.\scripts\infra\reconcile_video_batch_production.ps1 -Region ap-northeast-2 -VideoCEName academy-video-batch-ce-final`
