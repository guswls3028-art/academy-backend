# Phase 1 — Batch Video Worker 팩트 시트 (Cursor 팩트 수집 결과)

**목적:** 아래 6줄이 증거(코드 위치/로그 문자열)로 채워지면 Phase 2 처방이 바로 나온다.

---

## 출력물 요약 (A)~(F)

| 항목 | 결과 |
|------|------|
| **(A) submit 호출 경로** | video_views.py → video_encoding.create_job_and_submit_batch → batch_submit.submit_batch_job |
| **(B) submit 입력값** | jobQueue=settings.VIDEO_BATCH_JOB_QUEUE, jobDefinition=settings.VIDEO_BATCH_JOB_DEFINITION, parameters={"job_id": job_id}, containerOverrides 없음 |
| **(C) submit 결과 처리** | 반환 aws_job_id는 로그만. None이면 video_encoding에서 job.delete(), video.current_job_id=None 후 None 반환 |
| **(D) 워커 실행 엔트리** | JobDef는 **batch_entrypoint.py가 아님**. `python -m apps.worker.video_worker.batch_main Ref::job_id` 로 **batch_main** 직접 실행 |
| **(E) 워커 실패/종료 패턴** | 성공 시 BATCH_PROCESS_START → BATCH_JOB_COMPLETED, exit 0. 실패 시 BATCH_JOB_FAILED 로그 후 exit 1 |
| **(F) stuck retry** | RUNNING + last_heartbeat_at < 3분 → RETRY_WAIT + submit_batch_job(동일 job.id). attempt≥5 → DEAD. Batch attempts=1과 충돌 없음 |

---

## [Batch Video Worker Fact Sheet] (상세)

### 1) Submit call chain

| 항목 | 값 |
|------|-----|
| Entry endpoint/file | `apps/support/video/views/video_views.py` — L430, L452, L473, L537 에서 `create_job_and_submit_batch(video)` 호출 (upload_complete, retry 등) |
| create_job_and_submit_batch 정의 | `apps/support/video/services/video_encoding.py:L18` |
| submit_batch_job 호출 위치 | `apps/support/video/services/video_encoding.py:L47` → `apps/support/video/services/batch_submit.py:L46` (boto3 client.submit_job) |

### 2) Submit parameters (source of truth)

| 항목 | 값 |
|------|-----|
| VIDEO_BATCH_JOB_QUEUE 읽는 곳 | `apps/api/config/settings/base.py:L348` — `os.getenv("VIDEO_BATCH_JOB_QUEUE", "academy-video-batch-queue")` → default **Y** |
| VIDEO_BATCH_JOB_DEFINITION 읽는 곳 | `apps/api/config/settings/base.py:L349` — `os.getenv("VIDEO_BATCH_JOB_DEFINITION", "academy-video-batch-jobdef")` → default **Y** |
| submit 시 실제 사용 | `batch_submit.py:L33-36` 에서 없으면 ImproperlyConfigured. L42-43 에서 getattr(settings, ..., default) 로 queue_name, job_def_name 사용 |
| Overrides (command/env)? | **N** — containerOverrides 없음. JobDef의 command만 사용 |
| 상세 | `batch_submit.py:L46-51`: jobName=video-{job_id[:8]}, jobQueue=queue_name, jobDefinition=job_def_name, parameters={"job_id": str(video_job_id)} |

### 3) Worker runtime

| 항목 | 값 |
|------|-----|
| Job entrypoint가 batch_entrypoint.py? | **N** — JobDef는 batch_main을 직접 실행함 |
| JobDef 실제 command | `["python", "-m", "apps.worker.video_worker.batch_main", "Ref::job_id"]` (scripts/infra/batch/video_job_definition.json) |
| job_id 전달 | submit_job의 parameters={"job_id": uuid} → Batch가 Ref::job_id를 치환해 argv[1]로 전달. batch_main은 `os.environ.get("VIDEO_JOB_ID") or sys.argv[1]` (batch_main.py:L57) |
| 시작 시 반드시 나와야 할 로그 | `"event":"BATCH_PROCESS_START"` (batch_main.py:L105 _log_json) — 또는 그 전에 JOB_NOT_FOUND / IDEMPOTENT_DONE / IDEMPOTENT_READY |
| 실패 시 exit 경로 | batch_main.py:L143-149 (CancelledError → 1), L142-149 (Exception → job_fail_retry, attempt≥MAX면 job_mark_dead, return 1) |

### 4) Job Definition registration scripts

| 항목 | 값 |
|------|-----|
| register-job-definition 스크립트 | `scripts/infra/batch_video_verify_and_register.ps1` (L79: file Uri로 register). 소스 JSON: `scripts/infra/batch/video_job_definition.json` |
| command/entrypoint 라인 | `"command":["python","-m","apps.worker.video_worker.batch_main","Ref::job_id"]` — batch_entrypoint 미사용 |
| image 태그 전략 | EcrRepoUri 파라미터로 전달, PLACEHOLDER_ECR_URI 치환. 일반적으로 :latest |
| jobRoleArn | **present** — PLACEHOLDER_JOB_ROLE_ARN → academy-video-batch-job-role |
| executionRoleArn | **present** — PLACEHOLDER_EXECUTION_ROLE_ARN → academy-batch-ecs-task-execution-role |
| log group config | `awslogs-group`: /aws/batch/academy-video-worker, `awslogs-region`: PLACEHOLDER_REGION |

### 5) Stuck retry

| 항목 | 값 |
|------|-----|
| stuck 기준 | state=RUNNING 이고 last_heartbeat_at < now - 3분 (scan_stuck_video_jobs.py:L42-46, STUCK_THRESHOLD_MINUTES=3) |
| 재제출 방식 | **동일 job_id** — submit_batch_job(str(job.id)) (L79). 새 VideoTranscodeJob 생성 없음 |
| DB 재시도 제한 | attempt_count 증가 후 attempt_after >= MAX_ATTEMPTS(5) 이면 DEAD (L53-65). 그 미만이면 RETRY_WAIT + submit_batch_job (L72-83) |
| Batch attempts=1과 충돌 | 없음. Batch는 1회만 재시도 안 함. 재시도는 Django scan_stuck_video_jobs가 같은 job_id로 새 Batch job 제출 |

### 6) Legacy confusion points

| 항목 | 값 |
|------|-----|
| check_workers.py가 sqs_main import? | **Y** — L26-27 WORKERS에 ("video_worker", "apps.worker.video_worker.sqs_main", ...), L104 Docker 검증에서도 sqs_main 사용 |
| check_workers.py를 누가 실행? | 문서: docs/archive/cursor_legacy/08-worker-deployment-and-test.md (check_workers.py, --docker). CI/배포 스크립트에서 직접 호출하는지는 미확인 |
| deployment_readiness_check.py | L93 에서 `import apps.worker.video_worker.sqs_main` 사용. docs/운영.md 에서 "검증: python scripts/deployment_readiness_check.py --docker" 로 참조 → **실행 시 Video 검증에서 ImportError 발생** |

---

## 1) 인코딩 submit 경로 (코드 확정)

- **video_views.py**  
  - L430, L452: upload_complete 분기에서 `create_job_and_submit_batch(video)`  
  - L473: 일반 분기에서 동일  
  - L537: retry 분기에서 `job = create_job_and_submit_batch(video)`  
- **video_encoding.py**  
  - L37-44: VideoTranscodeJob 생성, video.current_job_id 설정  
  - L46-50: `submit_batch_job(str(job.id))` 실패 시 job 삭제, current_job_id None, return None  
- **batch_submit.py**  
  - L46-51: boto3.client("batch").submit_job(jobName=..., jobQueue=..., jobDefinition=..., parameters={"job_id": ...})

---

## 2) 설정/환경변수

- **로딩:** base.py L348-349 (os.getenv, default 있음).  
- **필수 여부:** batch_submit.py L33-36 에서 없으면 ImproperlyConfigured.  
- **check_batch_settings:** settings에 VIDEO_BATCH_JOB_QUEUE, VIDEO_BATCH_JOB_DEFINITION 존재·비어있지 않으면 PASS.

---

## 3) 워커 런타임 (batch_main만 타는지)

- JobDef는 **batch_entrypoint.py를 타지 않음.** command가 `batch_main` + Ref::job_id.
- 컨테이너가 정상 기동하면 `_log_json("BATCH_PROCESS_START", ...)` (batch_main.py L105) 또는 그 전 idempotent 로그가 CloudWatch에 남음.
- 실패 시: `logger.exception("BATCH_JOB_FAILED | ...")` (L144) 후 exit 1.

---

## 4) Job Definition 역추적

- **실행 커맨드 1줄:**  
  `python -m apps.worker.video_worker.batch_main <job_id>`  
  (Ref::job_id는 submit 시 parameters job_id로 치환됨)
- jobRoleArn / executionRoleArn: verify 스크립트에서 academy-video-batch-job-role, academy-batch-ecs-task-execution-role 사용 (present).

---

## 5) 재시도/스캔 vs Batch attempts=1

- stuck 기준: RUNNING + last_heartbeat_at < 3분.
- 재제출: 동일 job.id로 submit_batch_job (새 Batch job, 동일 DB row).
- DB 제한: attempt_count >= 5 → DEAD.  
- Batch retryStrategy.attempts=1 → Batch 자체 재시도 없음. Django만 재제출.

---

## 6) 레거시로 인한 장애 가능성

- **check_workers.py** — `python scripts/check_workers.py` 또는 `--docker` 시 Video 항목에서 sqs_main import → **실패.**
- **deployment_readiness_check.py** — `--docker` 시 academy-video-worker 이미지로 sqs_main import → **실패.**  
- 두 스크립트가 배포/운영 문서에서 호출되면 “배치 전환 실패”로 오해될 수 있음.
