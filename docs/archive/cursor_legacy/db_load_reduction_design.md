# DB 부하 최소화 설계

## 🎯 목표

- 진행 중 작업: Redis만 조회 (DB 조회 제로)
- 완료된 작업: Redis 캐싱 (1시간 TTL)
- DB 조회: Redis 미스 시에만 폴백
- 작업박스 폴링: DB 부하 최소화

## 📋 설계 개요

### 1. 상태 저장 전략

```
진행 중:
  - 진행률: Redis (job:{id}:progress)
  - 상태: Redis (job:{id}:status) ← 새로 추가

완료 시:
  - Redis: 최종 상태 저장 (TTL 1시간)
  - DB: 영구 저장 (최종 결과)

조회 시:
  1. Redis에서 상태 조회 (진행률 + 상태)
  2. Redis 없으면 DB 조회 (폴백)
```

### 2. Redis 키 구조

```
# 진행률 (기존)
job:video:{video_id}:progress
job:{job_id}:progress

# 상태 (신규)
video:{video_id}:status
job:{job_id}:status
```

## 🔧 구현 설계

### 1. 비디오 워커: 완료 시 Redis에 상태 저장

**파일**: `apps/support/video/services/sqs_queue.py`

```python
def complete_video(
    self,
    video_id: int,
    hls_path: str,
    duration: Optional[int] = None,
) -> tuple[bool, str]:
    """비디오 처리 완료 처리"""
    video = get_video_for_update(video_id)
    if not video:
        return False, "not_found"
    
    # 멱등성: 이미 READY 상태면 OK
    if video.status == Video.Status.READY and bool(video.hls_path):
        return True, "idempotent"
    
    # DB 업데이트
    video.hls_path = str(hls_path)
    if duration is not None and duration >= 0:
        video.duration = int(duration)
    video.status = Video.Status.READY
    
    # lease 해제
    if hasattr(video, "leased_until"):
        video.leased_until = None
    if hasattr(video, "leased_by"):
        video.leased_by = ""
    
    update_fields = ["hls_path", "status"]
    if duration is not None and duration >= 0:
        update_fields.append("duration")
    if hasattr(video, "leased_until"):
        update_fields.append("leased_until")
    if hasattr(video, "leased_by"):
        update_fields.append("leased_by")
    
    video.save(update_fields=update_fields)
    
    # ✅ Redis에 최종 상태 저장 (TTL 1시간)
    try:
        from libs.redis.client import get_redis_client
        import json
        redis_client = get_redis_client()
        if redis_client:
            status_data = {
                "status": Video.Status.READY.value,
                "hls_path": hls_path,
                "duration": duration,
                "updated_at": video.updated_at.isoformat() if hasattr(video, "updated_at") else None,
            }
            redis_client.setex(
                f"video:{video_id}:status",
                3600,  # 1시간 TTL
                json.dumps(status_data, default=str)
            )
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
    
    return True, "ok"
```

**파일**: `apps/support/video/services/sqs_queue.py` (fail_video)

```python
@transaction.atomic
def fail_video(
    self,
    video_id: int,
    reason: str,
) -> tuple[bool, str]:
    """비디오 처리 실패 처리"""
    video = get_video_for_update(video_id)
    if not video:
        return False, "not_found"
    
    # 멱등성: 이미 FAILED 상태면 OK
    if video.status == Video.Status.FAILED:
        return True, "idempotent"
    
    # DB 업데이트
    video.status = Video.Status.FAILED
    if hasattr(video, "error_reason"):
        video.error_reason = str(reason)[:2000]
    
    # lease 해제
    if hasattr(video, "leased_until"):
        video.leased_until = None
    if hasattr(video, "leased_by"):
        video.leased_by = ""
    
    update_fields = ["status"]
    if hasattr(video, "error_reason"):
        update_fields.append("error_reason")
    if hasattr(video, "leased_until"):
        update_fields.append("leased_until")
    if hasattr(video, "leased_by"):
        update_fields.append("leased_by")
    
    video.save(update_fields=update_fields)
    
    # ✅ Redis에 실패 상태 저장 (TTL 1시간)
    try:
        from libs.redis.client import get_redis_client
        import json
        redis_client = get_redis_client()
        if redis_client:
            status_data = {
                "status": Video.Status.FAILED.value,
                "error_reason": str(reason)[:2000],
                "updated_at": video.updated_at.isoformat() if hasattr(video, "updated_at") else None,
            }
            redis_client.setex(
                f"video:{video_id}:status",
                3600,  # 1시간 TTL
                json.dumps(status_data, default=str)
            )
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
    
    return True, "ok"
```

**파일**: `apps/support/video/services/sqs_queue.py` (mark_processing)

```python
@transaction.atomic
def mark_processing(self, video_id: int) -> bool:
    """비디오를 PROCESSING 상태로 변경"""
    video = get_video_for_update(video_id)
    if not video:
        return False
    
    # 이미 PROCESSING이면 OK
    if video.status == Video.Status.PROCESSING:
        return True
    
    # UPLOADED 상태만 PROCESSING으로 변경 가능
    if video.status != Video.Status.UPLOADED:
        logger.warning(
            "Cannot mark video %s as PROCESSING: status=%s",
            video_id,
            video.status,
        )
        return False
    
    # DB 업데이트
    video.status = Video.Status.PROCESSING
    if hasattr(video, "processing_started_at"):
        video.processing_started_at = timezone.now()
    
    update_fields = ["status"]
    if hasattr(video, "processing_started_at"):
        update_fields.append("processing_started_at")
    
    video.save(update_fields=update_fields)
    
    # ✅ Redis에 PROCESSING 상태 저장 (TTL 2시간 - 인코딩 시간 고려)
    try:
        from libs.redis.client import get_redis_client
        import json
        redis_client = get_redis_client()
        if redis_client:
            status_data = {
                "status": Video.Status.PROCESSING.value,
                "processing_started_at": video.processing_started_at.isoformat() if hasattr(video, "processing_started_at") else None,
                "updated_at": video.updated_at.isoformat() if hasattr(video, "updated_at") else None,
            }
            redis_client.setex(
                f"video:{video_id}:status",
                7200,  # 2시간 TTL (인코딩 시간 고려)
                json.dumps(status_data, default=str)
            )
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
    
    return True
```

### 2. 비디오 조회: Redis 우선 조회

**파일**: `apps/support/video/views/video_views.py`

```python
from libs.redis.client import get_redis_client
import json

class VideoDetailView(RetrieveAPIView):
    """비디오 상세 조회 (Redis 우선)"""
    
    def get(self, request, pk):
        video_id = int(pk)
        
        # ✅ 1. Redis에서 상태 조회 시도
        cached_status = None
        try:
            redis_client = get_redis_client()
            if redis_client:
                cached_data = redis_client.get(f"video:{video_id}:status")
                if cached_data:
                    cached_status = json.loads(cached_data)
        except Exception as e:
            logger.debug("Redis status lookup failed: %s", e)
        
        # ✅ 2. Redis에 상태가 있고 PROCESSING이면 DB 조회 생략
        if cached_status and cached_status.get("status") == Video.Status.PROCESSING.value:
            # 진행률은 Redis에서 조회
            from apps.support.video.encoding_progress import (
                get_video_encoding_progress,
                get_video_encoding_step_detail,
            )
            
            progress = get_video_encoding_progress(video_id)
            step_detail = get_video_encoding_step_detail(video_id)
            
            # Redis 데이터로 응답 구성
            response_data = {
                "id": video_id,
                "status": cached_status["status"],
                "encoding_progress": progress,
                "encoding_step_index": step_detail.get("step_index") if step_detail else None,
                "encoding_step_total": step_detail.get("step_total") if step_detail else None,
                "encoding_step_name": step_detail.get("step_name_display") if step_detail else None,
                "encoding_step_percent": step_detail.get("step_percent") if step_detail else None,
                # 기타 필드는 최소한만 (또는 None)
            }
            return Response(response_data)
        
        # ✅ 3. Redis에 완료 상태가 있으면 DB 조회 생략 (1시간 내)
        if cached_status and cached_status.get("status") in [Video.Status.READY.value, Video.Status.FAILED.value]:
            # 완료된 비디오는 Redis 데이터로 응답
            response_data = {
                "id": video_id,
                "status": cached_status["status"],
                "hls_path": cached_status.get("hls_path"),
                "duration": cached_status.get("duration"),
                "error_reason": cached_status.get("error_reason"),
            }
            # 필요한 경우 DB에서 추가 필드 조회 (선택적)
            # video = Video.objects.only("title", "session_id", ...).get(id=video_id)
            return Response(response_data)
        
        # ✅ 4. Redis 없으면 DB 조회 (폴백)
        video = Video.objects.get(id=video_id)
        serializer = VideoSerializer(video)
        return Response(serializer.data)
```

### 3. AI Job: 완료 시 Redis에 상태 저장

**파일**: `academy/adapters/db/django/repositories_ai.py`

```python
def save(self, job: AIJob) -> None:
    """AIJob 저장 (완료 시 Redis에도 저장)"""
    from django.utils import timezone
    from apps.domains.ai.models import AIJobModel
    now = timezone.now()
    
    # DB 저장
    model, created = AIJobModel.objects.update_or_create(
        job_id=job.job_id,
        defaults={
            "job_type": job.job_type,
            "status": job.status.value,
            "payload": job.payload,
            "tenant_id": job.tenant_id,
            "error_message": job.error_message,
            "updated_at": now,
        }
    )
    
    # ✅ 완료/실패 시 Redis에 상태 저장 (TTL 1시간)
    if job.status.value in ["DONE", "FAILED"]:
        try:
            from libs.redis.client import get_redis_client
            import json
            redis_client = get_redis_client()
            if redis_client:
                status_data = {
                    "status": job.status.value,
                    "job_type": job.job_type,
                    "error_message": job.error_message,
                    "updated_at": model.updated_at.isoformat() if hasattr(model, "updated_at") else None,
                }
                redis_client.setex(
                    f"job:{job.job_id}:status",
                    3600,  # 1시간 TTL
                    json.dumps(status_data, default=str)
                )
        except Exception as e:
            logger.warning("Failed to cache job status in Redis: %s", e)
    
    # ✅ PROCESSING 상태도 Redis에 저장 (TTL 2시간)
    elif job.status.value == "PROCESSING":
        try:
            from libs.redis.client import get_redis_client
            import json
            redis_client = get_redis_client()
            if redis_client:
                status_data = {
                    "status": job.status.value,
                    "job_type": job.job_type,
                    "updated_at": model.updated_at.isoformat() if hasattr(model, "updated_at") else None,
                }
                redis_client.setex(
                    f"job:{job.job_id}:status",
                    7200,  # 2시간 TTL
                    json.dumps(status_data, default=str)
                )
        except Exception as e:
            logger.warning("Failed to cache job status in Redis: %s", e)
```

### 4. AI Job 조회: Redis 우선 조회

**파일**: `apps/domains/ai/views/job_status_view.py`

```python
class JobStatusView(APIView):
    """Job 상태 조회 (Redis 우선)"""
    
    def get(self, request, job_id: str):
        try:
            tenant = getattr(request, "tenant", None)
            if not tenant:
                return Response(
                    {"detail": "tenant가 필요합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            # ✅ 1. Redis에서 상태 조회 시도
            cached_status = None
            try:
                from libs.redis.client import get_redis_client
                import json
                redis_client = get_redis_client()
                if redis_client:
                    cached_data = redis_client.get(f"job:{job_id}:status")
                    if cached_data:
                        cached_status = json.loads(cached_data)
            except Exception as e:
                logger.debug("Redis status lookup failed: %s", e)
            
            # ✅ 2. Redis에 상태가 있고 PROCESSING이면 DB 조회 생략
            if cached_status and cached_status.get("status") == "PROCESSING":
                # 진행률은 Redis에서 조회
                from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
                progress = RedisProgressAdapter().get_progress(job_id)
                
                # Redis 데이터로 응답 구성
                response_data = {
                    "job_id": job_id,
                    "job_type": cached_status.get("job_type"),
                    "status": cached_status["status"],
                    "progress": progress,
                    "error_message": None,
                    "result": None,
                }
                return Response(response_data)
            
            # ✅ 3. Redis에 완료 상태가 있으면 DB 조회 생략 (1시간 내)
            if cached_status and cached_status.get("status") in ["DONE", "FAILED"]:
                # result는 DB에서만 조회 (완료 시에만 필요)
                repo = _ai_repo()
                job = repo.get_job_model_for_status(job_id, str(tenant.id))
                if job:
                    result_payload = repo.get_result_payload_for_job(job)
                    response_data = {
                        "job_id": job_id,
                        "job_type": cached_status.get("job_type"),
                        "status": cached_status["status"],
                        "error_message": cached_status.get("error_message"),
                        "result": result_payload,
                        "progress": None,  # 완료된 작업은 진행률 없음
                    }
                    return Response(response_data)
            
            # ✅ 4. Redis 없으면 DB 조회 (폴백)
            repo = _ai_repo()
            job = repo.get_job_model_for_status(job_id, str(tenant.id))
            if not job:
                return Response(
                    {"detail": "해당 job을 찾을 수 없습니다."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            result_payload = repo.get_result_payload_for_job(job)
            return Response(build_job_status_response(job, result_payload=result_payload))
            
        except Exception as e:
            logger.exception("JobStatusView get job_id=%s: %s", job_id, e)
            return Response(
                {"detail": "job 상태 조회 중 오류가 발생했습니다.", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
```

### 5. 헬퍼 함수: Redis 상태 조회

**파일**: `apps/support/video/redis_status_cache.py` (신규)

```python
"""비디오 상태 Redis 캐싱 헬퍼"""
from typing import Optional, Dict, Any
from libs.redis.client import get_redis_client
import json
import logging

logger = logging.getLogger(__name__)


def get_video_status_from_redis(video_id: int) -> Optional[Dict[str, Any]]:
    """Redis에서 비디오 상태 조회"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return None
        
        cached_data = redis_client.get(f"video:{video_id}:status")
        if not cached_data:
            return None
        
        return json.loads(cached_data)
    except Exception as e:
        logger.debug("Redis video status lookup failed: %s", e)
        return None


def cache_video_status(
    video_id: int,
    status: str,
    hls_path: Optional[str] = None,
    duration: Optional[int] = None,
    error_reason: Optional[str] = None,
    ttl: int = 3600,
) -> bool:
    """비디오 상태를 Redis에 캐싱"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        
        status_data = {
            "status": status,
        }
        if hls_path is not None:
            status_data["hls_path"] = hls_path
        if duration is not None:
            status_data["duration"] = duration
        if error_reason is not None:
            status_data["error_reason"] = error_reason
        
        redis_client.setex(
            f"video:{video_id}:status",
            ttl,
            json.dumps(status_data, default=str)
        )
        return True
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
        return False
```

**파일**: `apps/domains/ai/redis_status_cache.py` (신규)

```python
"""AI Job 상태 Redis 캐싱 헬퍼"""
from typing import Optional, Dict, Any
from libs.redis.client import get_redis_client
import json
import logging

logger = logging.getLogger(__name__)


def get_job_status_from_redis(job_id: str) -> Optional[Dict[str, Any]]:
    """Redis에서 Job 상태 조회"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return None
        
        cached_data = redis_client.get(f"job:{job_id}:status")
        if not cached_data:
            return None
        
        return json.loads(cached_data)
    except Exception as e:
        logger.debug("Redis job status lookup failed: %s", e)
        return None


def cache_job_status(
    job_id: str,
    status: str,
    job_type: Optional[str] = None,
    error_message: Optional[str] = None,
    ttl: int = 3600,
) -> bool:
    """Job 상태를 Redis에 캐싱"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        
        status_data = {
            "status": status,
        }
        if job_type is not None:
            status_data["job_type"] = job_type
        if error_message is not None:
            status_data["error_message"] = error_message
        
        redis_client.setex(
            f"job:{job_id}:status",
            ttl,
            json.dumps(status_data, default=str)
        )
        return True
    except Exception as e:
        logger.warning("Failed to cache job status in Redis: %s", e)
        return False
```

## 📊 예상 효과

### Before (현재)
- 비디오 3개 + 엑셀 2개 진행 중
- 초당: 5번 DB SELECT
- 10분: 약 3,000번 DB SELECT
- RDS CPU: 80-100%

### After (개선 후)
- 비디오 3개 + 엑셀 2개 진행 중
- 초당: 0번 DB SELECT (진행 중 작업은 Redis만 조회)
- 완료 후 1시간 내: 0번 DB SELECT (Redis 캐싱)
- 완료 후 1시간 이후: 필요 시에만 DB SELECT
- RDS CPU: 10-20% (대폭 감소)

## 🎯 구현 순서

1. **1단계**: 헬퍼 함수 생성
   - `apps/support/video/redis_status_cache.py`
   - `apps/domains/ai/redis_status_cache.py`

2. **2단계**: 완료 시 Redis 저장
   - `apps/support/video/services/sqs_queue.py` (complete_video, fail_video, mark_processing)
   - `academy/adapters/db/django/repositories_ai.py` (save)

3. **3단계**: 조회 시 Redis 우선
   - `apps/support/video/views/video_views.py` (VideoDetailView)
   - `apps/domains/ai/views/job_status_view.py` (JobStatusView)

4. **4단계**: 테스트 및 모니터링
   - 진행 중 작업: Redis만 조회 확인
   - 완료된 작업: Redis 캐싱 확인
   - DB 쿼리 수 모니터링

## ⚠️ 주의사항

1. **TTL 관리**
   - 진행 중: 2시간 (인코딩 시간 고려)
   - 완료: 1시간 (충분한 조회 시간)

2. **폴백 전략**
   - Redis 실패 시 DB 조회 (안정성)
   - Redis 없으면 DB 조회 (호환성)

3. **데이터 일관성**
   - 완료 시 Redis와 DB 동시 업데이트
   - Redis 실패해도 DB는 저장됨

4. **메모리 사용**
   - Redis 메모리 모니터링
   - TTL로 자동 정리

## 📈 모니터링

### CloudWatch 메트릭
- RDS CPUUtilization: 80% → 20% 예상
- RDS DatabaseConnections: 감소 예상
- Redis MemoryUsage: 증가 (TTL로 관리)

### 로그 확인
- Redis 캐싱 실패 로그 모니터링
- DB 조회 빈도 감소 확인
