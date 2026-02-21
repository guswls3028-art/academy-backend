# Video Worker 엔터프라이즈급 리팩터 플랜

## 0. Handler return 조건 확정 (코드 근거)

### `handler.handle()` 반환값 (src/application/video/handler.py)

| 반환값 | 조건 | 라인 | sqs_main.py 동작 |
|--------|------|------|------------------|
| `"ok"` | process_fn 완료 + complete_video 성공 | 113 | delete_message |
| `"skip:cancel"` | 취소 요청 (is_cancel_requested) 또는 CancelledError | 74–75, 115 | delete_message |
| `"skip:mark_processing"` | mark_processing 실패 (이미 PROCESSING/READY 등) | 97 | change_visibility(NACK) |
| `"lock_fail"` | Redis idempotency.acquire_lock 실패 | 91 | change_visibility(NACK) |
| `"failed"` | Exception 발생 (fail_video 호출 후) | 119 | change_visibility(backoff) |

### delete_message 실행 조건 (sqs_main.py)

- `result == "ok"` (라인 283)
- `result == "skip:cancel"` (라인 328)

### 핵심: inflight 유지 원인

현재 흐름: **receive → handler.handle() (긴 인코딩) → delete**

→ handler가 반환할 때까지 SQS 메시지가 inflight로 유지됨.  
→ Lambda 스케일 수식 `desired = inflight + backlog_add` 때문에 “작업 시간”에 비례해 워커 수 증가.

---

## 1. 단계별 구현 플랜

### ✅ 단계 1: Lambda 반환값 패치 (완료)

- **파일**: `infra/worker_asg/queue_depth_lambda/lambda_function.py`
- **변경**: `set_video_worker_desired` 반환값에 디버깅용 필드 추가
  - `video_visible`, `video_inflight`, `video_backlog_add`, `video_desired_raw`, `video_new_desired`, `video_decision`, `stable_zero_since_epoch`
- **lambda_handler return**: 기존 3개 + `video_scale_result` 병합

### 단계 2: Worker "빠른 ACK + DB lease" 전환

#### 2.1 흐름 변경

**현재**

```
receive → (idempotency lock) → mark_processing → process_fn → complete_video → delete
```

**목표**

```
receive → parse/validate → 즉시 delete (ACK) → try_claim (DB) → 성공 시 process_fn → complete_video
```

#### 2.2 정확한 패치 위치

| 순서 | 파일 | 변경 |
|------|------|------|
| 1 | `academy/adapters/db/django/repositories_video.py` | `try_claim_video(video_id, worker_id) -> bool` 추가. UPLOADED → PROCESSING 원자 변경 + leased_by, leased_until 설정 |
| 2 | `src/application/ports/video_repository.py` | `try_claim_video` 인터페이스 추가 |
| 3 | `src/application/video/handler.py` | 기존 idempotency+mark_processing 대신 `try_claim_video` 호출. 실패 시 `"skip:claim"` 반환 |
| 4 | `apps/worker/video_worker/sqs_main.py` | receive 직후(handler 호출 전) `queue.delete_message(receipt_handle)` 실행. 그 후 handler 호출 |

#### 2.3 sqs_main.py 흐름 (구체)

**인코딩 작업 분기 (라인 197~):**

1. `SQS_MESSAGE_RECEIVED` 로그
2. `VIDEO_ALREADY_READY_SKIP` 체크 → 해당 시 delete 후 continue
3. **신규**: `queue.delete_message(receipt_handle)`  ← 빠른 ACK
4. visibility extender **미시작** (delete 이미 함)
5. `handler.handle(job, cfg)` 호출
6. `result == "ok"` → R2 raw 삭제, `SQS_JOB_COMPLETED` 로그 (delete는 이미 함, 스킵)
7. `result == "skip:claim"` → 이미 ACK됐으므로 로그만
8. `result == "failed"` → SQS 메시지는 이미 삭제됐으나 DB FAILED. 별도 재시도/재 enqueue 정책 필요

#### 2.4 주의사항

- **failed 처리**: 빠른 ACK 후 handler가 실패하면 메시지는 유실됨.  
  - 해결: `fail_video` 시 `FAILED_TRANSIENT` 등으로 표시 후, 별도 스케줄러/재 enqueue가 SQS에 kick 메시지 전송
- **visibility extender**: delete 직후이므로 extender 스레드 불필요 (인코딩 작업에서 제거)

### 단계 3: Lambda 스케일 수식을 visible 중심으로 단순화 (선택)

- **파일**: `infra/worker_asg/queue_depth_lambda/lambda_function.py`
- **환경변수**: `VIDEO_SCALE_VISIBLE_ONLY=1` 시 `desired_candidate = backlog_add + warm_pool` (inflight 제외)
- **scale-in**: 기존과 동일 (visible==0 AND inflight==0 유지 시)

---

## 2. DB try_claim_video 스펙

```python
def try_claim_video(self, video_id: int, worker_id: str, lease_seconds: int = 14400) -> bool:
    """
    UPLOADED → PROCESSING 원자 변경 + leased_by, leased_until 설정.
    이미 PROCESSING/READY면 False (다른 워커가 처리 중이거나 완료).
    """
    with transaction.atomic():
        video = get_video_for_update(video_id)
        if not video:
            return False
        if video.status == Video.Status.PROCESSING:
            return False  # 이미 claim됨
        if video.status == Video.Status.READY:
            return False  # 이미 완료
        if video.status != Video.Status.UPLOADED:
            return False
        video.status = Video.Status.PROCESSING
        video.leased_by = worker_id
        video.leased_until = timezone.now() + timedelta(seconds=lease_seconds)
        video.save(update_fields=["status", "leased_by", "leased_until"])
    return True
```

---

## 3. 관련 파일 요약

| 파일 | 역할 |
|------|------|
| `src/application/video/handler.py` | handle(), return 조건 |
| `apps/worker/video_worker/sqs_main.py` | SQS receive, delete, handler 호출 |
| `academy/adapters/db/django/repositories_video.py` | mark_processing, try_claim_video |
| `apps/support/video/models.py` | Video.leased_by, leased_until |
| `infra/worker_asg/queue_depth_lambda/lambda_function.py` | 스케일 계산, 반환값 |

---

## 4. 적용 순서 권장

1. **단계 1** 배포 → Lambda 반환값으로 디버깅 확인
2. **단계 2** 배포 → worker rolling update, 모니터링
3. **단계 3** 배포 → Lambda 스케일 수식 변경
