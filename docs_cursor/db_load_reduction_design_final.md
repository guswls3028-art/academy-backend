# DB 부하 최소화 설계 (최종 합일점)

## ✅ 최종 설계 원칙 (핵심)

### 0. 기본 원칙
- **"DB는 영구 저장소"**, **"Redis는 상태 스트림/캐시"**
- 진행 중 상태/진행률/임시 결과: Redis
- 최종 결과: DB (필수)
- 조회: Redis 우선, Redis 미스 시 DB

### 1. ❌ DB 폴링은 "꼭" 할 필요 없다 → ✅ 완전히 제거 가능

**핵심: 진행 상태는 Redis에 이미 있음. DB를 왜 때리냐?**

#### 진행 중 작업
필요한 데이터:
- `status` → Redis에 있음
- `progress` → Redis에 있음
- `step` → Redis에 있음
- `error` → Redis에 있음

👉 **DB 필요 없음**

#### 완료된 작업
필요한 데이터:
- `status` → 완료 시 Redis에 저장
- `hls_path` → 완료 시 Redis에 저장
- `duration` → 완료 시 Redis에 저장
- `result` → 완료 시 Redis에 저장
- `error_message` → 완료 시 Redis에 저장

👉 **DB 필요 없음**

#### DB는 언제 필요함?
오직 이런 경우만:
- 사용자가 작업 페이지를 새로고침했는데 Redis에 캐시가 없을 때
- 과거 기록을 조회할 때

👉 **이때만 DB fallback**

### 2. 가장 이상적인 구조

```
진행 중:
  Frontend
    ↓
  GET /progress/ (Redis only)
    ↓
  DB 0번 ✅

완료 감지:
  Redis status == READY
    ↓
  Frontend stops polling
    ↓
  (선택) GET /detail/ 1회 호출
    ↓
  DB 1회 ✅

끝.
```

### 3. 진행 상황은 "보기 편하라고 주는 것"
- 진행 상황 때문에 DB 터지는 게 문제
- 시청 로그, 정채 판단 프로그래스바는 **무조건 DB 안 때리게**

### 2. 완료 상태는 TTL로 날리지 않는다 (폭탄 방지)
- 완료는 자주 조회되고, 크기도 작고, DB 부하를 막는 핵심
- DONE/FAILED/READY는 **TTL 없음** (권장)
- 또는 비용 방어 모드면 **24시간** (최소 1시간은 비추: 만료 폭탄 가능)
- Redis 메모리 걱정? 상태 JSON 몇백 바이트 수준이라 "완료 캐시"는 거의 공짜

### 3. 멀티테넌트 키는 "테넌트 네임스페이스" 필수
- `tenant:{tenant_id}:...` 고정
- 이거 안 하면 나중에 반드시 사고 난다

## 🧱 Redis 키 설계 (최종)

### Video
```
# 진행률 (기존)
tenant:{tid}:video:{vid}:progress (HASH 또는 JSON)

# 상태 (신규)
tenant:{tid}:video:{vid}:status (JSON)

# 세부 스텝 (선택)
tenant:{tid}:video:{vid}:step (JSON)
```

### Job (AI/Message 공통)
```
# 진행률 (기존)
tenant:{tid}:job:{jid}:progress

# 상태 (신규)
tenant:{tid}:job:{jid}:status
```

## ⏱ TTL 정책 (운영 안정성/가성비 밸런스)

### 진행 중 (PROCESSING)
- **TTL: 6시간** (2시간은 짧을 수 있음: 장애/재시도/대기 때문에)
- 매 progress 업데이트마다 TTL **"슬라이딩"** 갱신

### 완료 (DONE/FAILED/READY)
- **TTL: 없음** (권장)
- 또는 비용 방어 모드면 **24시간** (최소 1시간은 비추: 만료 폭탄 가능)

**Redis 메모리 걱정?**
- 상태 JSON 몇백 바이트 수준이라 "완료 캐시"는 거의 공짜에 가까움
- 반대로 DB 부하를 엄청 줄여줌

## 🧭 API 설계 (DB 부하 0로 만드는 핵심)

### 1. Progress/Status 전용 endpoint 신설 (강추)

#### Video
```
GET /media/videos/{id}/progress/
```
- **Redis-only 응답**
- 응답: `status` + `progress` + `step` + (완료면 `hls_path`/`duration`/`error`)
- **진행 중 폴링은 여기만**

#### Job
```
GET /api/v1/jobs/{job_id}/progress/
```
- **Redis-only**
- 응답: `status` + `progress` + (완료면 `result`/`error`)

### 2. 기존 Detail endpoint는 그대로 둔다
```
GET /media/videos/{id}/ → DB 기반 (기존 유지)
GET /api/v1/jobs/{job_id}/ → DB 기반 (기존 유지)
```

**프론트 전략:**
- 진행 중: **progress endpoint만 폴링**
- 완료 감지: **detail endpoint 1회 호출 후 폴링 종료**

이 방식이 **"성능 + 안정 + 유지보수"** 다 잡는 베스트.

## 🧩 워커 쪽 저장 로직 (최종)

### Video 워커

#### mark_processing
- DB 업데이트(필요) + Redis status 저장(PROCESSING, TTL 6h)

#### progress 업데이트(매 step)
- Redis progress만 업데이트
- Redis status는 "PROCESSING 유지" 정도만(선택)

#### complete/fail
- DB 업데이트(영구)
- Redis status 저장(READY/FAILED, **TTL 없음**)
- Redis progress는 남겨도 되고 지워도 됨(선택)

### AI/Message 워커

#### 상태 변화(START/PROCESSING)
- Redis status 저장(PROCESSING, TTL 6h)

#### 완료(DONE/FAILED)
- DB 저장
- Redis status에 **result/error까지 포함**해서 저장(=완료 후 DB 안 봐도 됨)

**여기서 result까지 Redis에 넣으면**
- JobStatusView에서 완료 시에도 DB 조회가 거의 사라짐

## 🔒 멱등성/일관성 규칙 (운영 안정성)

### 1. "DB가 소스 오브 트루스"
- Redis 쓰기 실패해도 DB가 저장되면 OK
- Redis는 캐시이자 진행 스트림

### 2. 완료 상태는 "단방향"
- PROCESSING → READY/FAILED/DONE
- READY/DONE/FAILED가 Redis에 있으면, 워커가 같은 이벤트를 또 보내도 덮어쓰기 OK (멱등)

### 3. 테넌트 검증
- progress endpoint에서 tenant_id 확인 후 키 조회
- 다른 테넌트가 다른 작업을 조회 못하게

## ⚡ 성능 추가 팁 (거의 공짜)

### 1. Redis 구조는 HASH 추천
- `HSET key field value`로 갱신하면 JSON dump/loads 비용 줄어듦
- step/progress/status 자주 바뀌는 애들은 HASH가 유리

### 2. 폴링 간격 "적응형"
```
0~10초: 1초
10~60초: 2초
60초 이상: 3~5초
완료 시 즉시 중지
```
=> DB는 이미 0이지만, Redis/네트워크 비용도 줄어듦

### 3. DB_CONN_MAX_AGE 줄이기
- 15~20 추천
- connection 점유 줄여서 안정성 상승

## 💸 가성비 관점 결론

### ❌ DB 폴링은 구조적으로 불필요

**지금 DB가 터지는 이유:**
1. DB 체급 작음 (근본) → RDS 크기 증가 필요
2. **폴링이 DB 때림 (불필요한 부하)** → Redis-only로 해결

**Redis-only로 바꾸면:**
- DB SELECT 폭격 **0으로 만들 수 있음** ✅
- 진행 상황은 "보기 편하라고 주는 것"일 뿐
- 진행 상황 때문에 DB 터지는 게 문제

**그래서 "합일점"은:**
- Redis progress/status endpoint 분리로 **폴링 DB 0 만들기** (핵심)
- **완료 캐시 TTL 제거(또는 24h)**로 만료 폭탄 방지
- RDS는 최소 small, 추천 medium (이건 체급 문제라 결국 필요)
- Excel bulk 최적화는 다음 단계(하지만 이게 장기적으로 비용을 더 줄임)

### 🔥 진짜 답

**❌ DB 폴링은 구조적으로 불필요**

폴링을 해야 한다면:
- 그건 Redis 설계가 불완전해서임
- Redis에 모든 데이터가 있으면 DB 폴링 불필요

**더 나아가면?**
- WebSocket 쓰면 폴링도 필요 없음
- Worker → Redis → PubSub → WebSocket → Frontend
- 근데 지금 단계에서는 Redis-only polling이면 충분히 안정적임

## ✅ 최종 실행 우선순위 (현실적인 로드맵)

### 오늘 (핵심)
1. progress/status 전용 endpoint 추가 (Redis-only)
2. 완료 상태 Redis 캐시(TTL 없음/24h)
3. 키에 tenant 네임스페이스 적용

### 이번주
4. 프론트 폴링을 progress endpoint로 전환 + 완료시 폴링 중지
5. DB_CONN_MAX_AGE 15~20 조정

### 다음
6. Excel bulk_create/업서트 최적화

---

## 🔧 구현 상세

### 1. Redis 키 헬퍼 (Tenant 네임스페이스)

**파일**: `apps/support/video/redis_status_cache.py`

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
        
        # 진행률과 상태 모두 TTL 갱신
        redis_client.expire(progress_key, ttl)
        redis_client.expire(status_key, ttl)
        
        return True
    except Exception as e:
        logger.warning("Failed to refresh video TTL: %s", e)
        return False
```

**파일**: `apps/domains/ai/redis_status_cache.py`

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
            status_data["result"] = result
        
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
        
        # 진행률과 상태 모두 TTL 갱신
        redis_client.expire(progress_key, ttl)
        redis_client.expire(status_key, ttl)
        
        return True
    except Exception as e:
        logger.warning("Failed to refresh job TTL: %s", e)
        return False
```

### 2. Progress/Status 전용 Endpoint

**파일**: `apps/support/video/views/progress_views.py` (신규 또는 수정)

```python
"""비디오 진행률/상태 전용 endpoint (Redis-only)"""
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from apps.support.video.models import Video
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
            progress = get_video_encoding_progress(video_id)
            step_detail = get_video_encoding_step_detail(video_id)
            remaining_seconds = get_video_encoding_remaining_seconds(video_id)
        
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

**파일**: `apps/domains/ai/views/job_progress_view.py` (신규)

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
            progress_adapter = RedisProgressAdapter()
            progress = progress_adapter.get_progress(job_id)
        
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

### 3. 워커 저장 로직 수정

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
    
    # DB 업데이트 (영구 저장)
    video.hls_path = str(hls_path)
    if duration is not None and duration >= 0:
        video.duration = int(duration)
    video.status = Video.Status.READY
    
    # ... (기존 코드)
    
    video.save(update_fields=update_fields)
    
    # ✅ Redis에 최종 상태 저장 (TTL 없음)
    try:
        from apps.support.video.redis_status_cache import cache_video_status
        # tenant_id는 video에서 가져오기 (예: video.session.tenant_id)
        tenant_id = video.session.tenant_id if hasattr(video, "session") and video.session else None
        if tenant_id:
            cache_video_status(
                tenant_id=tenant_id,
                video_id=video_id,
                status=Video.Status.READY.value,
                hls_path=hls_path,
                duration=duration,
                ttl=None,  # TTL 없음
            )
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
    
    return True, "ok"
```

**파일**: `academy/adapters/db/django/repositories_ai.py`

```python
def save(self, job: AIJob) -> None:
    """AIJob 저장 (완료 시 Redis에도 저장, result 포함)"""
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
    
    # ✅ 완료/실패 시 Redis에 상태 저장 (TTL 없음, result 포함)
    if job.status.value in ["DONE", "FAILED"]:
        try:
            from apps.domains.ai.redis_status_cache import cache_job_status
            from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
            
            # result 가져오기
            result_payload = None
            if job.status.value == "DONE":
                repo = DjangoAIJobRepository()
                result_payload = repo.get_result_payload_for_job(model)
            
            cache_job_status(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                status=job.status.value,
                job_type=job.job_type,
                error_message=job.error_message,
                result=result_payload,  # 완료 시 result 포함
                ttl=None,  # TTL 없음
            )
        except Exception as e:
            logger.warning("Failed to cache job status in Redis: %s", e)
    
    # ✅ PROCESSING 상태도 Redis에 저장 (TTL 6시간)
    elif job.status.value == "PROCESSING":
        try:
            from apps.domains.ai.redis_status_cache import cache_job_status
            cache_job_status(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                status=job.status.value,
                job_type=job.job_type,
                ttl=21600,  # 6시간
            )
        except Exception as e:
            logger.warning("Failed to cache job status in Redis: %s", e)
```

### 4. 프론트엔드 폴링 전환

**파일**: `src/shared/ui/asyncStatus/useWorkerJobPoller.ts`

```typescript
// Progress endpoint로 전환
function pollVideoJob(taskId: string, videoId: string, onSuccess?: () => void) {
  api
    .get<{
      status: string;
      encoding_progress?: number | null;
      encoding_remaining_seconds?: number | null;
      encoding_step_index?: number | null;
      encoding_step_total?: number | null;
      encoding_step_name?: string | null;
      encoding_step_percent?: number | null;
      hls_path?: string | null;
      duration?: number | null;
      error_reason?: string | null;
    }>(`/media/videos/${videoId}/progress/`)  // ✅ progress endpoint 사용
    .then((res) => {
      const status = res.data?.status;
      
      if (status === "PROCESSING") {
        // 진행 중: 진행률 업데이트
        const encodingProgress = res.data?.encoding_progress;
        const remainingSeconds = res.data?.encoding_remaining_seconds ?? null;
        const stepIndex = res.data?.encoding_step_index;
        const stepTotal = res.data?.encoding_step_total;
        const stepName = res.data?.encoding_step_name;
        const stepPercent = res.data?.encoding_step_percent;
        
        const encodingStep =
          typeof stepIndex === "number" &&
          typeof stepTotal === "number" &&
          typeof stepName === "string" &&
          typeof stepPercent === "number"
            ? { index: stepIndex, total: stepTotal, name: stepName, percent: stepPercent }
            : null;
        
        if (typeof encodingProgress === "number") {
          asyncStatusStore.updateProgress(
            taskId,
            Math.min(99, Math.max(1, encodingProgress)),
            remainingSeconds ?? undefined,
            encodingStep
          );
        }
      } else if (status === "READY") {
        // ✅ 완료: detail endpoint 1회 호출 후 폴링 종료
        onSuccess?.();
        asyncStatusStore.completeTask(taskId, "success");
      } else if (status === "FAILED") {
        asyncStatusStore.completeTask(taskId, "error", res.data?.error_reason || "영상 처리 실패");
      }
    })
    .catch(() => {});
}

function pollExcelJob(taskId: string, onSuccess?: () => void) {
  api
    .get<{
      status: string;
      progress?: { percent?: number; step_index?: number; step_total?: number; step_name_display?: string; step_percent?: number };
      error_message?: string | null;
      result?: any;
    }>(`/api/v1/jobs/${taskId}/progress/`)  // ✅ progress endpoint 사용
    .then((res) => {
      const status = res.data?.status;
      
      if (status === "PROCESSING") {
        // 진행 중: 진행률 업데이트
        const progress = res.data?.progress;
        if (progress?.percent !== undefined) {
          const encodingStep =
            typeof progress.step_index === "number" &&
            typeof progress.step_total === "number" &&
            typeof progress.step_name_display === "string" &&
            typeof progress.step_percent === "number"
              ? {
                  index: progress.step_index,
                  total: progress.step_total,
                  name: progress.step_name_display,
                  percent: progress.step_percent,
                }
              : null;
          asyncStatusStore.updateProgress(taskId, progress.percent, undefined, encodingStep);
        }
      } else if (status === "DONE") {
        // ✅ 완료: detail endpoint 1회 호출 후 폴링 종료
        onSuccess?.();
        asyncStatusStore.completeTask(taskId, "success");
      } else if (status === "FAILED") {
        asyncStatusStore.completeTask(taskId, "error", res.data?.error_message || "처리 실패");
      }
    })
    .catch(() => {});
}

// ✅ 적응형 폴링 간격
const getPollInterval = (elapsedSeconds: number): number => {
  if (elapsedSeconds < 10) return 1000;  // 0~10초: 1초
  if (elapsedSeconds < 60) return 2000;  // 10~60초: 2초
  return 3000;  // 60초 이상: 3초
};
```

## 📊 예상 효과

### Before (현재)
- 비디오 3개 + 엑셀 2개 진행 중
- 초당: 5번 DB SELECT (폴링)
- 10분: 약 3,000번 DB SELECT
- RDS CPU: 80-100%

### After (개선 후)
- 비디오 3개 + 엑셀 2개 진행 중
- 초당: **0번 DB SELECT** (진행 중 작업은 Redis만 조회)
- 완료 후: **0번 DB SELECT** (Redis 캐싱, TTL 없음)
- RDS CPU: **10-20%** (대폭 감소)

### 🔥 핵심 정리

**❌ DB 폴링은 구조적으로 불필요**

**진행 상황은 "보기 편하라고 주는 것"**
- 진행 상황 때문에 DB 터지는 게 문제
- 시청 로그, 정채 판단 프로그래스바는 **무조건 DB 안 때리게**

**Redis-only로 바꾸면:**
- DB SELECT 폭격 **0으로 만들 수 있음** ✅
- 진행 중: Redis만 조회
- 완료 후: Redis 캐싱 (TTL 없음)
- DB는 오직 fallback으로만 사용 (새로고침, 과거 기록)

## 🎯 구현 체크리스트

- [ ] Redis 키 헬퍼 생성 (Tenant 네임스페이스)
- [ ] Progress/Status 전용 endpoint 추가
- [ ] 워커 완료 시 Redis 저장 (TTL 없음, result 포함)
- [ ] 프론트엔드 폴링 전환 (progress endpoint)
- [ ] 적응형 폴링 간격 구현
- [ ] DB_CONN_MAX_AGE 15~20 조정
- [ ] 테스트 및 모니터링
