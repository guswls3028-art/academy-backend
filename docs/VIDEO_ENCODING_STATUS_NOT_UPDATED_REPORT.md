# Video 인코딩 완료 후 상태 미전이 이슈 보고서 (2차 — 팩트 확보용)

## 1. 현상 요약

| 항목 | 상태 |
|------|------|
| 프론트 | "인코딩 중", 업로드 7/7 완료, 전체 진행률 95%, 시청 가능으로 전환 안 됨 |
| DB `Video.status` | `UPLOADED` 유지 |
| DB `Video.current_job_id` | 존재 (예: `155f1003-734e-4a27-a95e-3f961e45cdab`) |
| 인코딩/R2 업로드 | 완료된 것으로 관측 |
| **상태 전이** | **발생하지 않음** (UPLOADED → RUNNING → READY 미발생) |

**정상 흐름:** `UPLOADED` → (Job 생성) → Job `RUNNING` → (인코딩 완료) → `job_complete` → **Video.status = READY**, Job.state = SUCCEEDED  
**현재:** `UPLOADED` → (변화 없음)

---

## 2. VideoTranscodeJob 모델 필드 목록

**소스:** `apps/support/video/models.py` (VideoTranscodeJob 클래스)

| 필드 | 타입 | 비고 |
|------|------|------|
| `id` | UUIDField (PK) | default=uuid.uuid4, editable=False |
| `video` | ForeignKey(Video) | CASCADE |
| `tenant_id` | PositiveIntegerField | db_index=True |
| `state` | CharField(20) | choices=State.choices, default=QUEUED, db_index |
| `attempt_count` | PositiveIntegerField | default=1 |
| `cancel_requested` | BooleanField | default=False |
| `locked_by` | CharField(64) | blank |
| `locked_until` | DateTimeField | null, blank |
| `last_heartbeat_at` | DateTimeField | null, blank |
| `error_code` | CharField(64) | blank |
| `error_message` | TextField | blank |
| `aws_batch_job_id` | CharField(256) | blank, db_index |
| `created_at` | DateTimeField | auto_now_add=True |
| `updated_at` | DateTimeField | auto_now=True |

**State choices:** QUEUED, RUNNING, SUCCEEDED, FAILED, RETRY_WAIT, DEAD, CANCELLED

**조사 시 확인할 DB 값 (해당 job_id 기준):**

- `VideoTranscodeJob.state` — QUEUED 유지인지, RUNNING/SUCCEEDED로 바뀌었는지
- `VideoTranscodeJob.last_heartbeat_at` — Worker가 heartbeat 호출했으면 갱신됨
- `VideoTranscodeJob.aws_batch_job_id` — Batch Job ID 매칭용
- `Video.hls_path`, `Video.duration`, `Video.status` — job_complete 호출 시 READY로 변경됨

---

## 3. Worker CloudWatch 로그 — 초기 환경 출력

Batch 컨테이너는 **entrypoint**로 `batch_entrypoint.py`를 사용하며, 그 다음 `batch_main`이 실행된다.

### 3.1 batch_entrypoint (stderr)

**로그 그룹:** `/aws/batch/academy-video-worker`  
**스트림 접두사:** `batch/default/` (워커), `ops/` (reconcile/scan_stuck)

**반드시 확인할 초기 로그:**

| 로그 라인 | 의미 |
|-----------|------|
| `Loaded SSM JSON with N keys` | SSM에서 N개 키 로드 성공. **N이 19 등으로 기대값과 비슷한지** 확인. |
| `DJANGO_SETTINGS_MODULE = apps.api.config.settings.worker` | Worker용 설정 모듈 사용 중. **다른 값이면 DB/Redis 설정이 API와 다를 수 있음.** |
| `batch_entrypoint: missing required env keys: [...]` | 필수 키 누락 시 **batch_main 실행 전에 exit 1** → 인코딩 자체가 안 돌 수 있음. |
| `batch_entrypoint: SSM fetch failed: ...` | SSM 조회 실패 → 컨테이너 즉시 종료. |

**필수 키 (REQUIRED_KEYS):**  
`AWS_DEFAULT_REGION`, `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`,  
`R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`, `R2_VIDEO_BUCKET`,  
`API_BASE_URL`, `INTERNAL_WORKER_TOKEN`, `REDIS_HOST`, `REDIS_PORT`, `DJANGO_SETTINGS_MODULE`

**2차 확인용 (batch_main 직후, Django 설정 로드 후):**

- `DB_HOST` 등이 API 서버와 동일한지 확인하려면, batch_main 쪽에서 **DB 연결 후 쿼리 결과**를 로그하는 부분이 있는지 확인.  
  (현재 코드에는 `job_get_by_id` 성공 시 별도 “DB connection OK” 로그는 없음. `BATCH_PROCESS_START` 전에 예외가 나면 스택트레이스로 DB/설정 오류 추정 가능.)

### 3.2 batch_main (구조화 로그)

**확인할 이벤트 순서:**

1. `BATCH_PROCESS_START` — job_id, tenant_id, video_id, aws_batch_job_id  
   - **이전에** `JOB_ALREADY_TAKEN` 이 나오면 → `job_set_running(job_id)` 가 False (다른 프로세스가 이미 RUNNING으로 잡음, 또는 state가 QUEUED/RETRY_WAIT가 아님).
2. 그 다음 정상 완료 시:
   - `BATCH_JOB_COMPLETED` — job_id, duration_sec 등  
   - **이전에** `job_complete(job_id, hls_path, duration)` 호출됨 (repositories_video.py).
3. 실패 시:
   - `BATCH_JOB_FAILED` + 스택트레이스  
   - `job_fail_retry` 호출 → Video.status 는 그대로, Job만 RETRY_WAIT.

**상태 미전이 시나리오별로 볼 것:**

- **job_set_running 실패:**  
  - `JOB_ALREADY_TAKEN` 로그 있음 (batch_main.py L136–137).  
  - DB에서 해당 job의 `state`가 이미 RUNNING이거나 QUEUED/RETRY_WAIT가 아님.
- **job_complete 호출 전 예외:**  
  - `BATCH_JOB_COMPLETED` 없고, `BATCH_JOB_FAILED` + 예외 메시지 있음.  
  - `process_video()` 내부 또는 그 직후에서 예외 → `job_complete` 미호출 → Video.status 유지.
- **job_complete 내부 실패:**  
  - `job_complete` 가 False 반환 → batch_main에서 `RuntimeError` 발생 → `BATCH_JOB_FAILED` 로그.  
  - repositories_video.py 의 `job_complete` 반환값: `(False, "job_not_found" | "job_already_succeeded" | "job_not_runnable" | "video_not_found")` 등.

---

## 4. Batch Job 최종 상태 확인 (SUCCEEDED 여부)

**해당 Video의 `current_job_id`(UUID)로 DB에서 `aws_batch_job_id` 조회 후 사용.**

### 4.1 AWS CLI (PowerShell)

```powershell
$Region = "ap-northeast-2"
$JobId = "<aws_batch_job_id from VideoTranscodeJob.aws_batch_job_id>"

aws batch describe-jobs --jobs $JobId --region $Region --output json
```

**확인할 필드:**

- `jobs[0].status` — `SUCCEEDED` / `FAILED` / `RUNNING` / …
- `jobs[0].statusReason` — 실패 시 사유.
- `jobs[0].container.exitCode` — 0이면 정상 종료.
- `jobs[0].container.logStreamName` — CloudWatch 로그 스트림 (위 3절 로그 검색용).

### 4.2 job_id(UUID)만 있을 때

DB에서 해당 job의 `aws_batch_job_id` 를 조회한 뒤 위처럼 `describe-jobs` 실행.

```sql
-- 예시 (Django shell 또는 DB 클라이언트)
SELECT id, state, aws_batch_job_id, updated_at, last_heartbeat_at
FROM video_videotranscodejob
WHERE id = '155f1003-734e-4a27-a95e-3f961e45cdab';
```

---

## 5. 상태 전이 경로 (코드 기준)

| 단계 | 위치 | 동작 |
|------|------|------|
| Job 생성 | video_encoding.py | VideoTranscodeJob 생성 (state=QUEUED), video.current_job_id 저장, submit_batch_job |
| QUEUED → RUNNING | repositories_video.py `job_set_running` | pk=job_id, state in (QUEUED, RETRY_WAIT) 인 행만 update → state=RUNNING, last_heartbeat_at 등 설정. **성공 시 1행 갱신.** |
| RUNNING 유지 | batch_main.py | heartbeat 스레드가 `job_heartbeat` 주기 호출 (last_heartbeat_at 갱신). **Video.status는 이때 변경하지 않음.** |
| RUNNING → SUCCEEDED + Video READY | repositories_video.py `job_complete` | transaction.atomic() 내에서 job, video select_for_update 후 video.hls_path, duration, **video.status=READY**, job.state=SUCCEEDED 저장. Redis 캐시 갱신. |

**중요:**  
- **Video.status를 READY로 바꾸는 유일한 코드 경로**는 `job_complete()` 내부 (repositories_video.py L706) 이다.  
- **Video.status를 RUNNING으로 바꾸는 코드는 없다.** (Batch 경로에서는 Job만 RUNNING, Video는 UPLOADED 유지 후 READY로만 전환.)  
- 따라서 “상태 미전이” = **job_complete 가 호출되지 않았거나**, 호출되었으나 **해당 트랜잭션이 API가 보는 DB에 커밋되지 않은 경우**로 압축된다.

---

## 6. 1차 결론 및 2차 조사 체크리스트

**결론:**  
Worker의 **상태 전이 로직(job_set_running / job_complete)** 이 실행되지 않았거나, 실행되었으나 **API와 동일한 DB에 반영되지 않은** 문제로 보는 것이 타당하다.

**2차 조사 시 확보할 팩트:**

1. **VideoTranscodeJob (해당 job_id)**  
   - `state`, `last_heartbeat_at`, `aws_batch_job_id`, `updated_at`, `error_message`
2. **Worker CloudWatch 로그 (해당 Batch job의 logStreamName)**  
   - `Loaded SSM JSON with N keys`  
   - `DJANGO_SETTINGS_MODULE = ...`  
   - `BATCH_PROCESS_START` / `JOB_ALREADY_TAKEN` / `BATCH_JOB_COMPLETED` / `BATCH_JOB_FAILED` 순서 및 유무
3. **Batch describe-jobs**  
   - `status` (SUCCEEDED 여부), `statusReason`, `container.exitCode`, `logStreamName`

위 세 가지를 채우면,  
- Worker가 **어느 DB(호스트)** 에 붙었는지(SSM/환경),  
- **job_set_running** 이 성공했는지(JOB_ALREADY_TAKEN 여부 + Job.state),  
- **job_complete** 가 호출되었는지(BATCH_JOB_COMPLETED 여부),  
- Batch는 **SUCCEEDED** 인데 DB만 안 바뀐 건지(다른 DB에 썼을 가능성)  
까지 구체적으로 좁혀서 2차 원인 분석 보고서로 이어갈 수 있다.
