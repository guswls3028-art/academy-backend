# 비디오 진행률(Progress) 엔드포인트 — 최종 요약

## 1. 진행 요약 (완료된 작업)

### 1.1 원인
- **증상**: `GET /api/v1/media/videos/{id}/progress/` 호출 시 **502 Bad Gateway** + CORS 에러 (No 'Access-Control-Allow-Origin' header).
- **근본 원인**: Batch 모드에서는 워커가 아직 Redis에 상태/진행률 키를 쓰기 전에 프론트가 progress를 폴링함. `redis.get()` 이 `None` 인데 **`json.loads(None)`** 을 호출해 `TypeError` 발생 → Gunicorn 워커 크래시 → nginx 502 → CORS 헤더 없이 응답.

### 1.2 적용된 수정 사항

| 파일 | 수정 내용 |
|------|-----------|
| **`apps/support/video/encoding_progress.py`** | `_get_progress_payload()`: `raw = client.get(key)` 후 **`if raw is not None and raw:`** 일 때만 `json.loads(raw)` 호출. tenant 키 / legacy 키 모두 동일 가드 적용. |
| **`apps/support/video/redis_status_cache.py`** | `get_video_status_from_redis()`: `cached_data = redis_client.get(key)` 후 **`if cached_data is None or not cached_data: return None`** 이면 `json.loads` 호출 안 함. |
| **`apps/support/video/views/progress_views.py`** | ① `_default_progress_response(video_id)` 도입: Redis/DB 없거나 예외 시 **항상 200** 으로 `status: "PENDING"`, `progress: 0` 등 기본값 반환. ② `get_video_status_from_redis` 예외 → 기본 응답. ③ `cached_status is None` 이고 DB에서 READY/FAILED 없음 → 기본 응답. ④ 응답 구성 중 어떤 예외든 `try/except` 로 잡아 기본 응답 반환. ⑤ `cached_status` 사용 시 `isinstance(cached_status, dict)` 체크. |

**결과**: progress 엔드포인트는 **Batch 모드에서 Redis 키가 없어도 예외를 던지지 않고**, 항상 `{"status": "PENDING", "progress": 0, ...}` 형태의 기본 응답을 200으로 반환.

---

## 2. 남은 과정 (실행할 작업)

1. **배포**
   - 위 수정이 반영된 코드를 API 서버에 배포 (예: `scripts/full_redeploy.ps1` 또는 사용 중인 배포 파이프라인).

2. **검증**
   - 배포 후 브라우저에서 비디오 업로드 → 업로드 완료 후 progress 폴링 시 **502/CORS 없이** 응답이 오는지 확인.
   - 직접 호출 예:
     ```bash
     curl -s -o /dev/null -w "%{http_code}" "https://api.hakwonplus.com/api/v1/media/videos/<video_id>/progress/" -H "Authorization: Bearer <token>"
     ```
     → **200** 이면 정상.

3. **추가 코드 수정**
   - **없음.** 인프라(ALB/서브넷/SG) 변경 없이, progress 엔드포인트만 수정하면 됨.

---

## 3. Redis로 진행률을 표시하는 로직 요약

### 3.1 사용하는 Redis 키 (tenant 네임스페이스)

| 키 | 용도 | 설정 주체 |
|----|------|------------|
| `tenant:{tenant_id}:video:{video_id}:status` | 비디오 상태 (PENDING, PROCESSING, READY, FAILED 등) | API(작업 등록 시) / 워커(진행·완료 시) |
| `tenant:{tenant_id}:video:{video_id}:progress` | 인코딩 진행률 payload (percent, step, remaining_seconds 등) | **워커만** (인코딩 중 `record_progress` 등으로 기록) |

- Legacy 호환: `job:video:{video_id}:progress` 도 조회함 (tenant 키 없을 때).

### 3.2 Progress API 동작 흐름 (`GET /api/v1/media/videos/{id}/progress/`)

1. **Redis 상태 조회**  
   `get_video_status_from_redis(tenant_id, video_id)`  
   - 키 없음 / `get()` 이 None 또는 빈 값 → **`json.loads` 호출 안 함**, `None` 반환.

2. **상태가 없을 때 (Batch 모드에서 자주 발생)**  
   - DB에서 해당 비디오의 `status`, `hls_path` 등 조회.  
   - **READY / FAILED** 이면 DB 기준으로 응답 (hls_path, duration, error_reason 등 포함).  
   - 그 외(레코드 없음, PENDING 등) → **`_default_progress_response(video_id)`** → `status: "PENDING"`, `progress: 0` 등으로 **200** 반환.

3. **상태가 있을 때 (Redis에 status 키 있음)**  
   - `cached_status` 가 dict인지 확인 후 사용.  
   - **PROCESSING** 이면 `get_video_encoding_progress`, `get_video_encoding_step_detail`, `get_video_encoding_remaining_seconds` 호출.  
     - 이 함수들은 내부적으로 **`tenant:{tenant_id}:video:{video_id}:progress`** (및 legacy 키)를 읽고, **값이 없거나 None이면 `json.loads` 호출하지 않고** None 반환.  
   - progress/step 값이 None이면 0 등으로 채워서 응답.  
   - **READY / FAILED** 이면 Redis에 캐시된 hls_path, duration, error_reason 등 포함해 응답.

4. **예외 처리**  
   - Redis 조회 예외, DB 조회 예외, 응답 구성 중 어떤 예외든 **전부 잡아서** `_default_progress_response(video_id)` 반환.  
   → **progress 엔드포인트는 예외로 인해 502를 내지 않도록** 되어 있음.

### 3.3 Batch 모드에서의 차이

- **이전(SQS/상시 워커)**: 워커가 메시지를 받자마자 Redis에 status(PROCESSING) 등을 세팅할 수 있어, progress 폴링 시 키가 있을 가능성이 높음.
- **Batch**: Job이 RUNNING 되기 전까지는 워커가 없음. 따라서 **업로드 완료 직후 ~ 워커 기동 후 첫 기록 전** 구간에서는 **status/progress 키가 없는 것이 정상**.  
  → 이제 이 구간에서도 **기본 응답(PENDING, progress 0)** 으로 200을 반환하므로 502/CORS가 발생하지 않음.

### 3.4 워커가 진행률을 쓰는 방식 (참고)

- 워커(Batch 컨테이너)는 인코딩 파이프라인 단계마다 `record_progress(job_id="video:{video_id}", step=..., extra=...)` 형태로 **progress 키**에 JSON을 기록.
- `encoding_progress.py`의 `_STEP_PERCENT`(presigning, downloading, probing, transcoding, validating, thumbnail, uploading)와 맞춰 단계별 % 가 계산됨.
- **Batch 워커도 동일한 Redis 키에 기록**하면, 워커 기동 후에는 프론트의 progress 폴링이 실시간 %/단계를 표시할 수 있음.

---

## 4. 최종 체크리스트

- [x] `encoding_progress.py`: `redis.get()` 결과 None/빈 값일 때 `json.loads` 미호출.
- [x] `redis_status_cache.py`: `get_video_status_from_redis`에서 동일 가드.
- [x] `progress_views.py`: Redis/DB 없음 또는 예외 시 `_default_progress_response` 로 200 반환.
- [ ] **배포** 후 API 서버에 반영.
- [ ] **실제 요청**으로 `/progress/` 200 응답 및 502/CORS 없음 확인.

이 문서는 progress 502 원인 수정과 Redis 기반 진행률 로직을 한 번에 참고하기 위한 최종 요약입니다.
