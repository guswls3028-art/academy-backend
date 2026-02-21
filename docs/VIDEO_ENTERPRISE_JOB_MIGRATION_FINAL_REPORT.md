# Video Transcode Pipeline — Enterprise Job System 마이그레이션 최종 보고서

> **작업 기간**: 2025년 기준  
> **목표**: Video.status 기반 인코딩 파이프라인을 Job 기반 실행 시스템으로 마이그레이션  
> **상태**: 구현 완료

---

## 1. 개요

### 1.1 작업 배경

기존 파이프라인은 다음 문제를 가지고 있었다:

- **VIDEO_FAST_ACK=1**: receive 직후 DeleteMessage → ffmpeg 중 워커 죽으면 SQS 메시지 유실, DB PROCESSING stuck
- **재인코딩 버튼**: 프론트/백엔드 불일치(프론트: FAILED/PROCESSING/UPLOADED 노출, 백엔드: READY/FAILED만 허용) → PROCESSING/UPLOADED에서 "먹통"
- **job_id 부재**: 메시지/DB에 job_id 없음 → DLQ에서 정확한 재처리 불가
- **상태 불일치 위험**: 파일 스토리지 작업과 DB 업데이트 사이 트랜잭션/순서 미흡

### 1.2 마이그레이션 목표

| 목표 | 설명 |
|------|------|
| **At-least-once** | 워커 강제 종료/재시작/스팟 종료 시에도 작업 재처리 가능 |
| **Idempotent** | 동일 job 중복 실행 시에도 결과/DB/스토리지 손상 방지 |
| **Long job safe** | ffmpeg가 visibility timeout보다 길어도 ChangeMessageVisibility heartbeat로 중복 실행 방지 |
| **DB locking** | 메시지 수신 시 DB 원자적 락/상태 전환 성공 워커만 실행 |
| **Stuck auto-repair** | RUNNING stuck 자동 탐지 → RETRY_WAIT/DEAD 처리 |
| **Re-encode 정석** | retry API는 새 Job 생성 + SQS enqueue(job_id 포함) 보장 |
| **DLQ 추적** | DLQ 메시지에 job_id 포함 → 정확한 Job 재처리 가능 |
| **Transactional** | 작업 성공/실패 DB 업데이트는 반드시 트랜잭션 처리 |
| **Idempotent 구조** | 스토리지/DB 작업 순서 고려하여 중복 실행 시에도 안전 |

---

## 2. 아키텍처 변경

### 2.1 원칙 변경

| 기존 | 변경 후 |
|------|---------|
| Video.status가 실행 상태 포함 (UPLOADED, PROCESSING, READY, FAILED) | Video.status는 결과만 (UPLOADED, READY, FAILED) |
| 실행 상태: Video.status + leased_by/leased_until | 실행 상태: **VideoTranscodeJob.state** |
| SQS 메시지: video_id 중심 | SQS 메시지: **job_id** 중심 |
| VIDEO_FAST_ACK=1 허용 (receive 직후 delete) | **VIDEO_FAST_ACK 제거** — delete는 성공 커밋 이후만 |
| BacklogCount: Video.status IN (UPLOADED, PROCESSING) | BacklogCount: **Job.state IN (QUEUED, RETRY_WAIT, RUNNING)** |

### 2.2 처리 흐름 비교

| 단계 | 기존 | 변경 후 |
|------|------|---------|
| 1 | receive → (FAST_ACK 시 즉시 delete) → handler | receive → job_id 검증 → job_claim_for_running |
| 2 | mark_processing / try_claim_video (Video.status) | Job.state RUNNING 원자 전환 (UPDATE WHERE state IN (QUEUED,RETRY_WAIT)) |
| 3 | process_video | process_video |
| 4 | complete_video / fail_video | job_complete / job_fail_retry |
| 5 | delete_message (성공 시, !FAST_ACK만) | delete_message (**성공 커밋 이후 항상**) |
| 6 | visibility extender (90초마다, !FAST_ACK만) | **60초마다** ChangeMessageVisibility + job_heartbeat |

---

## 3. 신규/변경 모델

### 3.1 VideoTranscodeJob (신규)

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID (PK) | Job 고유 식별자 |
| video_id | FK | Video 참조 |
| tenant_id | PositiveInteger | 쿼리용 비정규화 |
| state | CharField | QUEUED, RUNNING, SUCCEEDED, FAILED, RETRY_WAIT, DEAD, CANCELLED |
| attempt_count | PositiveInteger | 재시도 횟수 (기본 1) |
| locked_by | CharField | 워커 식별자 |
| locked_until | DateTimeField | 락 만료 시각 |
| last_heartbeat_at | DateTimeField | 마지막 heartbeat 시각 |
| error_code | CharField | 실패 코드 |
| error_message | TextField | 실패 사유 |
| created_at, updated_at | DateTimeField | 생성/수정 시각 |

**파일**: `apps/support/video/models.py` L162-209

### 3.2 Video.current_job (신규 FK)

| 필드 | 타입 | 설명 |
|------|------|------|
| current_job_id | FK (nullable) | 현재 transcoding Job (진행 중 또는 최종) |

**파일**: `apps/support/video/models.py` L124-136

### 3.3 마이그레이션

**파일**: `apps/support/video/migrations/0003_videotranscodejob_video_current_job.py`  
**적용**: `python manage.py migrate video`

---

## 4. 메시지 구조 변경

### 4.1 enqueue 메시지 (신규)

```json
{
  "job_id": "uuid",
  "video_id": 1,
  "tenant_id": 1,
  "file_key": "tenants/1/videos/1/source.mp4"
}
```

### 4.2 기존 메시지 (legacy)

```json
{
  "video_id": 1,
  "file_key": "...",
  "tenant_id": 1,
  "tenant_code": "...",
  "created_at": "...",
  "attempt": 1
}
```

**처리**: job_id 없으면 NACK (legacy 메시지는 maxReceiveCount 초과 후 DLQ로 이동).

---

## 5. 변경 파일 상세

### 5.1 apps/support/video/models.py

- `import uuid` 추가
- `VideoTranscodeJob` 모델 추가 (State: QUEUED, RUNNING, SUCCEEDED, FAILED, RETRY_WAIT, DEAD, CANCELLED)
- `Video.current_job` FK 추가

### 5.2 apps/support/video/services/sqs_queue.py

| 함수 | 변경 내용 |
|------|----------|
| `create_job_and_enqueue(video)` | VideoTranscodeJob 생성 → Video.current_job 설정 → enqueue_by_job 호출 |
| `enqueue_by_job(job)` | job_id, video_id, tenant_id, file_key 포함 메시지 전송 |
| `receive_message` | 반환 dict에 `job_id` 필드 추가 |

### 5.3 academy/adapters/db/django/repositories_video.py

| 함수 | 설명 |
|------|------|
| `job_get_by_id(job_id)` | Job 조회 (video, session, lecture 포함) |
| `job_claim_for_running(job_id, worker_id, lease_seconds)` | QUEUED/RETRY_WAIT → RUNNING 원자 전환 |
| `job_heartbeat(job_id)` | last_heartbeat_at 갱신 |
| `job_complete(job_id, hls_path, duration)` | Job SUCCEEDED + Video READY (transactional, idempotent) |
| `job_fail_retry(job_id, reason)` | Job RETRY_WAIT + attempt_count++ |
| `job_cancel(job_id)` | Job CANCELLED |
| `job_mark_dead(job_id, error_code, error_message)` | Job DEAD + Video FAILED (transactional) |
| `job_count_backlog()` | QUEUED + RETRY_WAIT + RUNNING 개수 |

### 5.4 apps/worker/video_worker/sqs_main.py

- **VIDEO_FAST_ACK 제거**: receive 직후 delete 경로 삭제
- **ProcessVideoJobHandler 미사용**: process_video 직접 호출
- **Job 기반 처리**:
  1. job_id 없으면 NACK
  2. job_claim_for_running → 실패 시 NACK
  3. 60초마다 ChangeMessageVisibility + job_heartbeat
  4. process_video → job_complete → R2 raw 삭제 → delete_message
  5. CancelledError → job_cancel → delete_message
  6. Exception → job_fail_retry → attempt_count >= MAX 시 job_mark_dead → NACK

### 5.5 apps/support/video/views/video_views.py

| 액션 | 변경 내용 |
|------|----------|
| `upload_complete` | `VideoSQSQueue().enqueue(video)` → `create_job_and_enqueue(video)` |
| `retry` | current_job 상태 검사, cancel_requested 설정, `create_job_and_enqueue(video)` |

### 5.6 apps/support/video/views/internal_views.py

| 뷰 | 변경 내용 |
|------|----------|
| `VideoBacklogCountView` | `Video.objects.filter(status__in=[UPLOADED, PROCESSING]).count()` → `job_count_backlog()` |

### 5.7 apps/support/video/management/commands/scan_stuck_video_jobs.py (신규)

- **조건**: Job.state=RUNNING && last_heartbeat_at < now - 3분
- **동작**: attempt_count >= 5 → DEAD, else → RETRY_WAIT, attempt_count++
- **실행**: `python manage.py scan_stuck_video_jobs [--dry-run] [--threshold N]`
- **권장 cron**: 2분마다

---

## 6. Transactional 및 Idempotent 보강

### 6.1 Transactional DB 업데이트

| 함수 | 처리 |
|------|------|
| `job_complete` | `with transaction.atomic()`: Video + Job 원자 커밋 |
| `job_fail_retry` | `with transaction.atomic()`: Job 업데이트 |
| `job_mark_dead` | `with transaction.atomic()`: Job + Video 원자 업데이트 (보강 적용) |
| `job_claim_for_running` | 단일 UPDATE (원자적) |

### 6.2 Idempotent 구조

**처리 순서 (sqs_main.py)**:

1. **스토리지(HLS)**: `process_video()` — R2에 HLS 업로드
2. **DB 커밋**: `job_complete()` — Video READY + Job SUCCEEDED 원자 커밋
3. **R2 raw 삭제**: DB 커밋 후 수행 (실패해도 DB는 READY 유지)
4. **DeleteMessage**: DB 커밋 이후에만 호출

**job_complete Idempotent 보강**:

- 이미 Job.state=SUCCEEDED && Video.status=READY && video.hls_path 존재 시 → `True, "idempotent"` 반환
- DB 커밋 직후 크래시로 메시지 재전달되어도 중복 커밋 없이 안전 처리

---

## 7. 환경 변수 및 상수

| 항목 | 기본값 | 설명 |
|------|--------|------|
| VIDEO_JOB_MAX_ATTEMPTS | 5 | attempt_count 초과 시 DEAD 처리 |
| JOB_HEARTBEAT_INTERVAL_SECONDS | 60 | ChangeMessageVisibility + job_heartbeat 주기 |
| VISIBILITY_EXTEND_SECONDS | 900 | visibility 연장 값 |
| STUCK_THRESHOLD_MINUTES | 3 | scan_stuck_video_jobs 기준 (분) |

---

## 8. API 변경

### 8.1 retry API

**URL**: `POST /api/v1/media/videos/{pk}/retry/`

**변경 전**:
- UPLOADED/PROCESSING → "Already in backlog"
- READY/FAILED → status=UPLOADED, enqueue(video)

**변경 후**:
- current_job이 QUEUED/RUNNING/RETRY_WAIT → "Already in backlog"
- READY/FAILED (또는 UPLOADED/PROCESSING이지만 current_job 없음/종료됨) → 새 Job 생성, cancel_requested 설정, create_job_and_enqueue

**응답 추가**: `{"detail": "...", "job_id": "uuid"}`

### 8.2 BacklogCount API

**URL**: `GET /api/v1/internal/video/backlog-count/`

**변경**: Video.status 기반 → `job_count_backlog()` (Job.state IN (QUEUED, RETRY_WAIT, RUNNING))

---

## 9. 배포 시 고려사항

### 9.1 배포 순서

1. **마이그레이션**: `python manage.py migrate video`
2. **API/Worker 동시 배포**: 신규 enqueue는 job_id 포함, Worker는 job_id 필수
3. **기존 큐 메시지**: job_id 없는 legacy 메시지는 NACK → maxReceiveCount(3) 초과 후 DLQ 이동

### 9.2 롤백

- 마이그레이션 롤백: `python manage.py migrate video 0002_...`
- 코드 롤백 시 VideoTranscodeJob 테이블은 유지 가능 (unused)

### 9.3 VIDEO_FAST_ACK 제거에 따른 영향

- 기존 `full_redeploy.ps1`, `video_worker_user_data.sh`, `docker-compose.yml`에서 `VIDEO_FAST_ACK=1` 설정이 있어도 **Worker 코드에서 해당 분기 제거**됨
- 환경 변수는 무시되며, 항상 "성공 후 delete" 경로만 사용됨

---

## 10. 검증 체크리스트

### 10.1 Worker Kill 테스트

1. 영상 업로드 → SQS 메시지 수신
2. process_video 진행 중 Worker에 SIGTERM
3. **기대**: 메시지 visibility 복귀 → 다른 워커가 재수신 → job_claim_for_running (이전 워커는 heartbeat 중단으로 stuck) → scan_stuck_video_jobs가 RETRY_WAIT 전환 또는 visibility 복귀 후 새 워커가 claim

### 10.2 Retry 버튼 테스트

1. READY 또는 FAILED 영상에서 retry 클릭
2. **기대**: 새 VideoTranscodeJob 생성, SQS 메시지(job_id 포함) 전송, 202 + job_id 응답
3. CloudWatch/SQS 콘솔에서 메시지 본문에 job_id 확인

### 10.3 DLQ 유도 테스트

1. process_video 내부에 고의 예외 발생 코드 임시 추가
2. 3회 수신 후 DLQ로 이동 확인
3. DLQ 메시지 본문에 job_id 포함 확인

### 10.4 Idempotent 검증

1. job_complete 직후 delete_message 직전에 프로세스 강제 종료
2. 메시지 visibility 복귀 후 재수신
3. **기대**: get_video_status == READY이면 VIDEO_ALREADY_READY_SKIP, delete_message (idempotent ack)

---

## 11. 관련 문서

| 문서 | 설명 |
|------|------|
| `VIDEO_ENTERPRISE_CURRENT_STRUCTURE_REPORT.md` | 마이그레이션 전 현 구조 (grep 기반) |
| `VIDEO_ENTERPRISE_JOB_MIGRATION_DESIGN.md` | Job 기반 마이그레이션 설계 |
| `VIDEO_ENTERPRISE_WORKER_PATCH.md` | Worker patch 요약 |
| `B1_IMPLEMENTATION_FINAL_REPORT.md` | B1 스케일링 (BacklogCount TargetTracking) |

---

## 12. 요약

| 항목 | 내용 |
|------|------|
| **모델** | VideoTranscodeJob 신규, Video.current_job 추가 |
| **메시지** | job_id 필수, `{job_id, video_id, tenant_id, file_key}` |
| **Worker** | VIDEO_FAST_ACK 제거, job_id 기반, 60초 heartbeat, 성공 후 delete |
| **API** | upload_complete/retry → create_job_and_enqueue |
| **BacklogCount** | job_count_backlog() |
| **Stuck Scanner** | scan_stuck_video_jobs (2분 cron 권장) |
| **Transactional** | job_complete, job_fail_retry, job_mark_dead |
| **Idempotent** | 스토리지→DB→delete 순서, job_complete idempotent 반환 |
