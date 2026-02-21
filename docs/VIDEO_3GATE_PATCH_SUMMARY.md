# Video Worker 3-Gate STRICT EVIDENCE-ONLY 패치 요약

## GATE 1 — FAST ACK 토글/코드 경로 확정 + 안전장치

### 수정 파일

| 파일 | 변경 |
|------|------|
| `apps/worker/video_worker/sqs_main.py` | VIDEO_FAST_ACK_APPLIED / VIDEO_FAST_ACK_SKIPPED 로그, receipt_handle_suffix |

### 핵심 diff

```python
# L244-258 (sqs_main.py)
if VIDEO_FAST_ACK:
    queue.delete_message(receipt_handle)
    receipt_suffix = receipt_handle[-12:] if receipt_handle and len(receipt_handle) >= 12 else (receipt_handle or "")[:12]
    logger.info("VIDEO_FAST_ACK_APPLIED | request_id=%s | video_id=%s | receipt_handle_suffix=%s", ...)
else:
    logger.info("VIDEO_FAST_ACK_SKIPPED | request_id=%s | video_id=%s | reason=VIDEO_FAST_ACK=0", ...)
```

- `result == "ok"` / `"skip:cancel"` 분기: `if not VIDEO_FAST_ACK:` 가드로 delete_message 중복 호출 방지 (기존 코드)
- VIDEO_FAST_ACK=1 시 visibility extender 미시작 (기존 코드)

### 금지 사항 (준수됨)

- 진행률 DB 저장 안 함
- SQS Visibility/ChangeVisibility를 FAST_ACK 경로에서 사용 안 함

### 검증

```bash
# VIDEO_FAST_ACK=1 로그
grep -E "VIDEO_FAST_ACK_APPLIED|VIDEO_FAST_ACK_SKIPPED" /app/apps/worker/video_worker/sqs_main.py

# delete_message 중복 방지 확인 (VIDEO_FAST_ACK=1일 때 result 분기에서 delete 안 함)
grep -n "delete_message\|VIDEO_FAST_ACK" /app/apps/worker/video_worker/sqs_main.py
```

**기대 결과**:
- `VIDEO_FAST_ACK=1`: `VIDEO_FAST_ACK_APPLIED` 로그
- `VIDEO_FAST_ACK=0`: `VIDEO_FAST_ACK_SKIPPED` 로그
- `result == "ok"` 분기: `if not VIDEO_FAST_ACK:` 블록 내에서만 delete_message 호출

---

## GATE 2 — DB Lease(Ownership) 도입

### 수정 파일

| 파일 | 변경 |
|------|------|
| `apps/support/video/models.py` | 기존: `leased_by`, `leased_until` 필드 (이미 존재) |
| `academy/adapters/db/django/repositories_video.py` | `try_claim_video`, `try_reclaim_video` |
| `src/application/ports/video_repository.py` | `try_claim_video`, `try_reclaim_video` 인터페이스 |

### try_reclaim_video 스펙

- 조건: `status=PROCESSING` + (`leased_until < now` 또는 `force=True`)
- 동작: `UPLOADED`로 변경, `leased_by`/`leased_until` 초기화
- `force=True`: heartbeat 없음 등으로 worker 사망 시 lease 무시

### 금지 사항 (준수됨)

- Redis lock으로 ownership 구현 안 함
- try_claim 성공 전 인코딩 시작 안 함

### 검증

```bash
# Django shell에서 이중 claim 테스트
python manage.py shell -c "
from academy.adapters.db.django.repositories_video import DjangoVideoRepository
from apps.support.video.models import Video
repo = DjangoVideoRepository()
# video_id=1 (UPLOADED) 가정
r1 = repo.try_claim_video(1, 'worker-a')
r2 = repo.try_claim_video(1, 'worker-b')  # False 기대
print('first_claim=', r1, 'second_claim=', r2)
"
```

**기대**: `first_claim=True`, `second_claim=False`

---

## GATE 3 — Redis Telemetry + 재시도(Recovery)

### 수정 파일

| 파일 | 변경 |
|------|------|
| `apps/support/video/redis_status_cache.py` | `set_video_heartbeat`, `has_video_heartbeat`, `delete_video_heartbeat` |
| `src/infrastructure/cache/redis_progress_adapter.py` | `record_progress` 시 video job이면 heartbeat setex |
| `apps/support/video/management/commands/reconcile_video_processing.py` | 신규: Reclaim + Re-enqueue 커맨드 |

### Redis 키 규칙

- progress: `tenant:{tenant_id}:video:{video_id}:progress` (TTL 3600)
- heartbeat: `tenant:{tenant_id}:video:{video_id}:heartbeat` (TTL 60)

### Reconcile 커맨드

```bash
# Dry-run
python manage.py reconcile_video_processing --dry-run

# 실행
python manage.py reconcile_video_processing
```

트리거: PROCESSING + (leased_until < now 또는 heartbeat 없음) → try_reclaim_video → enqueue

### 금지 사항 (준수됨)

- 재시도를 SQS visibility timeout에 의존 안 함
- 진행률 DB 저장 안 함

### 검증

```bash
# reconcile dry-run
python manage.py reconcile_video_processing --dry-run
```

---

## 전체 검증 명령 및 기대 결과

### 1. SQS 상태

```powershell
aws sqs get-queue-attributes --queue-url https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-video-jobs --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region ap-northeast-2
```

**VIDEO_FAST_ACK=1 + 처리 중**: visible≈0, inflight 급감 (receive 직후 delete되므로)

### 2. Lambda invoke

```powershell
aws lambda invoke --function-name academy-worker-queue-depth-metric --region ap-northeast-2 out.json; Get-Content out.json
```

**기대**: `video_visible`, `video_inflight`, `video_desired_raw` 등 포함 (이미 패치됨)

### 3. Worker 로그

```bash
sudo docker logs academy-video-worker 2>&1 | grep -E "VIDEO_FAST_ACK_APPLIED|VIDEO_FAST_ACK_SKIPPED|SQS_MESSAGE_RECEIVED|handler.handle\(\) returned|SQS_JOB_COMPLETED"
```

**VIDEO_FAST_ACK=1**:
- `SQS_MESSAGE_RECEIVED` 직후 `VIDEO_FAST_ACK_APPLIED`
- `handler.handle() returned` 후 `SQS_JOB_COMPLETED` (delete는 이미 수행됨)

**VIDEO_FAST_ACK=0**:
- `VIDEO_FAST_ACK_SKIPPED`
- `result == "ok"` 시 `SQS_JOB_COMPLETED` 직전 delete_message
