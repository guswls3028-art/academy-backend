# Video Worker 진단 체크리스트

영상 인코딩이 "인코딩 중" / "대기 중"에서 멈출 때, **정확한 진단을 위해** 아래 순서대로 확인하세요. (코드 기준: `apps/worker/video_worker/`, `apps/support/video/services/sqs_queue.py`, `scripts/check_sqs_worker_connectivity.py`)

---

## 1. 워커 프로세스 실행 여부

| 환경 | 확인 방법 |
|------|-----------|
| **Docker** | `docker ps \| findstr academy-video-worker` (Windows) / `docker ps \| grep academy-video-worker` (Linux) |
| **ASG(EC2)** | AWS 콘솔 → EC2 → Auto Scaling Groups → `academy-video-worker-asg` → Desired / InService 인스턴스 수 ≥ 1. 또는 `aws ec2 describe-instances --region ap-northeast-2 --filters "Name=tag:Name,Values=academy-video-worker" "Name=instance-state-name,Values=running" --query "Reservations[].Instances[].[InstanceId,State.Name]" --output table` |

- 컨테이너/인스턴스가 없으면 **메시지를 소비할 프로세스가 없는 것**이므로 인코딩이 진행되지 않음.

---

## 2. SQS 큐 연결 (API ↔ 워커 동일 큐 사용 여부)

- **큐 이름**: `VIDEO_SQS_QUEUE_NAME` (기본값 `academy-video-jobs`)
- **API**가 enqueue할 큐와 **Video Worker**가 receive할 큐 이름이 **완전히 동일**해야 함.

**진단 스크립트 (API와 동일한 환경변수로 실행):**

```bash
# Windows (PowerShell)
cd C:\academy
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.base"
python scripts/check_sqs_worker_connectivity.py
```

```bash
# Linux / API가 Docker인 경우
docker exec -it academy-api python scripts/check_sqs_worker_connectivity.py
```

- **확인 내용**: Video 큐 `get_queue_url` / `receive_message` 성공 여부, 실패 시 큐 미존재 / AWS 자격 증명·권한 오류 메시지.
- API와 워커가 **서로 다른 리전·계정**이면 큐가 달라질 수 있음 → 두 쪽의 `AWS_REGION`, `VIDEO_SQS_QUEUE_NAME`, 자격 증명이 같은지 확인.

---

## 3. 워커 필수 환경변수 (config 로드 실패 시 워커가 시작 직후 종료)

`apps/worker/video_worker/config.py`의 `load_config()`에서 **필수**로 읽는 값들:

| 변수 | 용도 |
|------|------|
| `API_BASE_URL` | API 서버 URL (끝 `/` 제거) |
| `INTERNAL_WORKER_TOKEN` | 내부 인증 |
| `R2_ENDPOINT` | R2 스토리지 |
| `R2_ACCESS_KEY` | R2 |
| `R2_SECRET_KEY` | R2 |
| `R2_BUCKET` 또는 `R2_VIDEO_BUCKET` | 영상 버킷 (기본 `academy-video`) |

- 하나라도 없으면 워커는 **시작 시 `config error: ...` 로 exit(1)**.  
- **확인**: 워커 컨테이너/프로세스 로그 맨 앞부분에 `config error` 또는 `Video Worker (SQS) started \| queue=academy-video-jobs` 가 있는지.

---

## 4. AWS 자격 증명 (SQS / R2 아님)

- Video Worker는 **SQS** receive/delete/changeVisibility 와 **R2** 업로드에 AWS 스타일 자격을 씀.
- **SQS**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`(또는 `AWS_DEFAULT_REGION`).  
- **R2**: `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT` (R2는 보통 별도 키이지만, SQS는 AWS 키 필요).
- 로컬/비프로덕션에서 SQS만 쓰는 경우 `.env`에 `AWS_REGION=ap-northeast-2` 및 AWS 키가 설정되어 있는지 확인.

---

## 5. 워커 로그로 단계별 확인

워커가 정상 기동되면 다음 로그가 순서대로 나옴 (코드: `apps/worker/video_worker/sqs_main.py`).

| 로그 패턴 | 의미 |
|-----------|------|
| `Video Worker (SQS) started \| queue=academy-video-jobs \| wait_time=20` | 기동 성공, Long Polling 대기 중 |
| `SQS unavailable (AWS credentials invalid or missing?)` | SQS 연결 실패 → 60초 후 재시도 |
| `SQS_MESSAGE_RECEIVED \| request_id=... \| video_id=... \| tenant_id=...` | 메시지 수신, 해당 영상 처리 시작 |
| `[HANDLER] Lock acquired, marking as PROCESSING video_id=...` | DB 상태 PROCESSING으로 변경 |
| `[HANDLER] process_fn completed video_id=... hls_path=...` | 인코딩 파이프라인 완료 |
| `SQS_JOB_COMPLETED \| request_id=... \| video_id=...` | 성공 후 메시지 삭제 |
| `SQS_JOB_FAILED \| request_id=... \| video_id=...` | 실패 (이후 DLQ 등 재시도 정책에 따름) |
| `Video processing failed: video_id=...` | `process_video` 또는 DB 완료 처리 중 예외 |

- **메시지를 전혀 받지 못함**: 큐 이름 불일치, 리전/계정 불일치, 또는 큐가 비어 있음(API에서 enqueue 실패 여부는 API 로그로 확인).
- **메시지는 받지만 곧바로 실패**: `config error` / R2/ffmpeg 관련 예외 → 위 환경변수 및 R2/ffmpeg 설치·경로 확인.

---

## 6. API에서 enqueue 성공 여부

- 업로드 완료 시점에 API가 SQS로 메시지를 보냄 (`apps/support/video/views/video_views.py` → `VideoSQSQueue().enqueue(video)`).
- **확인**: API 로그에서 `Video job enqueued: video_id=<id>` 가 찍히는지.  
- 503 또는 enqueue 실패 로그가 있으면 **API 쪽 SQS 설정·자격 증명**을 2번과 동일하게 점검.

---

## 7. Redis (진행률/상태 표시용, 인코딩 완료 여부와는 별개)

- Worker는 **진행률**을 Redis에 쓰고, **완료/실패 상태**는 DB + Redis에 씀 (`academy/adapters/db/django/repositories_video.py`).
- Redis가 없거나 연결 실패해도 **인코딩 자체는 진행**될 수 있으나, 진행률 API(`GET /media/videos/{id}/progress/`)가 UNKNOWN 또는 오래된 상태를 반환할 수 있음.
- **확인**: 워커와 API가 같은 `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` 를 보는지. (docker-compose 기준 `REDIS_HOST=redis`, 포트 6379.)

---

## 8. 요약: “인코딩이 안 된다”일 때 볼 것

1. **워커 프로세스**가 떠 있는지 (Docker/ASG).
2. **SQS**  
   - `scripts/check_sqs_worker_connectivity.py` 로 Video 큐 접근 가능 여부.  
   - API/워커의 `VIDEO_SQS_QUEUE_NAME`, `AWS_REGION`, 자격 증명 일치.
3. **워커 설정**  
   - `API_BASE_URL`, `INTERNAL_WORKER_TOKEN`, R2 관련 필수 env로 config 로드 성공 여부 (시작 로그).
4. **워커 로그**  
   - `SQS_MESSAGE_RECEIVED` → `SQS_JOB_COMPLETED` / `SQS_JOB_FAILED` / `Video processing failed` 까지 흐름으로, 어디에서 멈추거나 에러 나는지.
5. **API 로그**  
   - 업로드 완료 시 `Video job enqueued` 여부.

위 순서대로 확인하면, “워커가 안 떠 있는지 / 큐가 안 맞는지 / 설정이 빠진지 / 처리 중에 터지는지”를 정확히 구분할 수 있습니다.
