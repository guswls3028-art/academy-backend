# 배포 가능 상태 격차 (Deployment Readiness Gap)

## 현재 상태 요약

### ✅ 완료된 것 (코드 레벨)

| 항목 | 상태 |
|------|------|
| Hexagonal 분리 | 성공 |
| Forbidden import | 0건 |
| Repository 격리 | 성공 |
| Handler ORM 없음 | 성공 |
| Idempotency 로직 | 존재 |

### ❌ 아직 증명 안 된 것 (실행 레벨)

| 항목 | 설명 |
|------|------|
| Worker 단독 실행 | Docker 이미지가 Django 없이 동작하는가? |
| Django 없이 DB 연결 | Worker가 DB만으로 상태 갱신 가능한가? |
| Redis 실연결 | `get_redis_client()` 실제 연결 성공하는가? |
| SQS 실연결 | 큐에 메시지 넣고 받을 수 있는가? |
| 실제 job 처리 E2E | 메시지 1건 → 처리 → DB 갱신 전체 흐름 |

---

## 🔥 진짜 배포 가능 조건 (4개 모두 통과 필요)

```
[1] Worker Docker 이미지 단독 실행 성공
[2] Redis 실연결 PASS
[3] SQS에서 메시지 1건 넣고 → 처리 성공
[4] DB 상태 업데이트 성공
```

**현재:** 4개 중 **0.5개** 통과 (코드/구조 검증만 완료, 인프라 실기 미검증)

---

## 검증 방법

### 수동 검증

```powershell
# 1. Worker Docker 단독 실행
docker run --rm -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod \
  -e DB_HOST=... -e REDIS_HOST=... --env-file .env \
  academy-video-worker:latest python -c "import apps.worker.video_worker.sqs_main; print('OK')"

# 2. Redis 연결
python -c "from libs.redis.client import get_redis_client; c=get_redis_client(); print('OK' if c and c.ping() else 'FAIL')"

# 3+4. SQS/DB E2E (docker-compose up 후)
# - API에서 Video 업로드 → SQS enqueue
# - Worker 실행 → 메시지 처리 → Video.status = READY
```

### 자동 검증 스크립트

인프라(Redis, SQS, DB)가 기동된 상태에서:

```powershell
python scripts/deployment_readiness_check.py
```

---

## Worker 전용 settings 적용 완료

- `apps/api/config/settings/worker.py`: base 상속 제거, corsheaders/rest_framework/django_extensions 등 제외
- `docker-compose.yml`: video-worker, ai-worker-cpu, ai-worker-gpu, messaging-worker 모두 `DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker` 사용
- Worker Docker 이미지 변경 반영을 위해 **재빌드 필요**: `./docker/build.ps1` 또는 `./docker/build.sh`

## 스크립트 실행 결과 (참고)

인프라 미기동 시 예상 출력:

```
통과: 0/4
[FAIL] 미통과 항목을 해결한 후 재실행하세요.
```

- **[1] Docker**: `worker.py` 적용 후 이미지 재빌드 필요. corsheaders 등 API 의존성 제거됨.
- **[2] Redis**: `REDIS_HOST` 등 `.env` 설정 필요
- **[3] SQS**: Django setup, AWS 자격증명 필요
- **[4] DB**: `DB_HOST` 등 DB 연결 정보 필요

## 다음 액션

1. **Redis/SQS/DB** 로컬 또는 스테이징 환경 기동
2. `.env` 설정 (REDIS_HOST, DB_*, AWS_*)
3. `python scripts/deployment_readiness_check.py --docker` 실행 → 4/4 통과 목표
4. 미통과 시: 연결 설정, IAM, Docker 이미지(worker requirements vs settings) 점검
