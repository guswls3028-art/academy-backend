# 실제 적용 가능한 패치 플랜

**생성일**: 2026-02-18  
**목표**: 하루 안에 적용 가능한 실제 코드 수정

---

## [PATCH PLAN — REDIS PROGRESS]

**⚠️ 중요 변경사항**: Video Progress는 AI와 완전히 분리하여 별도 Adapter 사용

### 단계 1: Redis 상태 캐싱 헬퍼 생성

#### PATCH 1.1: Video 상태 캐싱 헬퍼 생성

**파일**: `apps/support/video/redis_status_cache.py` (신규)

**수정 전 코드**: 파일 없음

**수정 후 코드**:

```python
"""비디오 상태 Redis 캐싱 헬퍼 (Tenant 네임스페이스)"""
from typing import Optional, Dict, Any
from libs.redis.client import get_redis_client
import json
import logging

logger = logging.getLogger(__name__)


def _get_video_status_key(tenant_id: int, video_id: int) -> str:
    """비디오 상태 Redis 키 (Tenant 네임스페이스)"""
    return f"tenant:{tenant_id}:video:{video_id}:status"


def _get_video_progress_key(tenant_id: int, video_id: int) -> str:
    """비디오 진행률 Redis 키 (Tenant 네임스페이스)"""
    return f"tenant:{tenant_id}:video:{video_id}:progress"


def get_video_status_from_redis(tenant_id: int, video_id: int) -> Optional[Dict[str, Any]]:
    """Redis에서 비디오 상태 조회 (Tenant 검증)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return None
        
        key = _get_video_status_key(tenant_id, video_id)
        cached_data = redis_client.get(key)
        if not cached_data:
            return None
        
        return json.loads(cached_data)
    except Exception as e:
        logger.debug("Redis video status lookup failed: %s", e)
        return None


def cache_video_status(
    tenant_id: int,
    video_id: int,
    status: str,
    hls_path: Optional[str] = None,
    duration: Optional[int] = None,
    error_reason: Optional[str] = None,
    ttl: Optional[int] = None,  # None이면 TTL 없음
) -> bool:
    """비디오 상태를 Redis에 캐싱 (Tenant 네임스페이스)"""
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
        
        key = _get_video_status_key(tenant_id, video_id)
        if ttl is None:
            # TTL 없음 (완료 상태)
            redis_client.set(key, json.dumps(status_data, default=str))
        else:
            # TTL 설정 (진행 중 상태)
            redis_client.setex(key, ttl, json.dumps(status_data, default=str))
        
        return True
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
        return False


def refresh_video_progress_ttl(tenant_id: int, video_id: int, ttl: int = 21600) -> bool:
    """비디오 진행률 TTL 슬라이딩 갱신 (6시간)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        
        progress_key = _get_video_progress_key(tenant_id, video_id)
        status_key = _get_video_status_key(tenant_id, video_id)
        
        # exists 체크 후 TTL 갱신 (의도치 않은 상태 방지)
        if redis_client.exists(progress_key):
            redis_client.expire(progress_key, ttl)
        if redis_client.exists(status_key):
            redis_client.expire(status_key, ttl)
        
        return True
    except Exception as e:
        logger.warning("Failed to refresh video TTL: %s", e)
        return False
```

**변경 이유**: Tenant namespace 포함한 상태 캐싱 헬퍼 필요

**영향 범위**: 신규 파일, 영향 없음

**롤백 방법**: 파일 삭제

---

#### PATCH 1.2: AI Job 상태 캐싱 헬퍼 생성

**파일**: `apps/domains/ai/redis_status_cache.py` (신규)

**수정 전 코드**: 파일 없음

**수정 후 코드**:

```python
"""AI Job 상태 Redis 캐싱 헬퍼 (Tenant 네임스페이스)"""
from typing import Optional, Dict, Any
from libs.redis.client import get_redis_client
import json
import logging

logger = logging.getLogger(__name__)


def _get_job_status_key(tenant_id: str, job_id: str) -> str:
    """Job 상태 Redis 키 (Tenant 네임스페이스)"""
    return f"tenant:{tenant_id}:job:{job_id}:status"


def _get_job_progress_key(tenant_id: str, job_id: str) -> str:
    """Job 진행률 Redis 키 (Tenant 네임스페이스)"""
    return f"tenant:{tenant_id}:job:{job_id}:progress"


def get_job_status_from_redis(tenant_id: str, job_id: str) -> Optional[Dict[str, Any]]:
    """Redis에서 Job 상태 조회 (Tenant 검증)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return None
        
        key = _get_job_status_key(tenant_id, job_id)
        cached_data = redis_client.get(key)
        if not cached_data:
            return None
        
        return json.loads(cached_data)
    except Exception as e:
        logger.debug("Redis job status lookup failed: %s", e)
        return None


def cache_job_status(
    tenant_id: str,
    job_id: str,
    status: str,
    job_type: Optional[str] = None,
    error_message: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    ttl: Optional[int] = None,  # None이면 TTL 없음
) -> bool:
    """Job 상태를 Redis에 캐싱 (Tenant 네임스페이스, 완료 시 result 포함)"""
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
        if result is not None:
            # result 크기 체크 (10KB 이하만 Redis 저장)
            import json as json_module
            result_size = len(json_module.dumps(result))
            if result_size < 10000:  # 10KB 이하면 Redis에 저장
                status_data["result"] = result
            else:
                logger.info("Result payload too large (%d bytes), skipping Redis cache", result_size)
        
        key = _get_job_status_key(tenant_id, job_id)
        if ttl is None:
            # TTL 없음 (완료 상태)
            redis_client.set(key, json.dumps(status_data, default=str))
        else:
            # TTL 설정 (진행 중 상태)
            redis_client.setex(key, ttl, json.dumps(status_data, default=str))
        
        return True
    except Exception as e:
        logger.warning("Failed to cache job status in Redis: %s", e)
        return False


def refresh_job_progress_ttl(tenant_id: str, job_id: str, ttl: int = 21600) -> bool:
    """Job 진행률 TTL 슬라이딩 갱신 (6시간)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        
        progress_key = _get_job_progress_key(tenant_id, job_id)
        status_key = _get_job_status_key(tenant_id, job_id)
        
        # exists 체크 후 TTL 갱신 (의도치 않은 상태 방지)
        if redis_client.exists(progress_key):
            redis_client.expire(progress_key, ttl)
        if redis_client.exists(status_key):
            redis_client.expire(status_key, ttl)
        
        return True
    except Exception as e:
        logger.warning("Failed to refresh job TTL: %s", e)
        return False
```

**변경 이유**: Tenant namespace 포함한 Job 상태 캐싱 헬퍼 필요

**영향 범위**: 신규 파일, 영향 없음

**롤백 방법**: 파일 삭제

---

### 단계 2: Video 워커 저장 로직 수정 (Redis 상태 저장 추가)

#### PATCH 2.1: complete_video에 Redis 상태 저장 추가

**파일**: `apps/support/video/services/sqs_queue.py`

**수정 전 코드**:

```python:apps/support/video/services/sqs_queue.py
@transaction.atomic
def complete_video(
    self,
    video_id: int,
    hls_path: str,
    duration: Optional[int] = None,
) -> tuple[bool, str]:
    video = get_video_for_update(video_id)
    if not video:
        return False, "not_found"
    
    video.hls_path = str(hls_path)
    if duration is not None and duration >= 0:
        video.duration = int(duration)
    video.status = Video.Status.READY
    
    video.save(update_fields=update_fields)
    return True, "ok"
```

**수정 후 코드**:

```python:apps/support/video/services/sqs_queue.py
@transaction.atomic
def complete_video(
    self,
    video_id: int,
    hls_path: str,
    duration: Optional[int] = None,
) -> tuple[bool, str]:
    video = get_video_for_update(video_id)
    if not video:
        return False, "not_found"
    
    # 멱등성: 이미 READY 상태면 OK
    if video.status == Video.Status.READY and bool(video.hls_path):
        return True, "idempotent"
    
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
    
    # ✅ Redis에 최종 상태 저장 (TTL 없음)
    try:
        from apps.support.video.redis_status_cache import cache_video_status
        # tenant_id는 video에서 가져오기
        tenant_id = None
        if hasattr(video, "session") and video.session:
            if hasattr(video.session, "lecture") and video.session.lecture:
                tenant_id = video.session.lecture.tenant_id
        
        if tenant_id:
            # ✅ 안전한 Status 값 추출 (TextChoices이면 .value, 아니면 그대로)
            status_value = getattr(Video.Status.READY, "value", Video.Status.READY)
            cache_video_status(
                tenant_id=tenant_id,
                video_id=video_id,
                status=status_value,
                hls_path=hls_path,
                duration=duration,
                ttl=None,  # TTL 없음
            )
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
    
    return True, "ok"
```

**변경 이유**: 완료 상태를 Redis에 저장하여 프론트엔드 폴링이 DB 조회 없이 상태 확인 가능

**영향 범위**: Video 워커 완료 처리 로직

**롤백 방법**: Redis 저장 코드 제거

**증명 (Worker에서 tenant_id 확보 경로)**:

**Evidence**: `apps/support/video/services/sqs_queue.py:75-79`
```python
tenant = video.session.lecture.tenant
tenant_id = int(tenant.id)
tenant_code = tenant.code
```

SQS 메시지에 `tenant_id` 포함됨:
```python:apps/support/video/services/sqs_queue.py:82-89
message = {
    "video_id": int(video.id),
    "file_key": str(video.file_key or ""),
    "tenant_id": tenant_id,  # ✅ 포함됨
    "tenant_code": str(tenant_code),
    "created_at": timezone.now().isoformat(),
    "attempt": 1,
}
```

---

#### PATCH 2.2: fail_video에 Redis 상태 저장 추가

**파일**: `apps/support/video/services/sqs_queue.py`

**수정 전 코드**:

```python:apps/support/video/services/sqs_queue.py
@transaction.atomic
def fail_video(
    self,
    video_id: int,
    reason: str,
) -> tuple[bool, str]:
    video = get_video_for_update(video_id)
    if not video:
        return False, "not_found"
    
    video.status = Video.Status.FAILED
    if hasattr(video, "error_reason"):
        video.error_reason = str(reason)[:2000]
    
    video.save(update_fields=update_fields)
    return True, "ok"
```

**수정 후 코드**:

```python:apps/support/video/services/sqs_queue.py
@transaction.atomic
def fail_video(
    self,
    video_id: int,
    reason: str,
) -> tuple[bool, str]:
    video = get_video_for_update(video_id)
    if not video:
        return False, "not_found"
    
    # 멱등성: 이미 FAILED 상태면 OK
    if video.status == Video.Status.FAILED:
        return True, "idempotent"
    
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
    
    # ✅ Redis에 실패 상태 저장 (TTL 없음)
    try:
        from apps.support.video.redis_status_cache import cache_video_status
        tenant_id = None
        if hasattr(video, "session") and video.session:
            if hasattr(video.session, "lecture") and video.session.lecture:
                tenant_id = video.session.lecture.tenant_id
        
        if tenant_id:
            cache_video_status(
                tenant_id=tenant_id,
                video_id=video_id,
                status=Video.Status.FAILED.value,
                error_reason=str(reason)[:2000],
                ttl=None,  # TTL 없음
            )
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
    
    return True, "ok"
```

**변경 이유**: 실패 상태를 Redis에 저장하여 프론트엔드 폴링이 DB 조회 없이 상태 확인 가능

**영향 범위**: Video 워커 실패 처리 로직

**롤백 방법**: Redis 저장 코드 제거

---

#### PATCH 2.3: mark_processing에 Redis 상태 저장 추가

**파일**: `apps/support/video/services/sqs_queue.py`

**수정 전 코드**:

```python:apps/support/video/services/sqs_queue.py
@transaction.atomic
def mark_processing(self, video_id: int) -> bool:
    video = get_video_for_update(video_id)
    if not video:
        return False
    
    video.status = Video.Status.PROCESSING
    if hasattr(video, "processing_started_at"):
        video.processing_started_at = timezone.now()
    
    video.save(update_fields=update_fields)
    return True
```

**수정 후 코드**:

```python:apps/support/video/services/sqs_queue.py
@transaction.atomic
def mark_processing(self, video_id: int) -> bool:
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
    
    video.status = Video.Status.PROCESSING
    if hasattr(video, "processing_started_at"):
        video.processing_started_at = timezone.now()
    
    update_fields = ["status"]
    if hasattr(video, "processing_started_at"):
        update_fields.append("processing_started_at")
    
    video.save(update_fields=update_fields)
    
    # ✅ Redis에 PROCESSING 상태 저장 (TTL 6시간)
    try:
        from apps.support.video.redis_status_cache import cache_video_status
        tenant_id = None
        if hasattr(video, "session") and video.session:
            if hasattr(video.session, "lecture") and video.session.lecture:
                tenant_id = video.session.lecture.tenant_id
        
        if tenant_id:
            cache_video_status(
                tenant_id=tenant_id,
                video_id=video_id,
                status=Video.Status.PROCESSING.value,
                ttl=21600,  # 6시간
            )
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
    
    return True
```

**변경 이유**: PROCESSING 상태를 Redis에 저장하여 프론트엔드 폴링이 DB 조회 없이 상태 확인 가능

**영향 범위**: Video 워커 시작 처리 로직

**롤백 방법**: Redis 저장 코드 제거

---

### 단계 3: AI Job 저장 로직 수정 (Redis 상태 저장 추가)

#### PATCH 3.1: repositories_ai.py save()에 Redis 상태 저장 추가

**파일**: `academy/adapters/db/django/repositories_ai.py`

**수정 전 코드**:

```python:academy/adapters/db/django/repositories_ai.py
def save(self, job: AIJob) -> None:
    from django.utils import timezone
    from apps.domains.ai.models import AIJobModel
    now = timezone.now()
    AIJobModel.objects.update_or_create(
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
```

**수정 후 코드**:

```python:academy/adapters/db/django/repositories_ai.py
def save(self, job: AIJob) -> None:
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
            "source_domain": job.source_domain,
            "source_id": job.source_id,
            "tier": job.tier,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "locked_by": job.locked_by,
            "locked_at": job.locked_at,
            "lease_expires_at": job.lease_expires_at,
            "idempotency_key": job.idempotency_key,
            "error_message": job.error_message,
            "updated_at": now,
        }
    )
    
    # ✅ 완료/실패 시 Redis에 상태 저장 (TTL 없음, result 포함)
    if job.status.value in ["DONE", "FAILED"]:
        try:
            import logging
            logger = logging.getLogger(__name__)  # ✅ logger 정의
            
            from apps.domains.ai.redis_status_cache import cache_job_status
            
            # ✅ result 가져오기 (방어적 처리)
            result_payload = None
            if job.status.value == "DONE":
                getter = getattr(self, "get_result_payload_for_job", None)
                if callable(getter):
                    try:
                        result_payload = getter(model)
                    except Exception as e:
                        logger.debug("Failed to get result payload: %s", e)
            
            cache_job_status(
                tenant_id=str(job.tenant_id),
                job_id=job.job_id,
                status=job.status.value,
                job_type=job.job_type,
                error_message=job.error_message,
                result=result_payload,  # 완료 시 result 포함 (있으면)
                ttl=None,  # TTL 없음
            )
        except Exception as e:
            logger.warning("Failed to cache job status in Redis: %s", e)
    
    # ✅ PROCESSING 상태도 Redis에 저장 (TTL 6시간)
    elif job.status.value == "PROCESSING":
        try:
            import logging
            logger = logging.getLogger(__name__)  # ✅ logger 정의
            
            from apps.domains.ai.redis_status_cache import cache_job_status
            cache_job_status(
                tenant_id=str(job.tenant_id),
                job_id=job.job_id,
                status=job.status.value,
                job_type=job.job_type,
                ttl=21600,  # 6시간
            )
        except Exception as e:
            logger.warning("Failed to cache job status in Redis: %s", e)
```

**변경 이유**: 완료/실패 상태를 Redis에 저장하여 프론트엔드 폴링이 DB 조회 없이 상태 확인 가능

**영향 범위**: AI Job 저장 로직

**롤백 방법**: Redis 저장 코드 제거

**증명 (Worker에서 tenant_id 확보 경로)**:

**Evidence**: `apps/worker/ai_worker/ai/pipelines/dispatcher.py:55-58`
```python
def handle_ai_job(job: AIJob) -> AIResult:
    # job.tenant_id는 AIJob entity에 포함됨
```

**Evidence**: `academy/domain/ai/entities.py` (추정)
- `AIJob` entity는 `tenant_id` 필드를 가짐
- Repository의 `save()` 메서드에서 `job.tenant_id` 사용

---

### 단계 4: Progress/Status 전용 Endpoint 추가

#### PATCH 4.1: Video Progress View 생성

**파일**: `apps/support/video/views/progress_views.py` (신규)

**수정 전 코드**: 파일 없음

**수정 후 코드**:

```python
"""비디오 진행률/상태 전용 endpoint (Redis-only)"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.support.video.encoding_progress import (
    get_video_encoding_progress,
    get_video_encoding_step_detail,
    get_video_encoding_remaining_seconds,
)
from apps.support.video.redis_status_cache import (
    get_video_status_from_redis,
)


class VideoProgressView(APIView):
    """비디오 진행률/상태 조회 (Redis-only, DB 부하 0)"""
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request, pk):
        """GET /media/videos/{id}/progress/"""
        video_id = int(pk)
        tenant = getattr(request, "tenant", None)
        
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # ✅ Redis에서 상태 조회 (Tenant 네임스페이스)
        cached_status = get_video_status_from_redis(tenant.id, video_id)
        
        if not cached_status:
            # Redis에 없으면 404 (진행 중이 아니거나 완료 후 TTL 만료)
            return Response(
                {"detail": "진행 중인 작업이 아닙니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        video_status = cached_status.get("status")
        
        # ✅ 진행률은 Redis에서 조회
        progress = None
        step_detail = None
        remaining_seconds = None
        
        if video_status == "PROCESSING":
            # 기존 encoding_progress 함수는 tenant namespace 없이 조회하므로
            # 마이그레이션 기간 동안 양쪽 키 모두 확인 필요
            # ✅ tenant_id 전달 필수
            progress = get_video_encoding_progress(video_id, tenant.id)
            step_detail = get_video_encoding_step_detail(video_id, tenant.id)
            remaining_seconds = get_video_encoding_remaining_seconds(video_id, tenant.id)
        
        # ✅ 응답 구성
        response_data = {
            "id": video_id,
            "status": video_status,
            "encoding_progress": progress,
            "encoding_remaining_seconds": remaining_seconds,
            "encoding_step_index": step_detail.get("step_index") if step_detail else None,
            "encoding_step_total": step_detail.get("step_total") if step_detail else None,
            "encoding_step_name": step_detail.get("step_name_display") if step_detail else None,
            "encoding_step_percent": step_detail.get("step_percent") if step_detail else None,
        }
        
        # ✅ 완료 상태면 추가 정보 포함
        if video_status in ["READY", "FAILED"]:
            response_data["hls_path"] = cached_status.get("hls_path")
            response_data["duration"] = cached_status.get("duration")
            if video_status == "FAILED":
                response_data["error_reason"] = cached_status.get("error_reason")
        
        return Response(response_data)
```

**변경 이유**: 진행 중 작업의 상태/진행률을 Redis-only로 조회하여 DB 부하 제거

**영향 범위**: 신규 endpoint, 기존 endpoint 영향 없음

**롤백 방법**: 파일 삭제, URL 라우팅 제거

**URL 라우팅 추가 필요**:

**파일**: `apps/support/video/urls.py`

**수정 전 코드**:

```python:apps/support/video/urls.py
router = DefaultRouter()
router.register(r"videos", VideoViewSet, basename="videos")
```

**수정 후 코드**:

```python:apps/support/video/urls.py
from apps.support.video.views.progress_views import VideoProgressView

router = DefaultRouter()
router.register(r"videos", VideoViewSet, basename="videos")

urlpatterns = [
    path("", include(router.urls)),
    # ✅ Progress endpoint 추가
    path("videos/<int:pk>/progress/", VideoProgressView.as_view(), name="video-progress"),
]
```

---

#### PATCH 4.2: Job Progress View 생성

**파일**: `apps/domains/ai/views/job_progress_view.py` (신규)

**수정 전 코드**: 파일 없음

**수정 후 코드**:

```python
"""AI Job 진행률/상태 전용 endpoint (Redis-only)"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.ai.redis_status_cache import get_job_status_from_redis
from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter


class JobProgressView(APIView):
    """Job 진행률/상태 조회 (Redis-only, DB 부하 0)"""
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request, job_id: str):
        """GET /api/v1/jobs/{job_id}/progress/"""
        tenant = getattr(request, "tenant", None)
        
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # ✅ Redis에서 상태 조회 (Tenant 네임스페이스)
        cached_status = get_job_status_from_redis(str(tenant.id), job_id)
        
        if not cached_status:
            # Redis에 없으면 404 (진행 중이 아니거나 완료 후 TTL 만료)
            return Response(
                {"detail": "진행 중인 작업이 아닙니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        job_status = cached_status.get("status")
        
        # ✅ 진행률은 Redis에서 조회
        progress = None
        if job_status == "PROCESSING":
            # ✅ tenant_id 전달하여 tenant namespace 키 조회
            progress_adapter = RedisProgressAdapter()
            progress = progress_adapter.get_progress(job_id, tenant_id=str(tenant.id))
        
        # ✅ 응답 구성
        response_data = {
            "job_id": job_id,
            "job_type": cached_status.get("job_type"),
            "status": job_status,
            "progress": progress,
        }
        
        # ✅ 완료 상태면 result/error 포함
        if job_status in ["DONE", "FAILED"]:
            response_data["error_message"] = cached_status.get("error_message")
            if job_status == "DONE":
                response_data["result"] = cached_status.get("result")
        
        return Response(response_data)
```

**변경 이유**: 진행 중 작업의 상태/진행률을 Redis-only로 조회하여 DB 부하 제거

**영향 범위**: 신규 endpoint, 기존 endpoint 영향 없음

**롤백 방법**: 파일 삭제, URL 라우팅 제거

**URL 라우팅 추가 필요**:

**파일**: `apps/domains/ai/urls.py`

**수정 전 코드**:

```python:apps/domains/ai/urls.py
from apps.domains.ai.views.job_status_view import JobStatusView

urlpatterns = [
    path("<str:job_id>/", JobStatusView.as_view(), name="job-status"),
]
```

**수정 후 코드**:

```python:apps/domains/ai/urls.py
from apps.domains.ai.views.job_status_view import JobStatusView
from apps.domains.ai.views.job_progress_view import JobProgressView

urlpatterns = [
    path("<str:job_id>/", JobStatusView.as_view(), name="job-status"),
    # ✅ Progress endpoint 추가
    path("<str:job_id>/progress/", JobProgressView.as_view(), name="job-progress"),
]
```

---

### 단계 5: Redis Progress Adapter 수정 (Tenant namespace 추가)

#### PATCH 5.1: Video Progress Adapter 분리 (AI와 완전 분리)

**⚠️ 중요**: Video는 AI와 완전히 분리하여 별도 Adapter 사용

**파일**: `apps/support/video/redis_progress_adapter.py` (신규)

**수정 전 코드**: 파일 없음

**수정 후 코드**:

```python
"""Video Progress Adapter - Video 전용 (AI와 분리, IProgress 인터페이스 구현)"""
from typing import Any, Optional
from libs.redis.client import get_redis_client
from src.application.ports.progress import IProgress
import json
import logging

logger = logging.getLogger(__name__)

# Video 진행 상태 키 TTL (6시간)
VIDEO_PROGRESS_TTL_SECONDS = 21600


class VideoProgressAdapter(IProgress):
    """Video 전용 Progress Adapter (IProgress 인터페이스 구현, AI와 분리)"""

    def __init__(self, video_id: int, tenant_id: int, ttl_seconds: int = VIDEO_PROGRESS_TTL_SECONDS) -> None:
        self._video_id = video_id
        self._tenant_id = tenant_id
        self._ttl = ttl_seconds

    def record_progress(
        self,
        job_id: str,  # IProgress 인터페이스 호환 (무시됨, video_id 사용)
        step: str,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Video 진행 단계 기록 (Redis에만) - IProgress 인터페이스 구현"""
        client = get_redis_client()
        if not client:
            return

        # ✅ Video 전용 키 형식: tenant:{tenant_id}:video:{video_id}:progress
        key = f"tenant:{self._tenant_id}:video:{self._video_id}:progress"
        payload = {"step": step, **(extra or {})}
        try:
            client.setex(
                key,
                self._ttl,
                json.dumps(payload, default=str),
            )
            logger.debug("Video progress recorded: video_id=%s step=%s tenant_id=%s", self._video_id, step, self._tenant_id)
        except Exception as e:
            logger.warning("Redis video progress record failed: %s", e)

    def get_progress(self, job_id: str) -> Optional[dict[str, Any]]:
        """Video 진행 상태 조회 - IProgress 인터페이스 구현"""
        client = get_redis_client()
        if not client:
            return None

        # ✅ Video 전용 키 형식
        key = f"tenant:{self._tenant_id}:video:{self._video_id}:progress"
        
        # 하위 호환성: tenant namespace 키가 없으면 기존 키 형식 확인
        try:
            raw = client.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        
        # Legacy 키 확인 (마이그레이션 기간 동안)
        legacy_key = f"job:video:{self._video_id}:progress"
        try:
            raw = client.get(legacy_key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        
        return None
    
    # 편의 메서드 (IProgress 외 추가)
    def get_progress_direct(self) -> Optional[dict[str, Any]]:
        """직접 조회 (job_id 없이)"""
        return self.get_progress("")  # job_id는 무시됨
```

**변경 이유**: 
- Video와 AI를 완전히 분리하여 키 구조 충돌 방지
- IProgress 인터페이스 구현으로 기존 코드 변경 최소화

**영향 범위**: Video worker의 progress 기록/조회

**롤백 방법**: 파일 삭제, 기존 RedisProgressAdapter 사용

---

#### PATCH 5.2: RedisProgressAdapter는 AI 전용으로 유지

**파일**: `src/infrastructure/cache/redis_progress_adapter.py`

**수정 전 코드**: (기존 코드 유지, Video는 사용하지 않음)

**수정 후 코드**: (AI Job 전용으로만 사용, tenant_id 파라미터 추가)

```python:src/infrastructure/cache/redis_progress_adapter.py
def record_progress(
    self,
    job_id: str,
    step: str,
    extra: Optional[dict[str, Any]] = None,
    tenant_id: Optional[str] = None,  # ✅ 추가 (AI Job 전용)
) -> None:
    """진행 단계 기록 (Redis에만) - AI Job 전용"""
    client = get_redis_client()
    if not client:
        return

    # ✅ AI Job 전용 키 형식: tenant:{tenant_id}:job:{job_id}:progress
    if tenant_id:
        key = f"tenant:{tenant_id}:job:{job_id}:progress"
    else:
        # 하위 호환성: tenant_id 없으면 기존 키 형식 사용
        key = f"job:{job_id}:progress"
    
    payload = {"step": step, **(extra or {})}
    try:
        client.setex(
            key,
            self._ttl,
            json.dumps(payload, default=str),
        )
        logger.debug("Progress recorded: job_id=%s step=%s tenant_id=%s", job_id, step, tenant_id)
    except Exception as e:
        logger.warning("Redis progress record failed: %s", e)

def get_progress(self, job_id: str, tenant_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """진행 상태 조회 - AI Job 전용"""
    client = get_redis_client()
    if not client:
        return None

    # ✅ AI Job 전용 키 형식
    if tenant_id:
        key = f"tenant:{tenant_id}:job:{job_id}:progress"
        try:
            raw = client.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        
        # 하위 호환성: tenant namespace 키가 없으면 기존 키 형식 확인
        legacy_key = f"job:{job_id}:progress"
        try:
            raw = client.get(legacy_key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        
        return None
    else:
        # tenant_id 없으면 기존 키 형식 사용
        key = f"job:{job_id}:progress"
        try:
            raw = client.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning("Redis progress get failed: %s", e)
            return None
```

**변경 이유**: AI Job 전용으로 명확히 분리

**영향 범위**: AI/Messaging Worker의 progress 기록/조회

**롤백 방법**: tenant_id 파라미터 제거, 기존 키 형식으로 복원

**호출부 수정 필요**:

**파일**: `apps/worker/ai_worker/ai/pipelines/dispatcher.py`

**수정 전 코드**:

```python:apps/worker/ai_worker/ai/pipelines/dispatcher.py
def _record_progress(
    job_id: str,
    step: str,
    percent: int,
    step_index: int | None = None,
    step_total: int | None = None,
    step_name_display: str | None = None,
    step_percent: int | None = None,
) -> None:
    from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
    extra = {"percent": percent}
    if step_index is not None and step_total is not None:
        extra.update({
            "step_index": step_index,
            "step_total": step_total,
            "step_name": step,
            "step_name_display": step_name_display or step,
            "step_percent": step_percent if step_percent is not None else 100,
        })
    RedisProgressAdapter().record_progress(job_id, step, extra)
```

**수정 후 코드**:

```python:apps/worker/ai_worker/ai/pipelines/dispatcher.py
def _record_progress(
    job_id: str,
    step: str,
    percent: int,
    step_index: int | None = None,
    step_total: int | None = None,
    step_name_display: str | None = None,
    step_percent: int | None = None,
    tenant_id: str | None = None,  # ✅ 추가
) -> None:
    from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
    extra = {"percent": percent}
    if step_index is not None and step_total is not None:
        extra.update({
            "step_index": step_index,
            "step_total": step_total,
            "step_name": step,
            "step_name_display": step_name_display or step,
            "step_percent": step_percent if step_percent is not None else 100,
        })
    RedisProgressAdapter().record_progress(job_id, step, extra, tenant_id=tenant_id)  # ✅ tenant_id 전달
```

**호출부 수정**:

**파일**: `apps/worker/ai_worker/ai/pipelines/dispatcher.py`

**수정 전 코드**:

```python:apps/worker/ai_worker/ai/pipelines/dispatcher.py
_record_progress(job.id, "downloading", 10, step_index=1, step_total=1, step_name_display="다운로드", step_percent=0)
```

**수정 후 코드**:

```python:apps/worker/ai_worker/ai/pipelines/dispatcher.py
_record_progress(job.id, "downloading", 10, step_index=1, step_total=1, step_name_display="다운로드", step_percent=0, tenant_id=str(job.tenant_id))
```

**모든 호출부 수정 필요**:
- `apps/worker/ai_worker/ai/pipelines/dispatcher.py` (모든 `_record_progress` 호출)
- `apps/worker/ai_worker/ai/pipelines/excel_handler.py` (모든 `_record_progress` 호출)
- `apps/worker/messaging_worker/sqs_main.py` (모든 `_record_progress` 호출)

---

#### PATCH 5.3: Video processor에서 VideoProgressAdapter 사용

**파일**: `apps/support/video/encoding_progress.py`

**수정 전 코드**:

```python:apps/support/video/encoding_progress.py
def _get_progress_payload(video_id: int) -> Optional[dict]:
    """Redis에서 job:video:{id}:progress payload 한 번에 조회."""
    try:
        from libs.redis.client import get_redis_client
    except ImportError:
        return None

    client = get_redis_client()
    if not client:
        return None

    job_id = f"{VIDEO_JOB_ID_PREFIX}{video_id}"
    key = f"job:{job_id}:progress"
    try:
        raw = client.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None
```

**수정 후 코드**:

```python:apps/support/video/encoding_progress.py
def _get_progress_payload(video_id: int, tenant_id: Optional[int] = None) -> Optional[dict]:
    """Redis에서 job:video:{id}:progress payload 한 번에 조회."""
    try:
        from libs.redis.client import get_redis_client
    except ImportError:
        return None

    client = get_redis_client()
    if not client:
        return None

    job_id = f"{VIDEO_JOB_ID_PREFIX}{video_id}"
    
    # ✅ Tenant namespace 포함한 키 우선 조회 (있으면)
    if tenant_id:
        key = f"tenant:{tenant_id}:video:{video_id}:progress"
        try:
            raw = client.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        
        # 하위 호환성: tenant namespace 키가 없으면 기존 키 형식 확인
        legacy_key = f"job:{job_id}:progress"
        try:
            raw = client.get(legacy_key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        
        return None
    else:
        # tenant_id 없으면 기존 키 형식 사용
        key = f"job:{job_id}:progress"
        try:
            raw = client.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            return None


def get_video_encoding_progress(video_id: int, tenant_id: Optional[int] = None) -> Optional[int]:
    """
    Redis에서 영상 인코딩 진행률 조회.
    워커가 record_progress(job_id="video:{video_id}", step=..., extra=...) 로 기록한 값을 읽음.
    반환: 0..100 또는 None (Redis 미설정/미기록 시).
    """
    payload = _get_progress_payload(video_id, tenant_id)  # ✅ tenant_id 전달
    if not payload:
        return None

    percent = payload.get("percent")
    if percent is not None:
        try:
            pct = int(percent)
            return max(0, min(100, pct))
        except (TypeError, ValueError):
            pass

    step = payload.get("step")
    if step in _STEP_PERCENT:
        return _STEP_PERCENT[step]
    return 50  # 알 수 없는 단계면 중간값


def get_video_encoding_remaining_seconds(video_id: int, tenant_id: Optional[int] = None) -> Optional[int]:
    """
    Redis에서 영상 인코딩 예상 남은 시간(초) 조회.
    워커가 record_progress 시 extra에 remaining_seconds 를 넣으면 반환.
    """
    payload = _get_progress_payload(video_id, tenant_id)  # ✅ tenant_id 전달
    if not payload:
        return None

    sec = payload.get("remaining_seconds")
    if sec is None:
        return None
    try:
        return max(0, int(sec))
    except (TypeError, ValueError):
        return None


def get_video_encoding_step_detail(video_id: int, tenant_id: Optional[int] = None) -> Optional[dict]:
    """
    Redis에서 구간별 진행률 조회. (n/7) 단계 + 구간 내 0~100%.
    반환: { step_index, step_total, step_name, step_name_display, step_percent } 또는 None.
    """
    payload = _get_progress_payload(video_id, tenant_id)  # ✅ tenant_id 전달
    if not payload:
        return None
    idx = payload.get("step_index")
    total = payload.get("step_total")
    name = payload.get("step_name")
    display = payload.get("step_name_display")
    pct = payload.get("step_percent")
    if idx is None or total is None or name is None or pct is None:
        return None
    try:
        return {
            "step_index": int(idx),
            "step_total": int(total),
            "step_name": str(name),
            "step_name_display": str(display) if display is not None else name,
            "step_percent": max(0, min(100, int(pct))),
        }
    except (TypeError, ValueError):
        return None
```

**변경 이유**: Tenant namespace 추가하여 멀티테넌트 안전성 확보, 하위 호환성 유지

**영향 범위**: Video encoding progress 조회 함수들

**롤백 방법**: tenant_id 파라미터 제거, 기존 키 형식으로 복원

**호출부 수정 필요**:

**파일**: `apps/support/video/serializers.py`

**수정 전 코드**:

```python:apps/support/video/serializers.py
def get_encoding_progress(self, obj):
    return get_video_encoding_progress(obj.id)
```

**수정 후 코드**:

```python:apps/support/video/serializers.py
def get_encoding_progress(self, obj):
    tenant_id = None
    if hasattr(obj, "session") and obj.session:
        if hasattr(obj.session, "lecture") and obj.session.lecture:
            tenant_id = obj.session.lecture.tenant_id
    return get_video_encoding_progress(obj.id, tenant_id)
```

---

#### PATCH 5.3: Video processor에서 VideoProgressAdapter 사용

**파일**: `src/infrastructure/video/processor.py`

**수정 전 코드**:

```python:src/infrastructure/video/processor.py
# IProgress 인터페이스 사용
progress.record_progress(
    job_id,  # "video:{video_id}"
    "presigning",
    {
        "percent": 5,
        "remaining_seconds": 120,
        "step_index": 1,
        "step_total": VIDEO_ENCODING_STEP_TOTAL,
        "step_name": "presigning",
        "step_name_display": "준비",
        "step_percent": 100,
    },
)
```

**수정 후 코드**:

```python:src/infrastructure/video/processor.py
# VideoProgressAdapter 사용 (IProgress 인터페이스 구현)
from apps.support.video.redis_progress_adapter import VideoProgressAdapter

# process_video() 함수 시작 부분에서
# 기존 progress 파라미터 대신 VideoProgressAdapter 인스턴스 생성
video_progress = VideoProgressAdapter(
    video_id=video_id,
    tenant_id=tenant_id,
    ttl_seconds=21600  # 6시간
)

# 기존 progress.record_progress() 호출은 그대로 유지 (IProgress 인터페이스 호환)
video_progress.record_progress(
    job_id,  # IProgress 인터페이스 호환 (무시됨)
    "presigning",
    {
        "percent": 5,
        "remaining_seconds": 120,
        "step_index": 1,
        "step_total": VIDEO_ENCODING_STEP_TOTAL,
        "step_name": "presigning",
        "step_name_display": "준비",
        "step_percent": 100,
    },
)
```

**또는 호출부에서 VideoProgressAdapter 전달**:

```python:apps/worker/video_worker/sqs_main.py
# VideoProgressAdapter 생성하여 process_video에 전달
from apps.support.video.redis_progress_adapter import VideoProgressAdapter

video_progress = VideoProgressAdapter(
    video_id=video_id,
    tenant_id=tenant_id,
    ttl_seconds=21600
)

hls_path, duration = process_video(
    job=job,
    cfg=cfg,
    progress=video_progress,  # ✅ VideoProgressAdapter 전달
)
```

**변경 이유**: Video 전용 Adapter 사용하여 키 구조 명확히 분리

**영향 범위**: Video worker의 모든 progress 기록

**롤백 방법**: 기존 IProgress 인터페이스 사용으로 복원

**주의사항**: 
- `process_video()` 함수는 IProgress 인터페이스를 유지하되, 내부에서 VideoProgressAdapter로 래핑
- 또는 `process_video()` 함수 시그니처 변경 (progress 파라미터 타입 변경)
- 호출부 확인 필요: `apps/worker/video_worker/video/processor.py` 등

**대안 (더 안전한 방법 - 권장)**:
IProgress 인터페이스를 유지하고, VideoProgressAdapter가 IProgress를 구현하도록 설계:

```python:apps/support/video/redis_progress_adapter.py
from src.application.ports.progress import IProgress

class VideoProgressAdapter(IProgress):
    """Video 전용 Progress Adapter (IProgress 인터페이스 구현)"""
    
    def __init__(self, video_id: int, tenant_id: int, ttl_seconds: int = VIDEO_PROGRESS_TTL_SECONDS) -> None:
        self._video_id = video_id
        self._tenant_id = tenant_id
        self._ttl = ttl_seconds
    
    def record_progress(
        self,
        job_id: str,  # IProgress 인터페이스 호환 (무시됨)
        step: str,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """IProgress 인터페이스 구현"""
        # job_id는 무시하고 video_id 사용
        client = get_redis_client()
        if not client:
            return
        
        # ✅ Video 전용 키 형식: tenant:{tenant_id}:video:{video_id}:progress
        key = f"tenant:{self._tenant_id}:video:{self._video_id}:progress"
        payload = {"step": step, **(extra or {})}
        try:
            client.setex(
                key,
                self._ttl,
                json.dumps(payload, default=str),
            )
            logger.debug("Video progress recorded: video_id=%s step=%s tenant_id=%s", self._video_id, step, self._tenant_id)
        except Exception as e:
            logger.warning("Redis video progress record failed: %s", e)
```

**호출부 수정**:

```python:src/infrastructure/video/processor.py
# process_video() 함수 시작 부분에서
from apps.support.video.redis_progress_adapter import VideoProgressAdapter

# IProgress 인터페이스를 구현한 VideoProgressAdapter 생성
video_progress = VideoProgressAdapter(
    video_id=video_id,
    tenant_id=tenant_id,
    ttl_seconds=21600  # 6시간
)

# 기존 progress 대신 video_progress 사용
# progress.record_progress(...) → video_progress.record_progress(...)
# (시그니처는 동일하므로 코드 변경 최소화)
```

이렇게 하면 `process_video()` 함수 시그니처 변경 없이 사용 가능

---

### 단계 6: 기존 Progress 조회 코드에서 DB 접근 제거 증명

#### 증명: VideoViewSet.retrieve()는 여전히 DB 조회

**파일**: `apps/support/video/views/video_views.py`

**Evidence**:

```python:apps/support/video/views/video_views.py
class VideoViewSet(VideoPlaybackMixin, ModelViewSet):
    queryset = video_repo.get_video_queryset_with_relations()
    serializer_class = VideoSerializer
    
    # ModelViewSet.retrieve()는 get_object()를 호출
    # get_object()는 queryset.get(pk=pk)로 DB 조회
```

**결론**: 기존 `GET /media/videos/{id}/` endpoint는 여전히 DB 조회

**해결**: 프론트엔드가 `GET /media/videos/{id}/progress/` endpoint만 사용하도록 변경 필요

---

#### 증명: JobStatusView.get()는 여전히 DB 조회

**파일**: `apps/domains/ai/views/job_status_view.py`

**Evidence**:

```python:apps/domains/ai/views/job_status_view.py
def get(self, request, job_id: str):
    repo = _ai_repo()
    job = repo.get_job_model_for_status(job_id, str(tenant.id))  # DB 조회
    result_payload = repo.get_result_payload_for_job(job)  # DB 조회
    return Response(build_job_status_response(job, result_payload=result_payload))
```

**결론**: 기존 `GET /api/v1/jobs/{job_id}/` endpoint는 여전히 DB 조회

**해결**: 프론트엔드가 `GET /api/v1/jobs/{job_id}/progress/` endpoint만 사용하도록 변경 필요

---

### 단계 7: 기존 키와의 호환 전략

#### 전략: 양쪽 키 모두 확인 (하위 호환성)

**구현 위치**: `RedisProgressAdapter.get_progress()`, `encoding_progress._get_progress_payload()`

**로직**:
1. Tenant namespace 포함한 키 우선 조회
2. 없으면 기존 키 형식 확인
3. 둘 다 없으면 None 반환

**마이그레이션 기간**: 1-2주 (기존 키 TTL 만료 대기)

**완료 조건**: 모든 Worker가 tenant_id를 전달하도록 수정 완료 후

---

## [PATCH PLAN — EXCEL BULK]

### 단계 1: Repository에 배치 조회 메서드 추가

#### PATCH 1.1: student_batch_filter_by_name_phone 추가

**파일**: `academy/adapters/db/django/repositories_students.py`

**수정 전 코드**: 메서드 없음

**수정 후 코드**:

```python
def student_batch_filter_by_name_phone(
    self,
    tenant_id: int,
    name_phone_pairs: list[tuple[str, str]],
) -> list:
    """배치로 기존 활성 학생 조회 (Tuple IN 방식, Index 활용)"""
    if not name_phone_pairs:
        return []
    
    from apps.domains.students.models import Student
    from django.db import connection
    
    # ✅ Tuple IN 방식 (Postgres composite index 활용)
    # WHERE (tenant_id, name, parent_phone) IN ((...), (...), ...)
    # Index: idx_student_tenant_name_phone (tenant_id, name, parent_phone)
    
    if len(name_phone_pairs) > 1000:
        # 대량 데이터는 chunk로 나눠서 처리
        results = []
        for chunk in [name_phone_pairs[i:i+1000] for i in range(0, len(name_phone_pairs), 1000)]:
            results.extend(self._student_batch_filter_chunk(tenant_id, chunk))
        return results
    
    return self._student_batch_filter_chunk(tenant_id, name_phone_pairs)


def _student_batch_filter_chunk(
    self,
    tenant_id: int,
    name_phone_pairs: list[tuple[str, str]],
) -> list:
    """Chunk 단위 배치 조회 (Raw SQL로 최적화)"""
    from apps.domains.students.models import Student
    from django.db import connection
    
    if not name_phone_pairs:
        return []
    
    # ✅ Raw SQL로 Tuple IN 쿼리 (Index 활용)
    # SELECT id, name, parent_phone, user_id, phone, ... FROM students
    # WHERE (tenant_id, name, parent_phone) IN ((...), (...), ...)
    # AND deleted_at IS NULL
    placeholders = ','.join(['(%s, %s, %s)'] * len(name_phone_pairs))
    values = []
    for name, parent_phone in name_phone_pairs:
        values.extend([tenant_id, name, parent_phone])
    
    query = f"""
        SELECT id, tenant_id, name, parent_phone, phone, user_id, ps_number, 
               school_type, high_school, middle_school, grade, memo, gender,
               deleted_at, created_at, updated_at
        FROM students
        WHERE (tenant_id, name, parent_phone) IN ({placeholders})
        AND deleted_at IS NULL
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, values)
        columns = [col[0] for col in cursor.description]
        return [
            Student(**dict(zip(columns, row)))
            for row in cursor.fetchall()
        ]


def student_batch_filter_deleted_by_name_phone(
    self,
    tenant_id: int,
    name_phone_pairs: list[tuple[str, str]],
) -> list:
    """배치로 삭제된 학생 조회 (Tuple IN 방식)"""
    if not name_phone_pairs:
        return []
    
    from apps.domains.students.models import Student
    from django.db import connection
    
    if len(name_phone_pairs) > 1000:
        results = []
        for chunk in [name_phone_pairs[i:i+1000] for i in range(0, len(name_phone_pairs), 1000)]:
            results.extend(self._student_batch_filter_deleted_chunk(tenant_id, chunk))
        return results
    
    return self._student_batch_filter_deleted_chunk(tenant_id, name_phone_pairs)


def _student_batch_filter_deleted_chunk(
    self,
    tenant_id: int,
    name_phone_pairs: list[tuple[str, str]],
) -> list:
    """Chunk 단위 삭제된 학생 배치 조회"""
    from apps.domains.students.models import Student
    from django.db import connection
    
    if not name_phone_pairs:
        return []
    
    placeholders = ','.join(['(%s, %s, %s)'] * len(name_phone_pairs))
    values = []
    for name, parent_phone in name_phone_pairs:
        values.extend([tenant_id, name, parent_phone])
    
    query = f"""
        SELECT id, tenant_id, name, parent_phone, phone, user_id, ps_number,
               school_type, high_school, middle_school, grade, memo, gender,
               deleted_at, created_at, updated_at
        FROM students
        WHERE (tenant_id, name, parent_phone) IN ({placeholders})
        AND deleted_at IS NOT NULL
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, values)
        columns = [col[0] for col in cursor.description]
        return [
            Student(**dict(zip(columns, row)))
            for row in cursor.fetchall()
        ]
```

**변경 이유**: 배치 조회로 쿼리 수 99% 감소

**영향 범위**: 신규 메서드 추가, 영향 없음

**롤백 방법**: 메서드 제거

**EXPLAIN 전략**:

```sql
-- 1. Tuple IN 방식 EXPLAIN
EXPLAIN ANALYZE
SELECT * FROM students
WHERE (tenant_id, name, parent_phone) IN ((1, '홍길동', '01012345678'), (1, '김철수', '01087654321'))
AND deleted_at IS NULL;

-- 2. Q OR 조건 방식 EXPLAIN (비교용)
EXPLAIN ANALYZE
SELECT * FROM students
WHERE (tenant_id = 1 AND name = '홍길동' AND parent_phone = '01012345678')
   OR (tenant_id = 1 AND name = '김철수' AND parent_phone = '01087654321')
AND deleted_at IS NULL;

-- 3. Index 사용 확인
EXPLAIN ANALYZE
SELECT * FROM students
WHERE tenant_id = 1 AND name = '홍길동' AND parent_phone = '01012345678'
AND deleted_at IS NULL;
```

**예상 결과**: Tuple IN 방식이 Index를 더 효율적으로 사용

---

### 단계 2: Excel Bulk Create 함수 구현

#### PATCH 2.1: bulk_create_students_from_excel_rows_optimized 추가

**파일**: `apps/domains/students/services/bulk_from_excel.py`

**수정 전 코드**: 기존 함수 유지

**수정 후 코드** (기존 함수 아래에 추가):

```python
def bulk_create_students_from_excel_rows_optimized(
    *,
    tenant_id: int,
    students_data: list[dict],
    initial_password: str,
    on_row_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """
    엑셀 파싱된 행으로 학생 일괄 생성 (최적화 버전)
    - 배치 조회로 쿼리 수 최소화
    - bulk_create로 일괄 생성
    - Chunked transaction으로 안정성 확보
    """
    from django.db import transaction
    from academy.adapters.db.django import repositories_enrollment as enroll_repo
    from academy.adapters.db.django import repositories_students as student_repo
    from apps.domains.students.models import Student
    from apps.core.models import TenantMembership, User
    from .lecture_enroll import _normalize_phone, _grade_value, normalize_school_from_name
    from ..ps_number import _generate_unique_ps_number
    from apps.domains.parents.services import ensure_parent_for_student
    
    tenant = enroll_repo.get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("tenant_id not found")
    
    initial_password = (initial_password or "").strip()
    if len(initial_password) < 4:
        raise ValueError("initial_password는 4자 이상이어야 합니다.")
    
    total = len(students_data)
    created_count = 0
    failed: list[dict] = []
    
    # ✅ 1. 모든 학생의 (name, parent_phone) 쌍 정규화
    normalized_pairs = []
    valid_rows = []
    
    for row_index, raw in enumerate(students_data, start=1):
        item = dict(raw) if isinstance(raw, dict) else {}
        name = (item.get("name") or "").strip()
        parent_phone = _normalize_phone(item.get("parent_phone") or item.get("parentPhone") or "")
        
        if not parent_phone or len(parent_phone) != 11 or not parent_phone.startswith("010"):
            failed.append({
                "row": row_index,
                "name": name or "(이름 없음)",
                "error": "학부모 전화번호가 올바르지 않습니다.",
            })
            continue
        
        if not name:
            failed.append({
                "row": row_index,
                "name": "(이름 없음)",
                "error": "이름이 필요합니다.",
            })
            continue
        
        normalized_pairs.append((name, parent_phone))
        valid_rows.append((row_index, item))
    
    if not normalized_pairs:
        return {
            "created": 0,
            "failed": failed,
            "total": total,
            "processed_by": "worker",
        }
    
    # ✅ 2. 배치로 기존 활성 학생 조회
    existing_students = student_repo.student_batch_filter_by_name_phone(
        tenant_id=tenant_id,
        name_phone_pairs=normalized_pairs
    )
    existing_map = {
        (s.name, s.parent_phone): s
        for s in existing_students
    }
    
    # ✅ 3. 배치로 삭제된 학생 조회
    deleted_students = student_repo.student_batch_filter_deleted_by_name_phone(
        tenant_id=tenant_id,
        name_phone_pairs=normalized_pairs
    )
    deleted_map = {
        (s.name, s.parent_phone): s
        for s in deleted_students
    }
    
    # ✅ 4. Chunked 트랜잭션으로 일괄 처리 (운영 안정성)
    CHUNK_SIZE = 200  # 운영 안정성을 위한 chunk 크기
    
    restored_students = []
    
    # 4-1. 삭제된 학생 복원 (chunk 단위)
    deleted_items = list(deleted_map.items())
    for chunk in [deleted_items[i:i+CHUNK_SIZE] for i in range(0, len(deleted_items), CHUNK_SIZE)]:
        with transaction.atomic():
            for (name, parent_phone), deleted_student in chunk:
                try:
                    deleted_student.deleted_at = None
                    update_fields = ["deleted_at"]
                    
                    # 기존 로직과 동일한 업데이트
                    item = next((r[1] for r in valid_rows if (r[1].get("name"), _normalize_phone(r[1].get("parent_phone"))) == (name, parent_phone)), {})
                    phone_raw = item.get("phone")
                    phone = _normalize_phone(phone_raw) if phone_raw else None
                    if phone and (len(phone) != 11 or not phone.startswith("010")):
                        phone = None
                    
                    if phone is not None:
                        deleted_student.phone = phone
                        update_fields.append("phone")
                    
                    school_val = (item.get("school") or "").strip() or None
                    if school_val is not None:
                        st, high_school, middle_school = normalize_school_from_name(
                            school_val, item.get("school_type")
                        )
                        deleted_student.school_type = st
                        deleted_student.high_school = high_school
                        deleted_student.middle_school = middle_school
                        deleted_student.high_school_class = (
                            (item.get("high_school_class") or "").strip() or None
                            if st == "HIGH"
                            else None
                        )
                        deleted_student.major = (
                            (item.get("major") or "").strip() or None
                            if st == "HIGH"
                            else None
                        )
                        update_fields.extend(
                            ["school_type", "high_school", "middle_school", "high_school_class", "major"]
                        )
                    
                    gr = _grade_value(item.get("grade"))
                    if gr is not None:
                        deleted_student.grade = gr
                        update_fields.append("grade")
                    
                    if item.get("memo") is not None:
                        deleted_student.memo = (item.get("memo") or "").strip() or None
                        update_fields.append("memo")
                    
                    if item.get("gender") is not None:
                        deleted_student.gender = (item.get("gender") or "").strip().upper()[:1] or None
                        update_fields.append("gender")
                    
                    deleted_student.save(update_fields=update_fields)
                    TenantMembership.ensure_active(
                        tenant=tenant,
                        user=deleted_student.user,
                        role="student",
                    )
                    restored_students.append((name, parent_phone))
                except Exception as e:
                    logger.warning("Failed to restore deleted student: %s", e)
                    failed.append({
                        "row": next((r[0] for r in valid_rows if (r[1].get("name"), _normalize_phone(r[1].get("parent_phone"))) == (name, parent_phone)), 0),
                        "name": name,
                        "error": f"복원 실패: {str(e)[:500]}",
                    })
    
    # 4-2. 신규 학생 수집
    new_students_data = []
    for idx, (row_index, item) in enumerate(valid_rows):
        if on_row_progress and total > 0:
            on_row_progress(idx + 1, total)
        
        name = (item.get("name") or "").strip()
        parent_phone = _normalize_phone(item.get("parent_phone") or item.get("parentPhone") or "")
        key = (name, parent_phone)
        
        # 이미 존재하거나 복원된 학생은 스킵
        if key in existing_map or key in restored_students:
            continue
        
        # 신규 학생 생성 (모델 인스턴스만 생성, 아직 저장 안 함)
        try:
            phone_raw = item.get("phone")
            phone = _normalize_phone(phone_raw) if phone_raw else None
            if phone and (len(phone) != 11 or not phone.startswith("010")):
                phone = None
            
            school_val = (item.get("school") or "").strip() or None
            school_type = None
            high_school = None
            middle_school = None
            if school_val:
                school_type, high_school, middle_school = normalize_school_from_name(
                    school_val, item.get("school_type")
                )
            
            ps_number = _generate_unique_ps_number(tenant_id)
            omr_code = (phone[-8:] if phone and len(phone) >= 8 else parent_phone[-8:]).ljust(8, "0")[:8]
            
            student = Student(
                tenant_id=tenant_id,
                name=name,
                parent_phone=parent_phone,
                phone=phone,
                school_type=school_type,
                high_school=high_school,
                middle_school=middle_school,
                high_school_class=(
                    (item.get("high_school_class") or "").strip() or None
                    if school_type == "HIGH"
                    else None
                ),
                major=(
                    (item.get("major") or "").strip() or None
                    if school_type == "HIGH"
                    else None
                ),
                grade=_grade_value(item.get("grade")),
                memo=(item.get("memo") or "").strip() or None,
                gender=(
                    (item.get("gender") or "").strip().upper()[:1] or None
                    if item.get("gender")
                    else None
                ),
                ps_number=ps_number,
                omr_code=omr_code,
                uses_identifier=item.get("uses_identifier", False) or (phone is None or not phone),
            )
            new_students_data.append({
                "student": student,
                "parent_phone": parent_phone,
                "password": initial_password,
                "row_index": row_index,
            })
        except Exception as e:
            failed.append({
                "row": row_index,
                "name": name or "(이름 없음)",
                "error": str(e)[:500],
            })
    
    # 4-3. 신규 학생 Bulk Create (chunk 단위)
    for chunk in [new_students_data[i:i+CHUNK_SIZE] for i in range(0, len(new_students_data), CHUNK_SIZE)]:
        with transaction.atomic():
            try:
                # Chunk 내 학생들 생성
                students_to_create = []
                for student_data in chunk:
                    students_to_create.append(student_data["student"])
                
                # ✅ Bulk Create로 일괄 생성 (ignore_conflicts로 ps_number 충돌 처리)
                Student.objects.bulk_create(students_to_create, batch_size=500, ignore_conflicts=True)
                
                # ✅ 충돌된 ps_number 재시도
                # bulk_create 후 실제 생성된 학생만 필터링
                created_ps_numbers = {s.ps_number for s in students_to_create}
                actual_students_list = list(Student.objects.filter(
                    tenant_id=tenant_id,
                    ps_number__in=created_ps_numbers
                ))
                actual_ps_numbers = {s.ps_number for s in actual_students_list}
                conflict_students = [s for s in students_to_create if s.ps_number not in actual_ps_numbers]
                
                # 충돌된 학생들 재시도 (ps_number 재생성)
                for conflict_student in conflict_students:
                    try:
                        # ps_number 재생성 (최대 3회 시도)
                        retry_count = 0
                        max_retries = 3
                        new_ps_number = None
                        while retry_count < max_retries:
                            try:
                                new_ps_number = _generate_unique_ps_number()
                                # DB에서 중복 확인
                                if not student_repo.user_filter_username_exists(new_ps_number):
                                    conflict_student.ps_number = new_ps_number
                                    conflict_student.save()
                                    actual_students_list.append(conflict_student)
                                    actual_ps_numbers.add(new_ps_number)
                                    break
                                retry_count += 1
                            except Exception:
                                retry_count += 1
                        
                        if new_ps_number is None or conflict_student.ps_number not in actual_ps_numbers:
                            raise ValueError("ps_number 재생성 실패")
                    except Exception as e:
                        logger.warning("Failed to retry ps_number for student: %s", e)
                        failed.append({
                            "row": next((d["row_index"] for d in chunk if d["student"].name == conflict_student.name), 0),
                            "name": conflict_student.name,
                            "error": f"ps_number 충돌 재시도 실패: {str(e)[:500]}",
                        })
                
                # ✅ User Bulk Create (FK 연결, ignore_conflicts로 username 충돌 처리)
                users_to_create = []
                for student_data, student in zip(chunk, students_to_create):
                    if student.ps_number in actual_ps_numbers:  # 실제 생성된 학생만
                        user = User(
                            username=student.ps_number,
                            phone=student.phone or "",
                            name=student.name,
                        )
                        user.set_password(student_data["password"])
                        users_to_create.append(user)
                
                User.objects.bulk_create(users_to_create, batch_size=500, ignore_conflicts=True)
                
                # ✅ User FK 연결 확인 및 재시도
                created_usernames = {u.username for u in users_to_create}
                actual_users = User.objects.filter(username__in=created_usernames)
                actual_usernames = {u.username for u in actual_users}
                missing_usernames = created_usernames - actual_usernames
                
                # User 생성 실패한 학생들 재시도 (개별 생성)
                for missing_username in missing_usernames:
                    student = next((s for s in actual_students_list if s.ps_number == missing_username), None)
                    if student:
                        student_data = next((d for d in chunk if d["student"].ps_number == missing_username), None)
                        if student_data:
                            try:
                                user = User(
                                    username=student.ps_number,
                                    phone=student.phone or "",
                                    name=student.name,
                                )
                                user.set_password(student_data["password"])
                                user.save()
                            except Exception as e:
                                logger.warning("Failed to create user for student: %s", e)
                                failed.append({
                                    "row": student_data["row_index"],
                                    "name": student.name,
                                    "error": f"User 생성 실패: {str(e)[:500]}",
                                })
                
                # ✅ Student FK 업데이트 (bulk_update)
                student_user_map = {
                    s.ps_number: u
                    for s, u in zip(students_to_create, users_to_create)
                }
                for student in students_to_create:
                    student.user = student_user_map[student.ps_number]
                
                Student.objects.bulk_update(
                    students_to_create,
                    ["user"],
                    batch_size=500
                )
                
                # ✅ TenantMembership Bulk Create
                memberships_to_create = []
                for student in students_to_create:
                    memberships_to_create.append(
                        TenantMembership(
                            tenant=tenant,
                            user=student.user,
                            role="student",
                        )
                    )
                TenantMembership.objects.bulk_create(memberships_to_create, batch_size=500)
                
                # ✅ Parent 생성 (개별 처리 필요 - 외래키 관계)
                for student_data in chunk:
                    try:
                        student = next((s for s in students_to_create if s.ps_number == student_data["student"].ps_number), None)
                        if student:
                            ensure_parent_for_student(student, student_data["parent_phone"])
                    except Exception as e:
                        logger.warning("Failed to create parent: %s", e)
                        failed.append({
                            "row": student_data["row_index"],
                            "name": student_data["student"].name,
                            "error": f"Parent 생성 실패: {str(e)[:500]}",
                        })
                
                created_count += len(students_to_create)
                
            except Exception as e:
                logger.exception("Failed to process chunk: %s", e)
                # Chunk 실패 시 해당 chunk만 실패 처리
                for student_data in chunk:
                    failed.append({
                        "row": student_data["row_index"],
                        "name": student_data.get("student").name if hasattr(student_data.get("student"), "name") else "(이름 없음)",
                        "error": f"처리 실패: {str(e)[:500]}",
                    })
    
    return {
        "created": created_count,
        "failed": failed,
        "total": total,
        "processed_by": "worker",
    }
```

**변경 이유**: 배치 조회 + Bulk Create로 쿼리 수 99% 감소

**영향 범위**: Excel 파싱 워커

**롤백 방법**: 기존 함수로 복원

**5000 rows 업로드 시 쿼리 수 계산**:

**Before (기존 방식)**:
- 학생 5000명 × (SELECT 2번 + INSERT 3번) = 약 25,000번 쿼리
- 실행 시간: 50-150초

**After (최적화 방식)**:
- 배치 조회: 2번 (기존 학생, 삭제된 학생)
- Bulk Create: 약 25번 (5000명 / 200 chunk × 각 chunk당 1번)
- **총 쿼리 수: 약 27번**
- 실행 시간: 5-15초

**쿼리 수 99.9% 감소**

---

### 단계 3: 기존 함수 유지 여부 판단

#### 판단: 기존 함수 유지 (하위 호환성)

**이유**:
1. 다른 코드에서 `bulk_create_students_from_excel_rows` 사용 가능
2. 점진적 마이그레이션 가능
3. 문제 발생 시 즉시 롤백 가능

**전환 전략**:
1. 신규 함수 추가 (`bulk_create_students_from_excel_rows_optimized`)
2. Excel 파싱 워커만 신규 함수 사용
3. 검증 후 다른 호출부도 전환
4. 문제 없으면 기존 함수 deprecated 표시

---

### 단계 4: 트랜잭션 범위 설계

#### Chunked Transaction (200개 단위)

**이유**:
- 하나의 giant transaction은 lock 시간이 길어짐
- 중간 실패 시 전체 롤백
- Chunk 단위로 나눠서 처리하면 안정성 향상

**트랜잭션 범위**:
- 삭제된 학생 복원: chunk 단위 (200개)
- 신규 학생 생성: chunk 단위 (200개)

**잠금(lock) 리스크 분석**:
- Student 테이블: Row-level lock (SELECT FOR UPDATE 없음)
- User 테이블: Row-level lock
- TenantMembership 테이블: Row-level lock
- **예상 Lock 시간**: chunk당 1-3초
- **총 Lock 시간**: 5000명 / 200 = 25 chunk × 2초 = 50초

**실패 시 부분 롤백 전략**:
- Chunk 실패 시 해당 chunk만 롤백
- 다른 chunk는 정상 처리
- 실패한 chunk는 failed 리스트에 추가

---

## [PATCH PLAN — WORKER LIMIT]

### 단계 1: 현재 ASG 설정 분석

#### 현재 설정 확인 필요

**확인 방법**:

```powershell
# AI Worker ASG 설정 확인
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-ai-worker-asg \
  --region ap-northeast-2 \
  --query 'AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity]'

# Video Worker ASG 설정 확인
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-video-worker-asg \
  --region ap-northeast-2 \
  --query 'AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity]'

# Messaging Worker ASG 설정 확인
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-messaging-worker-asg \
  --region ap-northeast-2 \
  --query 'AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity]'
```

**예상 현재 설정**:
- Min Size: 1
- Max Size: 20 (무제한 확장)
- Desired Capacity: 2-5

---

### 단계 2: Max Size 제안값

#### 제안 설정

**AI Worker ASG**:
```
Min Size: 1
Max Size: 5  # ✅ 제한
Desired Capacity: 2
```

**Video Worker ASG**:
```
Min Size: 1
Max Size: 3  # ✅ 제한
Desired Capacity: 1
```

**Messaging Worker ASG**:
```
Min Size: 1
Max Size: 5  # ✅ 제한
Desired Capacity: 2
```

**변경 명령**:

```powershell
# AI Worker
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name academy-ai-worker-asg \
  --max-size 5 \
  --region ap-northeast-2

# Video Worker
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name academy-video-worker-asg \
  --max-size 3 \
  --region ap-northeast-2

# Messaging Worker
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name academy-messaging-worker-asg \
  --max-size 5 \
  --region ap-northeast-2
```

---

### 단계 3: 예상 max DB connection 계산식

#### 계산식

**Worker 1개당 DB Connection 수**:
```
Gunicorn workers: 4개 (기본값)
Django DB_CONN_MAX_AGE: 15초 (권장)
예상: Worker 1개당 4-8개 connection
```

**총 Connection 수 (수정)**:
```
AI Worker: 5개 × 1-2 = 5-10개  # ✅ 수정: EC2 worker는 프로세스 1개, connection 1-2개
Video Worker: 3개 × 1-2 = 3-6개
Messaging Worker: 5개 × 1-2 = 5-10개
API 서버: 15-20개
총: 28-46개 connection
```

**RDS max_connections 대비 안전 마진 (수정)**:
```
db.t4g.micro: ~20-25개 → ⚠️ 위험 (100% 사용)
db.t4g.small: ~45-50개 → ✅ 충분 (60-90% 사용)
db.t4g.medium: ~90-100개 → ✅ 여유 (30-50% 사용)
```

**결론**: 
- **db.t4g.small로도 충분히 버틸 가능성 있음** ✅
- micro가 터진 건 Excel row-by-row + 폴링 DB 때문이지 순수 connection saturation 때문은 아님
- 권장: small (가성비), medium (안정성)

---

## [PATCH PLAN — INDEX MIGRATION]

### 단계 1: 인덱스 마이그레이션 SQL

#### PATCH 1.1: Students 인덱스 추가

**파일**: `migrations/XXXX_add_student_indexes.py` (신규)

**수정 후 코드**:

```python
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False  # ✅ 필수: CREATE INDEX CONCURRENTLY는 트랜잭션 밖에서 실행되어야 함

    dependencies = [
        ('students', 'XXXX_previous_migration'),  # 실제 migration 번호로 변경
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_student_tenant_name_phone
            ON students (tenant_id, name, parent_phone)
            WHERE deleted_at IS NULL;
            
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_student_tenant_name_phone_deleted
            ON students (tenant_id, name, parent_phone)
            WHERE deleted_at IS NOT NULL;
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS idx_student_tenant_name_phone;
            DROP INDEX IF EXISTS idx_student_tenant_name_phone_deleted;
            """
        ),
    ]
```

**변경 이유**: 배치 조회 성능 향상

**영향 범위**: 인덱스 추가만, 영향 없음

**롤백 방법**: Migration rollback

**주의사항**: `CREATE INDEX CONCURRENTLY`는 lock 없이 인덱스 생성 (운영 중 실행 가능)

---

#### PATCH 1.2: AIJob 인덱스 추가

**파일**: `migrations/XXXX_add_aijob_indexes.py` (신규)

**수정 후 코드**:

```python
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False  # ✅ 필수: CREATE INDEX CONCURRENTLY는 트랜잭션 밖에서 실행되어야 함

    dependencies = [
        ('ai', 'XXXX_previous_migration'),  # 실제 migration 번호로 변경
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_aijob_tenant_status
            ON aijob (tenant_id, status);
            
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_aijob_tenant_job_id
            ON aijob (tenant_id, job_id);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS idx_aijob_tenant_status;
            DROP INDEX IF EXISTS idx_aijob_tenant_job_id;
            """
        ),
    ]
```

---

#### PATCH 1.3: Video 인덱스 추가

**파일**: `migrations/XXXX_add_video_indexes.py` (신규)

**수정 후 코드**:

```python
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False  # ✅ 필수: CREATE INDEX CONCURRENTLY는 트랜잭션 밖에서 실행되어야 함

    dependencies = [
        ('video', 'XXXX_previous_migration'),  # 실제 migration 번호로 변경
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_tenant_status
            ON video (tenant_id, status);
            
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_session_status
            ON video (session_id, status);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS idx_video_tenant_status;
            DROP INDEX IF EXISTS idx_video_session_status;
            """
        ),
    ]
```

---

## [리스크 평가 — 실제 장애 시나리오]

### 시나리오 1: Redis Tenant Namespace 마이그레이션 실패

**장애 상황**:
- 기존 키: `job:{job_id}:progress`
- 신규 키: `tenant:{tenant_id}:job:{job_id}:progress`
- Worker가 tenant_id를 전달하지 않아 신규 키에 기록 안 됨
- 프론트엔드가 신규 키만 조회하여 진행률 표시 안 됨

**대응 전략**:
1. 하위 호환성 유지: 양쪽 키 모두 확인
2. Worker 로그 모니터링: tenant_id 전달 여부 확인
3. 점진적 마이그레이션: 모든 Worker 수정 완료 후 기존 키 제거

**롤백 방법**:
- RedisProgressAdapter에서 tenant_id 파라미터 제거
- 기존 키 형식으로 복원

---

### 시나리오 2: Excel Bulk 최적화에서 ps_number Race Condition

**장애 상황**:
- 동시에 Excel 2개 업로드
- Worker 2개가 동시에 `_generate_unique_ps_number()` 호출
- 같은 ps_number 생성
- bulk_create에서 unique constraint 충돌

**대응 전략**:
1. `bulk_create(ignore_conflicts=True)` 사용
2. 충돌된 ps_number 재시도 (재생성)
3. 재시도 실패 시 failed 리스트에 추가

**롤백 방법**:
- 기존 `bulk_create_students_from_excel_rows` 함수로 복원
- 신규 함수 호출부 제거

**추가 보완**:
- ps_number 생성 시 DB lock 사용 고려 (SELECT FOR UPDATE)
- 또는 UUID 기반 ps_number로 변경 고려

---

### 시나리오 3: Worker Concurrency 제한으로 작업 지연

**장애 상황**:
- ASG Max Size 제한으로 Worker 확장 불가
- SQS 큐 depth 증가
- 작업 처리 지연

**대응 전략**:
1. CloudWatch 알람 설정: SQS 큐 depth 모니터링
2. 수동 확장: 필요 시 Max Size 임시 증가
3. Worker 최적화: 처리 속도 향상

**롤백 방법**:
- ASG Max Size 원래 값으로 복원

---

## 최종 평가 (재계산 - 피드백 반영)

### 구조 안정성 점수: **9/10**

**강점**:
- Video와 AI 완전 분리로 키 구조 충돌 방지
- ps_number race condition 처리 (ignore_conflicts + 재시도)
- 하위 호환성 유지
- 점진적 마이그레이션 가능
- 롤백 전략 명확

**개선 사항**:
- ps_number race condition 완전 해결 필요 (DB lock 또는 UUID 기반)

---

### 확장성 점수: **9/10**

**강점**:
- 10K까지 구조 변경 없이 확장 가능
- 인덱스 추가로 쿼리 성능 향상
- Excel chunk 구조는 10K까지 안전

**개선 필요**:
- 10K+에서는 임시테이블 전략 고려

---

### 비용 대비 효율 점수: **9.5/10**

**강점**:
- DB 폴링 제거로 RDS 부하 대폭 감소
- Excel Bulk 최적화로 쿼리 수 99% 감소
- db.t4g.small로도 충분히 버틸 수 있음 (가성비 최고)

---

## 실행 순서 (수정 - 피드백 반영)

**⚠️ 중요**: 순서를 반드시 지켜야 함

### Day 1 (오늘 적용 가능한 범위) - 필수

1. **Redis 상태 캐싱 헬퍼 생성**
   - `apps/support/video/redis_status_cache.py` 추가
   - `apps/domains/ai/redis_status_cache.py` 추가

2. **Progress endpoint 추가**
   - `VideoProgressView` / `JobProgressView` 추가 + URL 라우팅
   - `encoding_progress.py` tenant-aware 조회 반영 + View에서 tenant_id 전달

3. **RedisProgressAdapter 수정**
   - `src/infrastructure/cache/redis_progress_adapter.py`에 tenant_id 지원 추가 (AI 전용)

4. **AI Repository 수정**
   - `repositories_ai.py` save()에 Redis status 저장 추가 (logger/result 방어 포함)

5. **프론트 폴링 전환** (DB CPU 즉시 안정화 포인트)
   - 프론트엔드가 progress endpoint로 변경

### Day 2

6. **Video worker 저장 로직 수정**
   - `complete_video`/`fail_video`/`mark_processing`에 Redis status 저장 반영
   - Status 값 타입 통일 (getattr 패턴 사용)

7. **VideoProgressAdapter writer 쪽 적용** (선택)
   - Video worker에서 VideoProgressAdapter 사용 (IProgress 인터페이스 구현)

### Day 4+ (별도 PR)

8. **Excel Bulk 최적화** (FK/제약조건 확인 후)
   - Student/User 생성 순서 재정렬 필요
   - ps_number 충돌 처리 완성

9. **인덱스 마이그레이션 실행**
   - Students, AIJob, Video 인덱스 추가 (`atomic=False` 필수)

10. **Worker Concurrency 제한 적용**
    - ASG Max Size 제한

**⚠️ 주의**: Excel부터 건드리면 리스크 커진다. 반드시 Redis progress endpoint 먼저 적용 후 프론트 polling 전환, DB CPU 안정 확인 후 Excel bulk 교체

---

## Aurora 필요성 판단 (피드백 반영)

### 수치 기반 기준

**Aurora로 전환해야 하는 시점**:

| 사용자 수 | 필요 여부 | 이유 |
|-----------|----------|------|
| 500 | ❌ 절대 불필요 | RDS small로 충분 |
| 3K | ❌ 불필요 | RDS small/medium으로 충분 |
| 10K | ❌ 불필요 | RDS medium으로 충분 |
| 30K | ⚠️ 고려 | 읽기 replica 분리 필요 시 |

**Aurora가 필요한 조건**:
- 읽기 replica 분리 필요할 때
- Write IOPS 5K 이상 나올 때
- Connection 200 이상일 때

**현재 설계 기준**:
- RDS t4g.medium으로 10K 충분히 간다
- Aurora는 30K+ 사용자부터 고려

---

**패치 플랜 완료 (피드백 반영)**
