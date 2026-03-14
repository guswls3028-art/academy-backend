# Video 프로그래스바 Redis 흐름 분석 리포트

**목적:** 작업 박스(우하단)의 Video 프로그래스바가 Redis 기반으로 설계되어 있는지, 스펙과 일치하는지 검증.

---

## 1. 설계 요약 (스펙 일치)

| 구간 | 설계 | 구현 위치 |
|------|------|-----------|
| **워커** | RedisProgressAdapter + cache_video_status | batch_main.py, processor.py, redis_progress_adapter.py |
| **Redis 키** | tenant:{tenant_id}:video:{video_id}:status, :progress | redis_status_cache.py, redis_progress_adapter.py |
| **API** | Redis-only, DB 접근 없음 | progress_views.py, encoding_progress.py |
| **프론트** | pollVideoJob → /media/videos/{id}/progress/ | useWorkerJobPoller.ts |

**결론:** 스펙과 구현이 일치함. Redis 기반 프로그래스바 설계가 올바르게 반영되어 있음.

---

## 2. 전체 흐름

```
[Batch Worker]
  batch_entrypoint → SSM /academy/workers/env 로드 (REDIS_HOST, REDIS_PORT 필수)
  batch_main → job_set_running
            → cache_video_status(tenant_id, video_id, "PROCESSING")  ← status 키 설정
            → process_video(progress=RedisProgressAdapter)
  processor → progress.record_progress(job_id="video:{video_id}", ..., tenant_id=tenant_id)
  RedisProgressAdapter → tenant:{tenant_id}:video:{video_id}:progress 에 JSON 기록

[API]
  GET /media/videos/{id}/progress/
  → get_video_status_from_redis(tenant.id, video_id)  ← status 키 조회
  → status == PROCESSING 이면 get_video_encoding_progress(video_id, tenant.id)  ← progress 키 조회
  → encoding_progress, encoding_step_* 반환

[프론트]
  pollVideoJob(taskId, videoId) → GET /media/videos/{videoId}/progress/
  meta.jobId = video.id (Video PK)
  status === "PROCESSING" → asyncStatusStore.updateProgress(...)
  status === "READY" | "FAILED" → completeTask
```

---

## 3. Redis 키 형식

| 키 | 용도 | 설정 시점 |
|----|------|-----------|
| `tenant:{tid}:video:{vid}:status` | 상태 (PROCESSING, READY, FAILED) | batch_main 시작 시, 완료/실패 시 |
| `tenant:{tid}:video:{vid}:progress` | 진행률 (percent, step_index, step_total 등) | processor 각 단계마다 record_progress |

---

## 4. 작업 반영이 안 될 때 점검 사항

### 4.1 SSM /academy/workers/env

- **batch_entrypoint** REQUIRED_KEYS에 `REDIS_HOST`, `REDIS_PORT` 포함.
- 없으면 워커가 **부팅 시 exit 1** (작업 자체가 실행되지 않음).
- Bootstrap(`scripts/v1/core/bootstrap.ps1`)에서 requiredKeys에 REDIS_HOST, REDIS_PORT 포함.
- `.env`에 REDIS_HOST가 있어야 bootstrap 시 SSM에 반영됨.

**확인:**
```powershell
aws ssm get-parameter --name /academy/workers/env --with-decryption --query "Parameter.Value" --output text --region ap-northeast-2 | ConvertFrom-Json | Select-Object REDIS_HOST, REDIS_PORT
```

### 4.2 Batch CE ↔ Redis 네트워크

- Batch Compute Environment는 private subnet 사용.
- ElastiCache(Redis)와 **동일 VPC** 또는 **라우팅 가능**해야 함.
- Security Group: Batch CE 인스턴스 → Redis 6379 허용 필요.

**확인:** `scripts/archive/infra/verify_batch_network_connectivity.ps1` 참고.

### 4.3 API 서버 ↔ Redis

- API도 `get_redis_client()` 사용 (REDIS_HOST from settings).
- API와 Batch worker가 **동일 Redis 인스턴스**를 가리켜야 progress 조회 가능.
- API env: `/academy/api/env` 또는 EC2 `/opt/api.env`에 REDIS_HOST 포함.
- **Redis SG:** academy-redis-sg에 **Batch SG + App SG** 6379 인바운드 필수 (IaC: redis.ps1 Ensure-RedisSg6379FromWorkersAndApi).

### 4.4 타이밍 (Cold Start)

- Job 제출 → QUEUED → RUNNING 전환까지 1~2분 소요 가능 (CE cold start, SSM fetch, 이미지 pull).
- 그 동안 API는 status 키 없음 → **UNKNOWN** 반환 (정상).
- Worker 시작 후 `cache_video_status(PROCESSING)` + `record_progress` 호출되면 다음 폴링에서 반영.

### 4.5 Worker 로그 확인

- CloudWatch: `/aws/batch/academy-video-worker` (또는 job definition logConfiguration)
- `"Redis connected"` (libs/redis/client.py) → Redis 연결 성공.
- `"Progress recorded"` (redis_progress_adapter, debug 레벨) → progress 기록 성공.
- `"cache PROCESSING failed"` (batch_main) → cache_video_status 실패.

---

## 5. 관련 파일

| 역할 | 경로 |
|------|------|
| Batch 엔트리 | apps/worker/video_worker/batch_entrypoint.py |
| Batch 메인 | apps/worker/video_worker/batch_main.py |
| 프로세서 | src/infrastructure/video/processor.py |
| Redis 진행률 어댑터 | src/infrastructure/cache/redis_progress_adapter.py |
| Redis 상태 캐시 | apps/support/video/redis_status_cache.py |
| 진행률 조회 | apps/support/video/encoding_progress.py |
| 진행률 API | apps/support/video/views/progress_views.py |
| Redis 클라이언트 | libs/redis/client.py |
| 폴러 | academyfront/src/shared/ui/asyncStatus/useWorkerJobPoller.ts |
| 작업 박스 | academyfront/src/shared/ui/asyncStatus/AsyncStatusBar.tsx |

---

## 6. API Fallback (2025-03 적용)

Redis miss 시 다음 fallback 적용 (`progress_views.py`):

1. **Tenant fallback:** request.tenant ≠ Video tenant 시 (중앙 API 등) Video의 tenant로 Redis 재조회.
2. **DB fallback:** Redis 미연결 시 VideoTranscodeJob RUNNING → PROCESSING 반환, Video READY/FAILED → 해당 상태 반환.

→ 워커가 Redis에 쓰지 못해도 DB 기준으로 "인코딩 중" 표시 가능 (진행률은 0%로 표시).

## 7. 권장 조치

1. **SSM workers env 확인:** REDIS_HOST, REDIS_PORT 존재 여부.
2. **Redis SG 확인:** academy-redis-sg에 Batch SG, App SG 6379 인바운드.
3. **Batch 로그 확인:** CloudWatch `/aws/batch/academy-video-worker` → `"Redis connected"`.
4. **API 재시작:** SG 변경 후 API 컨테이너 재시작 필수 (Redis 클라이언트는 연결 실패 시 프로세스 수명 동안 재시도 안 함).
   - `pwsh scripts/v1/restart-api.ps1 -AwsProfile default`
5. **검증:** 영상 업로드 → 인코딩 중 프로그래스바 증가 확인.

---

## 8. 검증 체크리스트 (2025-03 적용)

| 항목 | 확인 방법 |
|------|-----------|
| Redis SG Batch | `aws ec2 describe-security-groups --group-ids sg-0f4069135b6215cad --query "SecurityGroups[0].IpPermissions"` → Batch SG 6379 |
| Redis SG App | 위와 동일 → App SG 6379 |
| API 재시작 | SSM Run Command `docker restart academy-api` 또는 `restart-api.ps1` |
| 배포 검증 | `pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default` |
