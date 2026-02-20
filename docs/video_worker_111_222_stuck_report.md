# 영상 111·222 대기 / 333만 처리됨 — 원인 분석 보고서

## 1. 현상

- **워커 3대** 모두 실행 중
- 111, 222, 333 순으로 **동시 업로드**
- **333만** 인코딩 완료, 111·222는 **대기 상태**로 멈춤

---

## 2. 원인 (코드 기반 분석)

### 2.1 핵심 원인: Skip 시 메시지 삭제 + Redis 락 미해제

Handler가 `"skip"`을 반환하면 SQS 메시지는 **즉시 삭제**됩니다.  
이때 Redis idempotency 락이 다른 프로세스(이전 크래시 워커)에 의해 **남아 있는 상태**면, 새로 받은 메시지도 처리하지 못하고 그대로 메시지만 삭제됩니다.

#### 관련 코드 경로

| 구분 | 파일:라인 | 내용 |
|------|-----------|------|
| Handler skip 조건 | `src/application/video/handler.py:84-91` | `acquire_lock` 실패 → `return "skip"` |
| 메시지 삭제 | `apps/worker/video_worker/sqs_main.py:303-304` | `result == "skip"` → `queue.delete_message(receipt_handle)` |
| Redis 락 | `src/infrastructure/cache/redis_idempotency_adapter.py:43-50` | `job:{job_id}:lock` (job_id=`encode:{video_id}`) |

### 2.2 발생 시나리오

1. 이전 시도에서 워커 A가 111 메시지를 받고 `encode:111` 락 획득
2. `mark_processing(111)` 성공 후 `process_video` 실행 중 **워커 A 강제 종료** (SIGKILL, OOM, 스팟 회수 등)
3. `finally` 블록이 실행되지 않아 **락 미해제** (TTL 4h 동안 유지)
4. SQS visibility timeout 만료 후 메시지 111이 다시 visible
5. 워커 B가 메시지 111 수신 → `acquire_lock("encode:111")` **실패**
6. Handler가 `"skip"` 반환 → **메시지 삭제**
7. 111은 DB에는 UPLOADED, 큐에는 메시지 없음 → **영구 대기**

333은 같은 경로를 거치지 않았거나, 이전에 정상 완료/실패로 락이 해제된 상태라 처리된 것으로 보입니다.

### 2.3 Skip이 발생하는 세 가지 경우

| 조건 | Handler 반환 | sqs_main 동작 | 결과 |
|------|-------------|---------------|------|
| `acquire_lock` 실패 | `"skip"` | 메시지 삭제 | 영상 대기 (락 잔류 시) |
| `mark_processing` 실패 | `"skip"` | 메시지 삭제 | 재시도 필요 |
| `is_cancel_requested` | `"skip"` | 메시지 삭제 | 정상 동작 (사용자 취소) |

`acquire_lock` 실패 시, 설계 의도는 “다른 워커가 이미 처리 중이니 중복이므로 삭제”입니다.  
하지만 락을 잡은 워커가 **크래시**한 경우에는 그 워커가 아무것도 처리하지 못했고, 메시지만 삭제되어 재처리 경로가 사라집니다.

### 2.4 기술적 근거

- **락 TTL**: `apps/worker/video_worker/sqs_main.py:51`  
  `VIDEO_LOCK_TTL_SECONDS = 14400` (4시간)
- **락 키**: `job:encode:{video_id}:lock`
- **강제 종료 시 `finally` 미실행**: SIGKILL(-9) 등으로 프로세스가 죽으면 `release_lock()`이 호출되지 않음

---

## 3. 해결 방안

### 3.1 즉시 조치 (이미 멈춘 111·222에 대해)

1. **진단 스크립트로 상태 확인**

```bash
python scripts/check_video_stuck_diagnosis.py 111 222
```

2. **Redis 락 삭제 후 재시도**

```bash
redis-cli DEL job:encode:111:lock job:encode:222:lock
```

3. **API Retry 호출**  
   - UI에서 해당 영상의 “재처리” 버튼  
   - 또는 `POST /media/videos/111/retry/`, `POST /media/videos/222/retry/`

### 3.2 구조적 개선 (장기)

#### A. Lock fail 시 메시지 삭제 대신 visibility 0 으로 재노출 (권장)

- **현재**: lock fail → skip → 메시지 삭제
- **개선**: lock fail → 메시지 삭제 대신 `change_message_visibility(receipt_handle, 0)` 으로 큐에 반환
- **효과**: 락 TTL 만료 후 다른 워커가 다시 가져가서 처리 가능
- **주의**: 단기간에 여러 번 retry될 수 있으므로, 필요 시 visibility 0 대신 60초 등으로 완화

#### B. Lock TTL 축소

- **현재**: 4시간
- **검토**: 일반 인코딩 시간을 고려해 1~2시간으로 단축하여, 크래시 시 회복 시간 단축

#### C. 워커 종료 시 락 강제 해제

- Graceful shutdown (SIGTERM) 시 `release_lock` 확실히 호출
- SIGKILL에는 대응 불가이므로, 위 A/B와 함께 사용

---

## 4. 관련 파일 요약

| 파일 | 역할 |
|------|------|
| `src/application/video/handler.py` | lock 획득 실패 시 skip 반환 |
| `apps/worker/video_worker/sqs_main.py` | skip 시 메시지 삭제, lock TTL 설정 |
| `src/infrastructure/cache/redis_idempotency_adapter.py` | Redis idempotency 락 구현 |
| `academy/adapters/db/django/repositories_video.py` | `mark_processing` 구현 |
| `scripts/check_video_stuck_diagnosis.py` | 멈춘 영상 진단 스크립트 |
| `docs/video_worker_diagnostic_checklist.md` | 워커 진단 체크리스트 |

---

## 5. 결론

- **원인**: 워커 크래시로 Redis idempotency 락이 해제되지 않았을 때, 같은 영상의 메시지를 받은 다른 워커가 lock fail → skip → 메시지 삭제를 하면서, 해당 영상이 영구적으로 대기 상태로 남음.
- **즉시 조치**: Redis 락 삭제 + Retry API로 재 enqueue.
- **구조적 개선**: lock fail 시 메시지를 큐에 다시 visible 하게 돌려서, 락 TTL 만료 후 자동 재처리가 되도록 변경하는 것을 권장합니다.
