# Redis 보호 레이어

## 개요

SQS + Worker + DB 아키텍처는 **절대 변경하지 않음**.
Redis는 "상태 관리 및 보호 레이어"로만 도입.

```
SQS → Worker → (Redis 보호/상태 레이어) → PostgreSQL → R2
```

## Redis 사용 목적 (5가지)

### 1. 멱등성 (중복 실행 방지)

- Worker가 job 실행 전 `SETNX` 기반 락
- 키: `job:{job_id}:lock`
- TTL: 30분 (SQS Visibility Timeout과 충돌 방지)
- SETNX 실패 시 중복으로 간주 → 즉시 종료, 메시지 삭제
- 작업 완료/실패 시 `DEL`로 락 해제

### 2. 실시간 Job 상태 (SSOT)

- 진행률: `job:{job_id}:status` (Hash/JSON)
- `status`, `progress`, `current_step`, `updated_at`
- TTL: 1시간
- 완료 시 최종 상태만 DB 반영

### 3. 영상 시청 Heartbeat 버퍼링

- 5초 주기 heartbeat → DB 직행 금지, Redis Sorted Set 버퍼링
- 키: `session:{session_id}:watch`
- 정책 위반 시 `user:{user_id}:blocked` (TTL 포함)
- DB에는 세션 종료 시 Write-Behind

### 4. Write-Behind 전략

- 중간 상태/로그/heartbeat는 DB에 기록하지 않음
- Redis에서 처리 후, Completed/Failed 시 1회 Bulk Update
- Redis 장애 시 DB fallback

### 5. 인프라

- EC2 Docker Redis 또는 ElastiCache
- 환경변수: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`

## 환경변수

```env
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
```

- **미설정 시**: Redis 비활성, DB 기반 로직으로 자동 fallback
- **장애 시**: 연결 실패/타임아웃 시 DB fallback

## Redis 설정 (권장)

```
appendonly yes
appendfsync everysec
maxmemory-policy allkeys-lru
```

## 장애 시 동작

- Redis 연결 실패 → 즉시 DB fallback
- 서비스 중단 없음
- `libs.redis.client.get_redis_client()` → None 반환 시 호출부에서 DB 사용

## 아키텍처 유지 사항

- SQS 큐 구조 변경 금지
- Worker 파일 구조 유지
- API 응답 구조 유지
- 도메인 모델 수정 금지
