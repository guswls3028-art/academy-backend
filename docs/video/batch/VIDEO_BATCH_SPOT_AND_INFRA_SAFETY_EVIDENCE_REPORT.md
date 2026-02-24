# Video Batch — Spot/강제종료/인프라 실패 안전성 증거 보고서

**원칙:** 추측 금지. 파일 경로·라인·실제 코드 스니펫 필수. 없으면 "없음" 명시.

---

## 1️⃣ 실행 진입점 확정

### 1.1 Job Definition (command / entrypoint / Ref::job_id)

**파일:** `scripts/infra/batch/video_job_definition.json`

| 항목 | 값 | 근거 |
|------|-----|------|
| command | `["python", "-m", "apps.worker.video_worker.batch_main", "Ref::job_id"]` | JSON 전체 1라인. containerProperties.command에 해당 배열. |
| entrypoint | **지정 없음** | containerProperties에 `entrypoint` 키 없음. |
| Ref::job_id | **사용함** | command 마지막 요소가 `"Ref::job_id"`. Batch가 parameters.job_id로 치환. |

**실제 JSON 내용 (관련 부분):**

```json
"command":["python","-m","apps.worker.video_worker.batch_main","Ref::job_id"]
"parameters":{"job_id":""}
```

→ 컨테이너 실행 시 **이미지의 ENTRYPOINT + 위 command**가 조합됨. 이미지에 ENTRYPOINT가 있으면 그게 먼저 실행되고, command는 인자로 전달됨.

### 1.2 이미지 ENTRYPOINT/CMD (Dockerfile)

**파일:** `docker/video-worker/Dockerfile`

```dockerfile
# L39-41
# Video Worker 실행 (AWS Batch)
# ENTRYPOINT: SSM /academy/workers/env fetch 후 batch_main 실행
# Batch command: ["python", "-m", "apps.worker.video_worker.batch_main", "Ref::job_id"]
ENTRYPOINT ["python", "-m", "apps.worker.video_worker.batch_entrypoint"]
CMD ["python", "-m", "apps.worker.video_worker.batch_main"]
```

- **ENTRYPOINT:** `batch_entrypoint` 모듈 실행.
- **CMD:** Batch Job Definition의 command로 **덮어쓰임**. 따라서 실제 런타임에는 Batch가 넘긴 `["python", "-m", "apps.worker.video_worker.batch_main", "<job_id>"]`가 ENTRYPOINT에 **인자**로 전달됨.

### 1.3 batch_entrypoint.py가 실행 경로에 포함되는지

**파일:** `apps/worker/video_worker/batch_entrypoint.py`

- **L37-42:** `argv = sys.argv[1:]` (또는 기본값). Batch가 인자로 준 목록이 들어옴.  
  `argv[0] == "python" or argv[0].endswith("python")` 이면 `os.execvp(argv[0], argv)`, 아니면 `os.execv(argv[0], argv)`.
- Batch command가 `["python", "-m", "apps.worker.video_worker.batch_main", "<job_id>"]`이면,  
  entrypoint 프로세스의 `sys.argv` = `["python", "-m", "apps.worker.video_worker.batch_main", "<job_id>"]` (앞에 entrypoint 경로 등이 붙을 수 있음).
- **결론:** entrypoint가 실행되면, SSM 로드 후 위 argv로 **exec**하여 `batch_main`이 최종 실행됨. 즉 **batch_entrypoint → batch_main** 순서로 실행 경로에 포함됨.

### 1.4 실행 경로 다이어그램

```
AWS Batch 컨테이너 시작
  → ENTRYPOINT: python -m apps.worker.video_worker.batch_entrypoint
  → 인자(Command): python -m apps.worker.video_worker.batch_main <job_id>
  → batch_entrypoint.main() (L17-43)
       SSM /academy/workers/env 로드 후 os.environ 설정
       argv = sys.argv[1:] → ["python", "-m", "apps.worker.video_worker.batch_main", "<job_id>"] 등
       os.execvp("python", argv) → batch_main 실행 (진입점 교체)
  → batch_main.main() (L56-155)
       job_id = env VIDEO_JOB_ID 또는 argv[-1]
       job_get_by_id → process_video → job_complete / job_fail_retry 등
```

### 1.5 Job Definition revision 추적 (코드 기준)

- **정의:** `scripts/infra/batch/video_job_definition.json` 단일 소스. revision은 코드에 없고 AWS에서 register 시 부여됨.
- **등록:** `scripts/infra/batch_video_verify_and_register.ps1` L82-85 — `register-job-definition --cli-input-json` → `$newRevision = $regOut.revision`.
- **조회:** `scripts/infra/batch_video_verify_and_register.ps1` L91-94 — `describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE` 후 `revision` 내림차순으로 최신 사용.
- **제출 시 revision:** 동 스크립트 L109 — `--job-definition "$JobDefName:$newRevision"` 형태로 특정 revision 지정 가능. API에서 submit 시에는 `batch_submit.py`가 `jobDefinition` 이름만 사용하므로, Queue가 바인딩된 기본 revision이 사용됨.

---

## 2️⃣ batch_main.py 전수 분석

**파일:** `apps/worker/video_worker/batch_main.py` (전체 156라인)

| # | 항목 | 존재 여부 | 위치 | 실제 스니펫 |
|---|------|-----------|------|-------------|
| 1 | job_set_running 호출 | **없음** | — | (호출 없음. import 목록에도 없음) |
| 2 | job_heartbeat 호출 | **없음** | — | (호출 없음. import 목록에도 없음) |
| 3 | job_fail_retry 호출 | 있음 | L138, L145 | `job_fail_retry(job_id, "CANCELLED")` / `job_fail_retry(job_id, str(e)[:2000])` |
| 4 | job_complete 호출 | 있음 | L76, L108 | `job_complete(job_id, video.hls_path, video.duration)` / `job_complete(job_id, hls_path, duration)` |
| 5 | signal.signal 등록 | **없음** | — | (파일 전체에 `signal` 모듈 import 및 사용 없음) |
| 6 | SIGTERM 문자열 | **없음** | — | (문자열 "SIGTERM" 검색 결과 없음) |
| 7 | SIGINT | **없음** | — | (문자열 "SIGINT" 검색 결과 없음) |
| 8 | try/except 구조 | 있음 | L85-87, L98-135, L137-149 | try: cache_video_status / try: process_video ~ job_complete / except CancelledError / except Exception |
| 9 | finally 블록 | **없음** | — | (try/except에 finally 없음) |
| 10 | VIDEO_JOB_MAX_ATTEMPTS 적용 | 있음 | L38, L146-148 | `VIDEO_JOB_MAX_ATTEMPTS = int(os.environ.get("VIDEO_JOB_MAX_ATTEMPTS", "5"))` / `if job_after and job_after.attempt_count >= VIDEO_JOB_MAX_ATTEMPTS: job_mark_dead(...)` |

### RUNNING 상태를 DB에 세팅하는 코드

- **없음.** `job_set_running`은 import되지 않으며 호출되지 않음. docstring L4-5: "NO job_set_running. NO RUNNING state block."

### last_heartbeat_at을 DB에 업데이트하는 코드

- **없음.** `job_heartbeat` 미호출. `job_set_running`도 미호출이므로 Batch 워커 경로에서 `last_heartbeat_at`은 한 번도 갱신되지 않음.

### Redis heartbeat만 쓰고 DB는 안 쓰는지

- **맞음.** L85: `cache_video_status(job_obj.tenant_id, job_obj.video_id, "PROCESSING", ttl=21600)` — Redis에만 상태/진행 캐시. DB의 `VideoTranscodeJob.last_heartbeat_at`은 이 경로에서 갱신하지 않음.

### batch_main.py import 및 핵심 분기 (인용)

```python
# L21-27
from academy.adapters.db.django.repositories_video import (
    job_get_by_id,
    job_complete,
    job_fail_retry,
    job_mark_dead,
    job_is_cancel_requested,
)
# job_set_running, job_heartbeat 없음

# L106-110
    try:
        hls_path, duration = process_video(job=job_dict, cfg=cfg, progress=progress)
        ok, reason = job_complete(job_id, hls_path, duration)
        ...
# L137-149
    except CancelledError:
        job_fail_retry(job_id, "CANCELLED")
        ...
    except Exception as e:
        ...
        job_fail_retry(job_id, str(e)[:2000])
        job_after = job_get_by_id(job_id)
        if job_after and job_after.attempt_count >= VIDEO_JOB_MAX_ATTEMPTS:
            job_mark_dead(job_id, ...)
```

---

## 3️⃣ DB 상태 머신 실제 동작 추적

### 3.1 함수 정의 (repositories_video.py)

| 함수 | 파일:라인 | state 변경 | attempt_count | last_heartbeat_at |
|------|-----------|------------|---------------|-------------------|
| job_set_running | 632-648 | QUEUED/RETRY_WAIT → RUNNING | 변경 없음 | `last_heartbeat_at=now` 설정 |
| job_heartbeat | 659-673 | 변경 없음 | 변경 없음 | `last_heartbeat_at=now` 갱신 (RUNNING만) |
| job_fail_retry | 728-744 | → RETRY_WAIT | F("attempt_count")+1 | lock 해제만 |
| job_complete | 676-725 | → SUCCEEDED | 변경 없음 | lock 해제만 |
| job_mark_dead | 783-814 | → DEAD, Video→FAILED | 변경 없음 | lock 해제만 |

**job_set_running 스니펫 (L632-648):**

```python
def job_set_running(job_id: str) -> bool:
    ...
    n = VideoTranscodeJob.objects.filter(
        pk=job_id,
        state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RETRY_WAIT],
    ).update(
        state=VideoTranscodeJob.State.RUNNING,
        locked_by="batch",
        locked_until=now,
        last_heartbeat_at=now,
        updated_at=now,
    )
    return n == 1
```

**job_fail_retry 스니펫 (L728-744):**

```python
def job_fail_retry(job_id: str, reason: str) -> tuple[bool, str]:
    ...
    job.state = VideoTranscodeJob.State.RETRY_WAIT
    job.attempt_count = F("attempt_count") + 1
    job.error_message = str(reason)[:2000]
    ...
```

### 3.2 Batch 워커 경로에서의 호출 여부

| 함수 | Batch 워커(batch_main)에서 호출 | 호출 위치 |
|------|---------------------------------|-----------|
| job_set_running | **아니오** | — |
| job_heartbeat | **아니오** | — |
| job_fail_retry | 예 | L138 (CancelledError), L145 (Exception) |
| job_complete | 예 | L76 (idempotent READY), L108 (정상 완료) |
| job_mark_dead | 예 | L147 (attempt_count >= MAX) |

---

## 4️⃣ Spot / SIGTERM / 인프라 종료 대응 여부

### 4.1 프로젝트 전체 검색 결과 (Batch 워커와 연결 여부)

| 키워드 | 파일 경로 | 라인 | 실제 코드/용도 | Batch 워커와 연결 |
|--------|-----------|------|----------------|-------------------|
| signal.signal | apps/worker/messaging_worker/sqs_main.py | 194-195 | `signal.signal(signal.SIGTERM, _handle_signal)` / `signal.signal(signal.SIGINT, _handle_signal)` | 아니오 (SQS 워커) |
| signal.signal | academy/framework/workers/ai_sqs_worker.py | 95-96 | 동일 패턴 | 아니오 (AI 워커) |
| signal.signal | libs/observability/shutdown.py | 48-49 | SIGTERM/SIGINT 핸들러 | Batch 워커에서 import/사용 없음 |
| SIGTERM | docs, sqs_main, transcoder 등 | 여러 곳 | 문서 또는 ffmpeg 하위 프로세스 취소용 문자열 | batch_main/batch_entrypoint에는 없음 |
| SIGINT | 위와 동일 | — | — | batch_main/batch_entrypoint에는 없음 |
| instance-action | **없음** | — | — | — |
| 169.254.169.254 | **없음** | — | — | — |
| describe-jobs | validate_batch_video_system.py | 28-45 | aws batch describe-jobs 호출, status/statusReason 조회 | 검증 전용, DB 업데이트 없음 |
| statusReason | validate_batch_video_system.py | 32 | query에 reason:statusReason 포함 | 검증 출력용만, DB 반영 없음 |
| Host EC2 instance terminated | **없음** | — | — | — |
| Spot | docs, redis_status_cache 주석 등 | 여러 곳 | "Spot/Scale-in drain" 등 설명 | Batch 워커 코드에는 없음 |

**batch_main.py / batch_entrypoint.py 내 검색:** `signal`, `SIGTERM`, `SIGINT`, `169.254`, `instance-action`, `describe`, `statusReason`, `Spot` — **모두 없음.**

---

## 5️⃣ Batch 상태 → DB 동기화 존재 여부

| 질문 | 답 | 근거 |
|------|-----|------|
| describe_jobs 호출 후 DB 업데이트하는 코드 존재? | **없음** | validate_batch_video_system.py의 run_aws_batch_describe는 반환만 하고 DB update 없음. 다른 호출처도 검색 결과 없음. |
| aws_batch_job_id 기반 상태 polling 로직 존재? | **없음** | DB를 주기적으로 조회해 describe_jobs 호출하고 state를 갱신하는 루프/커맨드/Lambda 없음. |
| Batch FAILED 상태를 DB FAILED/RETRY_WAIT로 반영하는 코드 존재? | **없음** | Batch API 결과로 Job state를 쓰는 코드 없음. |

---

## 6️⃣ scan_stuck_video_jobs 실제 동작 조건 검증

**파일:** `apps/support/video/management/commands/scan_stuck_video_jobs.py`

### 6.1 조건

| 확인 항목 | 여부 | 코드 위치 및 내용 |
|-----------|------|-------------------|
| RUNNING만 조회하는지 | 예 | L44-47: `state=VideoTranscodeJob.State.RUNNING`, `last_heartbeat_at__lt=cutoff` |
| last_heartbeat_at 조건 | `last_heartbeat_at__lt=cutoff` (cutoff = now - threshold_minutes, 기본 3분) | L41, L46 |
| RETRY_WAIT 후 submit_batch_job 호출하는지 | **management command만 예** | L74-77: state=RETRY_WAIT, attempt_count 증가 후 L79 `submit_batch_job(str(job.id))` 호출 |

**스니펫 (L44-47, L74-80):**

```python
        qs = VideoTranscodeJob.objects.filter(
            state=VideoTranscodeJob.State.RUNNING,
            last_heartbeat_at__lt=cutoff,
        ).order_by("id")
        ...
                else:
                    job.state = VideoTranscodeJob.State.RETRY_WAIT
                    ...
                    aws_job_id, submit_err = submit_batch_job(str(job.id))
```

### 6.2 Internal API scan-stuck에서 submit 호출 여부

**파일:** `apps/support/video/views/internal_views.py` — `VideoScanStuckView` L209-256

- RUNNING + last_heartbeat_at < cutoff 인 job에 대해 DEAD 또는 RETRY_WAIT + attempt_count 증가만 수행.
- **submit_batch_job 호출 없음.** L249-254: `job.save(...)` 까지만 있고, `submit_batch_job` import/호출 없음.

### 6.3 현재 Batch 구조에서 스캐너가 실제로 동작 가능한지

- **아니오.**
  - Batch 워커는 `job_set_running`을 호출하지 않으므로 **DB state가 RUNNING이 된 적이 없음.**
  - `last_heartbeat_at`도 워커 경로에서 갱신하지 않아 **항상 null.**
  - Django/DB에서 `last_heartbeat_at__lt=cutoff`는 `last_heartbeat_at`이 null인 행을 **매칭하지 않음** (SQL NULL 비교).
- 따라서 **Batch로만 생성된 job은 scan_stuck_video_jobs 조건을 절대 만족하지 않으며**, 스캐너는 현재 Batch 구조에서 **실제로 동작하지 않음.**

---

## 7️⃣ 실제 위험 시나리오 시뮬레이션

전제: Batch 워커는 RUNNING/heartbeat 미설정, SIGTERM 미처리, Batch→DB 동기화 없음.

| 시나리오 | DB state 최종값 | 자동 복구 | Orphan 가능성 | 사용자 UI 표시 |
|----------|-----------------|-----------|---------------|----------------|
| A) 인코딩 도중 EC2 강제 종료 | QUEUED 유지 | 없음 (스캐너 대상 아님, describe_jobs 동기화 없음) | 높음 (영원히 QUEUED) | 처리 중/대기처럼 보이거나 멈춤 |
| B) Batch timeout 발생 | QUEUED 유지 | 없음 | 높음 | 동일 |
| C) 컨테이너 OOM | QUEUED 유지 | 없음 | 높음 | 동일 |
| D) SIGTERM 수신 | QUEUED 유지 (핸들러 없음, 프로세스만 종료) | 없음 | 높음 | 동일 |
| E) 네트워크 장애 | DB 연동 실패 시 QUEUED 유지 또는 job_complete/job_fail_retry 미호출 시 QUEUED | 없음 | 높음 | 동일 |
| F) Batch FAILED, container never calls job_fail_retry | QUEUED 유지 | 없음 | 높음 | 동일 |

---

## 8️⃣ 멀티테넌트 안전성 재확인

| 구분 | tenant_id 필터 | 파일:라인 | cross-tenant 영향 가능성 |
|------|----------------|-----------|---------------------------|
| VideoTranscodeJob 조회 (일반) | job_get_by_id: pk만 사용, tenant_id 없음 | repositories_video.py:626-629 | job_id만 알면 다른 테넌트 job 조회 가능 (내부 API는 신뢰 전제) |
| scan_stuck_video_jobs | **없음** | scan_stuck_video_jobs.py:44-47 | 전역 RUNNING 스캔. 현재는 Batch가 RUNNING을 안 써서 실질적 대상 0. |
| job_count_backlog | **없음** | repositories_video.py:810-816 | 전역 집계. |
| job_compute_backlog_score | **없음** | repositories_video.py:832-836 | 전역 집계. |
| DLQ mark-dead (internal) | **없음** | internal_views.py:150 job_get_by_id(job_id) | body의 job_id만 사용. 내부 API 노출 시 타테넌트 job DEAD 가능. |
| Retry/Delete (video_views) | video 기준 (get_object → video.current_job_id) | video_views.py:377, 516 | 테넌트 스코프됨 (ViewSet 권한). |

---

## 9️⃣ 최종 평가

| 항목 | 점수 (0~100) | 근거 |
|------|---------------|------|
| Lifecycle sync | 15 | RUNNING/heartbeat 미반영. job_complete·job_fail_retry만 사용. describe_jobs→DB 없음. |
| Infra failure recovery | 0 | SIGTERM/Spot/describe_jobs 기반 복구 전무. |
| Retry robustness | 50 | 앱 예외 시 RETRY_WAIT·MAX_ATTEMPTS 적용. 인프라 실패 시 재시도 경로 없음. |
| Orphan protection | 10 | 스캐너가 Batch job을 선택하지 않음. Batch→DB 동기화 없어 orphan 정리 없음. |
| Tenant isolation | 65 | 사용자/Workbox 경로는 테넌트 스코프. scan/backlog/DLQ는 tenant_id 없음. |
| Production readiness | 45 | 위 요소 종합. |

**한 줄 결론:** **Pre-production** — 인코딩·완료·앱 실패 재시도는 동작하나, Spot/강제종료/인프라 실패에 대한 자동 복구와 DB 생명주기 동기화가 없어 프로덕션 투입에는 미흡함.

---

## 출력 요약 (표·경로·스니펫)

- 모든 결론은 위 섹션의 **파일 경로 + 라인 번호 + 인용 코드**에 기반함.
- “없음”은 해당 키워드/동작이 **코드베이스에 존재하지 않음**을 의미함.
- Batch 워커 = `batch_entrypoint` → `batch_main` 실행 경로만 대상으로 함.
