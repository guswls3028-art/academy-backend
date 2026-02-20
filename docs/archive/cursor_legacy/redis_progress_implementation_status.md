# Redis 진행률 구현 상태 체크리스트

## ✅ 이미 Redis로 구현된 부분

### 1. 비디오 워커
- **진행률 기록**: Redis 사용 ✅
  - `src/infrastructure/video/processor.py`: `progress.record_progress()` → Redis
  - 키: `job:video:{video_id}:progress`
- **진행률 조회**: Redis 사용 ✅
  - `apps/support/video/encoding_progress.py`: `get_video_encoding_progress()` → Redis
  - `apps/support/video/serializers.py`: `get_encoding_progress()` → Redis
- **최종 상태**: DB 사용 (필요)
  - 완료 시: `repo.complete_video()` → DB 업데이트
  - 상태 조회: `VideoSerializer` → DB에서 Video 모델 조회

### 2. AI 워커
- **진행률 기록**: Redis 사용 ✅
  - `apps/worker/ai_worker/ai/pipelines/dispatcher.py`: `_record_progress()` → Redis
  - `apps/worker/ai_worker/ai/pipelines/excel_handler.py`: `_record_progress()` → Redis
  - 키: `job:{job_id}:progress`
- **진행률 조회**: Redis 사용 ✅
  - `apps/domains/ai/services/job_status_response.py`: `RedisProgressAdapter().get_progress()` → Redis
- **최종 상태**: DB 사용 (필요)
  - 완료 시: `AIJobModel` → DB 업데이트
  - 상태 조회: `JobStatusView` → DB에서 AIJobModel 조회

### 3. 메시지 워커
- **진행률 기록**: Redis 사용 ✅
  - `apps/worker/messaging_worker/sqs_main.py`: `_record_progress()` → Redis
  - 키: `job:{job_id}:progress`
- **진행률 조회**: Redis 사용 ✅
  - `apps/domains/ai/services/job_status_response.py`: `RedisProgressAdapter().get_progress()` → Redis
- **최종 상태**: DB 사용 (필요)
  - 완료 시: `AIJobModel` → DB 업데이트
  - 상태 조회: `JobStatusView` → DB에서 AIJobModel 조회

## ⚠️ 문제점: 여전히 DB를 조회하는 부분

### 1. 작업박스 폴링 (프론트엔드)

#### 비디오 진행률 조회 (`GET /media/videos/${videoId}/`)
- **진행률**: Redis에서 읽음 ✅
- **하지만**: Video 모델을 DB에서 조회함 ⚠️
  ```python
  # apps/support/video/views/video_views.py
  video = Video.objects.get(id=video_id)  # DB 조회
  return Response(VideoSerializer(video).data)
  ```
- **빈도**: 1초마다 폴링 → 초당 1번 DB SELECT

#### 엑셀/Job 상태 조회 (`GET /api/v1/jobs/<job_id>/`)
- **진행률**: Redis에서 읽음 ✅
- **하지만**: AIJobModel을 DB에서 조회함 ⚠️
  ```python
  # apps/domains/ai/views/job_status_view.py
  job = repo.get_job_model_for_status(job_id, tenant_id)  # DB 조회
  return Response(build_job_status_response(job, ...))
  ```
- **빈도**: 1초마다 폴링 → 초당 1번 DB SELECT

### 2. 최종 상태 조회가 필요한 이유

#### 비디오
- `status`: PROCESSING → READY/FAILED (완료 시 변경)
- `hls_path`: 완료 시 설정
- `duration`: 완료 시 설정
- `error_reason`: 실패 시 설정

#### AI Job
- `status`: PENDING → PROCESSING → DONE/FAILED (완료 시 변경)
- `error_message`: 실패 시 설정
- `result`: 완료 시 설정 (예: download_url)

## 💡 개선 방안

### 방안 1: 최종 상태도 Redis에 저장 (권장)

**개념:**
- 진행 중: Redis에 진행률 + 상태 저장
- 완료 시: Redis에 최종 상태 저장 + DB에 영구 저장
- 조회 시: Redis 우선, 없으면 DB 폴백

**구현:**
```python
# 완료 시 Redis에 최종 상태 저장
def complete_video(video_id, hls_path, duration):
    # DB 업데이트
    video.status = Video.Status.READY
    video.hls_path = hls_path
    video.duration = duration
    video.save()
    
    # Redis에 최종 상태 저장 (TTL 1시간)
    redis_client.setex(
        f"video:{video_id}:status",
        3600,
        json.dumps({
            "status": "READY",
            "hls_path": hls_path,
            "duration": duration,
        })
    )
```

**조회 시:**
```python
# Redis 우선 조회
status_data = redis_client.get(f"video:{video_id}:status")
if status_data:
    return json.loads(status_data)
# Redis 없으면 DB 조회
video = Video.objects.get(id=video_id)
return {"status": video.status, ...}
```

**장점:**
- DB 부하 대폭 감소 (진행 중 작업은 Redis만 조회)
- 완료된 작업도 1시간 동안 Redis에서 조회 가능
- 기존 코드 변경 최소화

**단점:**
- Redis 메모리 사용 증가 (TTL로 관리)
- 완료 후 1시간 지나면 DB 조회 필요

### 방안 2: 캐싱 레이어 추가

**개념:**
- Django Cache Framework 사용
- Video/AIJob 조회 결과 캐싱
- 진행 중 작업은 짧은 TTL (5초)
- 완료된 작업은 긴 TTL (1시간)

**구현:**
```python
from django.core.cache import cache

def get_video_with_cache(video_id):
    cache_key = f"video:{video_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    video = Video.objects.get(id=video_id)
    ttl = 5 if video.status == Video.Status.PROCESSING else 3600
    cache.set(cache_key, video, ttl)
    return video
```

**장점:**
- 기존 코드 변경 최소화
- Django Cache Framework 활용 (Redis 백엔드 사용 가능)

**단점:**
- 여전히 DB 조회 발생 (캐시 미스 시)

### 방안 3: 폴링 간격 조정 (단기)

**개념:**
- 진행 중: 2-3초 간격으로 폴링
- 완료 후: 즉시 조회 후 폴링 중지

**구현:**
```typescript
// useWorkerJobPoller.ts
const POLL_INTERVAL_MS = 2000; // 1초 → 2초
```

**장점:**
- 즉시 적용 가능
- DB 부하 50% 감소

**단점:**
- 완전한 해결책은 아님

## 📊 현재 DB 부하 분석

### 작업박스 폴링으로 인한 DB 부하

**시나리오:**
- 비디오 3개 인코딩 중
- 엑셀 작업 2개 진행 중
- 각 작업당 1초마다 폴링

**DB 쿼리:**
- 초당: 5번 SELECT (Video 3개 + AIJob 2개)
- 10분 인코딩: 약 3,000번 SELECT
- 1시간 인코딩: 약 18,000번 SELECT

**RDS 부하:**
- `db.t4g.micro`: CPU 100% 가능성 높음
- `db.t4g.small`: CPU 50-80% 가능성
- `db.t4g.medium`: CPU 20-40% (안전)

## 🎯 권장 조치 순서

### 즉시 (1순위)
1. **RDS 인스턴스 크기 증가** (`db.t4g.medium`)
   - 현재 DB 부하 문제 해결
   - 비용 대비 효과적

### 단기 (1주일 내)
2. **폴링 간격 조정** (2초)
   - DB 부하 50% 감소
   - 즉시 적용 가능

### 중기 (1개월 내)
3. **최종 상태도 Redis에 저장**
   - 진행 중 작업: Redis만 조회
   - 완료 후 1시간: Redis 조회
   - DB 부하 대폭 감소

### 장기 (3개월 내)
4. **WebSocket 도입**
   - 폴링 제거
   - 실시간 푸시
   - DB 부하 제로

## 📝 체크리스트

- [x] 비디오 워커 진행률: Redis 사용
- [x] AI 워커 진행률: Redis 사용
- [x] 메시지 워커 진행률: Redis 사용
- [x] 진행률 조회: Redis 사용
- [ ] **최종 상태 조회: 여전히 DB 사용** ⚠️
- [ ] **작업박스 폴링: DB 조회 발생** ⚠️
- [ ] 최종 상태도 Redis에 저장 (개선 필요)
- [ ] 폴링 간격 조정 (개선 필요)
