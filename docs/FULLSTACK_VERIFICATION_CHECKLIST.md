# 풀스택 검증 체크리스트 (Full-Stack Verification Checklist)

**목적**: 헥사고날 구조 + Redis 보호막 적용 후, 의존성·멱등성·인프라 격리·하트비트 버퍼링이 설계대로 동작하는지 검증

**실행자**: 터미널에서 직접 실행하여 확인

---

## 1️⃣ 의존성 검사 (The "Clean" Check)

워커가 Django의 무거운 기능(DRF, Admin, URL 라우팅 등)을 참조하지 않는지 확인합니다.

### 1.1 금지된 임포트 체크

```powershell
# PowerShell
cd C:\academy
python scripts/check_worker_forbidden_imports.py
```

**성공 기준**: `OK: No forbidden imports in apps/worker/**` 출력

### 1.2 실제 의존성 트리 확인

```powershell
# Video Worker import (Django 설정 필요하므로 base 사용)
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.base"
python -c "import apps.worker.video_worker.sqs_main; print('Import Success')"
```

**성공 기준**:
- `Import Success`가 출력되어야 함
- 로그에 `apps.api`, `apps.admin`, `rest_framework` 관련 로드 메시지가 없어야 함

### 1.3 (선택) pip-deptree로 worker 의존성 확인

```powershell
pip install pip-deptree
pip-deptree -p djangorestframework 2>$null | Select-String "worker"
# worker requirements에 djangorestframework가 있어서는 안 됨
# (worker-video.txt, worker-ai.txt, worker-messaging.txt는 api.txt를 포함할 수 있으나,
#  실제 worker 런타임에서 DRF/Admin import 되지 않음을 1.1, 1.2로 검증)
```

---

## 2️⃣ 멱등성 및 Redis 락 검사 (The "Protection" Check)

동일한 `job_id`를 가진 메시지가 두 번 들어왔을 때, Redis가 방패 역할을 하는지 확인합니다.

### 2.1 사전 준비

- Redis 실행 중 (`docker run -d -p 6379:6379 redis` 또는 로컬 Redis)
- `.env`에 `REDIS_HOST`, `REDIS_PORT` 설정

### 2.2 Redis 모니터링 모드 가동

```powershell
# Redis 컨테이너가 있다면
docker exec -it <redis_container> redis-cli monitor

# 로컬 Redis
redis-cli monitor
```

### 2.3 워커 수동 실행 + 동일 메시지 2회 투척

**터미널 1**: Video Worker 실행 (SQS 큐에 메시지가 있어야 동작)

```powershell
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.base"
python -m apps.worker.video_worker.sqs_main
```

**터미널 2**: SQS에 동일 `video_id` 메시지 2건 수동 전송 (AWS CLI 또는 스크립트)

```powershell
# 예: create_sqs_test_message.ps1 또는 boto3 스크립트로 동일 video_id 2회 send
```

**성공 기준**:
- **첫 번째 요청**: Redis에 `SET job:video:{video_id}:lock 1 NX EX 1800` 기록 → DB 업데이트 발생
- **두 번째 요청**: Redis 락 실패 (`SET NX` 실패) → Handler에서 `acquire_lock`이 `False` 반환 → `"skip"` 반환 → `IDEMPOTENT_SKIP job_id=video:{video_id} reason=duplicate` 로그와 함께 DB 접근 없이 메시지 삭제 후 종료

### 2.4 코드 레벨 확인 (락 키 형식)

Video Worker 멱등성 키: `job:video:{video_id}:lock`  
관련 코드: `src/application/video/handler.py` (job_id=`video:{video_id}`), `src/infrastructure/cache/redis_idempotency_adapter.py` (key=`job:{job_id}:lock`)

```powershell
# 락 어댑터 구현 확인
Select-String -Path "src/infrastructure/cache/redis_idempotency_adapter.py" -Pattern "job:|set\(|nx=True|acquire"
```

---

## 3️⃣ 인프라 독립성 검사 (The "Hexagonal" Check)

리포지토리가 Django 모델로부터 로직을 격리했는지 코드로 확인합니다.

### 3.1 VideoRepository에 ORM 로직 집중 확인

```powershell
# Video.objects 사용이 video_repository에만 있는지
Select-String -Path "src/infrastructure/db/video_repository.py" -Pattern "Video\.objects"
# 결과: mark_processing, complete_video, fail_video 내부에만 있어야 함
```

**성공 기준**: `Video.objects.filter().update()` 또는 `Video.objects.select_for_update()` 같은 로직이 `src/infrastructure/db/video_repository.py` 안에만 있어야 함

### 3.2 Worker에서 Video 모델 직접 import 없음 확인

```powershell
Select-String -Path "apps/worker/video_worker/sqs_main.py" -Pattern "from apps|import Video"
```

**성공 기준**:
- `from apps.models import Video` 줄이 **없어야** 함
- `from src.infrastructure.db.video_repository import VideoRepository` 또는 Handler/Adapter 경로만 사용

### 3.3 현재 구조 (검증 완료 기준)

| 파일 | 역할 |
|------|------|
| `apps/worker/video_worker/sqs_main.py` | VideoRepository, ProcessVideoJobHandler, VideoSQSAdapter 사용 |
| `src/infrastructure/db/video_repository.py` | `Video.objects` 사용 (ORM 격리) |
| `src/application/video/handler.py` | Port(IVideoRepository) 기반, ORM 직접 호출 없음 |

---

## 4️⃣ 하트비트 버퍼링 검사 (The "Performance" Check)

5초 주기 하트비트가 DB가 아닌 Redis로 가고 있는지 확인합니다.

### 4.1 Redis 키 구조 (실제 구현)

문서의 `user:1:watch` 대신, 실제 코드는 **세션 기반** 키를 사용합니다:

| 키 | 용도 |
|----|------|
| `session:{session_id}:watch` | Sorted Set — 시청 타임스탬프 버퍼 |
| `session:{session_id}:meta` | 세션 TTL 연장 (heartbeat_session → buffer_heartbeat_session_ttl) |

### 4.2 테스트 방법

**1) 재생 세션 발급**

```http
POST /api/v1/video/playback/session/
Authorization: Bearer <token>
Content-Type: application/json
{"device_id": "test-device-123"}
```

**2) 하트비트 API 호출 (5초 주기로 여러 번)**

```http
POST /api/v1/video/playback/heartbeat/
Authorization: Bearer <token>
Content-Type: application/json
{"session_id": "<발급받은 session_id>", "ttl_seconds": 3600}
```

**3) Redis에서 버퍼 확인**

```bash
# Redis CLI
redis-cli

# 세션별 시청 버퍼 (session_id는 발급 응답에서 획득)
ZRANGE session:<session_id>:watch 0 -1

# 세션 메타 (TTL 연장 시 갱신)
GET session:<session_id>:meta
```

**성공 기준**:
- **PostgreSQL 로그**: 하트비트 호출 시 `last_seen`, `expires_at` UPDATE 쿼리가 **남지 않아야** 함 (Redis 사용 시)
- **Redis**: `session:{session_id}:watch` 또는 `session:{session_id}:meta`에 데이터가 쌓여 있어야 함

### 4.3 Redis 미사용/장애 시 Fallback

`libs.redis.is_redis_available()`가 `False`이거나 `get_redis_client()`가 `None`이면 `heartbeat_session`은 DB로 fallback합니다.  
Redis 연결 정상 시에는 DB 쓰기가 발생하지 않아야 합니다.

### 4.4 관련 코드

- `apps/support/video/services/playback_session.py` — `heartbeat_session()` → `buffer_heartbeat_session_ttl()`
- `libs/redis/watch_buffer.py` — `buffer_heartbeat()`, `buffer_heartbeat_session_ttl()`

---

## 요약: 4단계 체크리스트

| 단계 | 명령/확인 | 성공 기준 |
|------|-----------|-----------|
| 1. 의존성 | `check_worker_forbidden_imports.py` + import 테스트 | OK 출력, Import Success, 금지 패턴 없음 |
| 2. 멱등성 | Redis monitor + 동일 job 2회 투척 | 첫 요청: SETNX/DB 갱신, 둘째: skip, DB 접근 없음 |
| 3. 헥사고날 | `Video.objects` 위치, Worker import 검사 | video_repository에만 ORM, sqs_main에 Video 직접 import 없음 |
| 4. 하트비트 | heartbeat API 호출 후 Redis/DB 로그 | Redis에 session:*:watch/meta 쌓임, PostgreSQL에 heartbeat 관련 UPDATE 없음 |

---

## 자동화 스크립트

배포 직전 한 번에 검증:

```powershell
python scripts/final_sanity_check.py
python scripts/final_sanity_check.py --with-redis   # Redis 실기 테스트 포함
python scripts/final_sanity_check.py --skip-imports # Django/DB 미설정 시 import 검사 생략
```

## 참고 문서

- [REDIS_PROTECTION_LAYER.md](REDIS_PROTECTION_LAYER.md) — Redis 보호 레이어 설계
- [HEXAGONAL_ARCHITECTURE.md](HEXAGONAL_ARCHITECTURE.md) — 헥사고날 구조
- [DEPLOYMENT_MASTER_GUIDE.md](DEPLOYMENT_MASTER_GUIDE.md) — 배포 가이드
