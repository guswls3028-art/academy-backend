# Reconcile (reconcile_batch_video_jobs) 로컬 스모크/락 테스트

Reconcile은 Redis 락(`video:reconcile:lock`)으로 단일 인스턴스만 실행되도록 한다.  
로컬에서 락 획득/락 실패/`--skip-lock` 동작을 확인하는 방법이다.

## 사전 조건

- Django 설정 및 DB 마이그레이션 완료
- (락 테스트 시) Redis 접근 가능: `REDIS_HOST` 등 설정 후 `get_redis_client()`가 반환하는 경우에만 락 적용

## 1. 락 없이 실행 (수동 원테이크)

```bash
# 락 무시하고 한 번만 실행 (DB 변경 가능)
python manage.py reconcile_batch_video_jobs --skip-lock

# dry-run: DB 변경 없이 로그만
python manage.py reconcile_batch_video_jobs --skip-lock --dry-run
```

기대: 정상적으로 reconcile 로직이 한 번 돌고 종료.  
로그에 `reconcile lock acquired, starting run` (또는 Redis 없으면 lock skip 메시지) 수준으로만 나와도 됨.

## 2. 락 실패 시 스킵 (두 번째 프로세스)

**터미널 1:** 락을 잡은 채로 오래 걸리게 유지 (실제로는 sleep 대신 무한 루프나 긴 작업을 시뮬할 수 있음):

```bash
# Redis에 락 키를 직접 설정 (TTL 600초)
# redis-cli SET video:reconcile:lock 1 NX EX 600
# 또는 첫 번째 프로세스를 --dry-run으로 돌리면서 두 번째를 동시에 실행
python manage.py reconcile_batch_video_jobs --dry-run
# 이 상태에서 터미널 2에서 아래 실행
```

**터미널 2:**

```bash
python manage.py reconcile_batch_video_jobs --dry-run
```

기대: 터미널 2에서는 락을 얻지 못하고 곧바로 종료.  
- stdout: `Reconcile skipped - lock held (another instance running).`  
- 로그: `event=reconcile_skipped`, `reason=lock_held` 구조화 로그.

## 3. Redis 없을 때

`REDIS_HOST`를 비우거나 Redis를 중지한 뒤:

```bash
python manage.py reconcile_batch_video_jobs --dry-run
```

기대: 경고 로그로 "Redis not available, skipping lock" 후 진행 (락 없이 실행).  
운영 환경에서는 Redis를 두고 락을 사용하는 것을 권장.

## 4. 구조화 로그 확인

다음 이벤트들이 필요 시 로그에서 확인 가능하다.

| event | 설명 |
|-------|------|
| `reconcile_skipped` | 락이 이미 잡혀 있어 스킵 |
| `reconcile_lock_acquired` | 락 획득 후 실행 시작 |
| `reconcile_describe_jobs_failed` | DescribeJobs 실패, DB 변경 없이 종료 |
| `reconcile_skip_succeeded` | Batch SUCCEEDED 건은 reconcile이 건드리지 않음 |
| `reconcile_not_found_skip` | not_found인데 DB가 RUNNING이면 덮어쓰지 않고 스킵 |
| `reconcile_not_found_defer` | not_found 카운트/나이 미달로 fail 보류 |
| `reconcile_not_found_fail` | not_found 3회 연속 또는 30분 초과 후 fail 처리 |

로그 포맷은 Django LOGGING 설정에 따라 다르며, `extra={...}` 필드가 JSON으로 나가도록 설정하면 위 키로 검색 가능하다.
