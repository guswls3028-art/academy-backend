# Video Worker 안정화 — 수정 목록 · 변경 요약 · 배포 순서

## 목표 (달성 사양)

- **3시간 영상 인코딩이 중간에 실패하지 않는 구조**
- ASG 기반 (self-stop 없음), 1 worker = 1 job
- 360p + 720p 인코딩, duration 기반 timeout
- **SQS visibility_timeout >= ffmpeg timeout**
- 중복 처리 없음 (Redis idempotency), **작업 중 scale-in 없음** ((visible + in_flight) 기반 Lambda)

---

## 1. 수정 파일 목록

| # | 파일 | 변경 내용 |
|---|------|-----------|
| 1 | `apps/worker/video_worker/video/transcoder.py` | duration 기반 timeout 공식 명시, duration 알 때 항상 Popen+stderr (50% 정체 방지), 360p+720p 유지 |
| 2 | `apps/worker/video_worker/sqs_main.py` | `VIDEO_VISIBILITY_EXTEND_SECONDS` 기본값 21600(6h), 로그용 변수 정리 |
| 3 | `scripts/create_sqs_resources.py` | Video 큐 `VisibilityTimeout` 21600(6h) |
| 4 | `src/infrastructure/video/processor.py` | 트랜스코딩 주석 보강 (Popen+stderr, SQS 6h 연장) |
| 5 | `.env.example` | `VIDEO_SQS_VISIBILITY_EXTEND=21600` 추가 |

---

## 2. 변경 요약

### 2.1 ffmpeg timeout (duration 기반)

- **공식**: `timeout = max(7200, int(duration * 1.5))`, 상한 6시간(21600초).
- `_effective_ffmpeg_timeout(duration_sec, config_timeout)` 에서 적용.
- 3시간 영상 → 10800*1.5 = 16200초 → 4.5시간 timeout.

### 2.2 SQS visibility_timeout

- **조건**: `visibility_timeout >= ffmpeg timeout` (최대 6h).
- **큐 기본값**: 신규 생성 시 `VisibilityTimeout = 21600` (create_sqs_resources.py).
- **워커 연장**: 메시지 수신 후 작업 시작 직후 `ChangeMessageVisibility(receipt_handle, 21600)`.
- **환경변수**: `VIDEO_SQS_VISIBILITY_EXTEND` 기본 21600 (sqs_main.py).

### 2.3 360p + 720p 인코딩

- `HLS_VARIANTS` 는 이미 360p, 720p 두 개만 사용 (transcoder.py). 변경 없음.

### 2.4 진행률 50% 정체 방지

- **duration 이 알려진 경우 항상** `subprocess.Popen` + stderr 스트리밍 파싱.
- `use_popen = (duration_sec is not None and duration_sec > 0)` 로 분기.
- callback 유무와 관계없이 stderr에서 `time=` 파싱 후 진행률 갱신 → 50%에서 멈추지 않음.

### 2.5 ASG 스케일링 · scale-in 방지

- Lambda `academy-worker-queue-depth-metric` 이 **(visible + in_flight)** 기준으로 Video ASG desired capacity 직접 설정.
- 메시지가 한 건이라도 in_flight 이면 desired ≥ 1 유지 → **작업 중 scale-in 없음** (별도 파일 수정 없음).

### 2.6 중복 처리 방지

- Redis idempotency 락 `job:encode:{video_id}:lock` + SQS visibility 연장으로 재노출 전 완료 유도. 기존 동작 유지.

---

## 3. 배포 순서

1. **코드 배포**
   - 위 수정 반영 후 Video Worker 이미지 빌드·푸시 및 ASG 롤링 업데이트 (또는 기존 배포 파이프라인 실행).

2. **SQS 큐 설정 (기존 큐가 이미 있는 경우)**
   - AWS SQS 콘솔 → `academy-video-jobs` → Edit → **Visibility timeout = 21600** 저장.
   - 또는 CLI:
     ```bash
     aws sqs set-queue-attributes \
       --queue-url https://sqs.ap-northeast-2.amazonaws.com/<ACCOUNT>/academy-video-jobs \
       --attributes VisibilityTimeout=21600
     ```

3. **환경변수 (워커)**
   - `VIDEO_SQS_VISIBILITY_EXTEND=21600` 설정 (또는 기본값 21600 사용).
   - 필요 시 `.env` / SSM 등에 반영.

4. **Lambda**
   - Video ASG desired capacity 를 (visible + in_flight) 기반으로 이미 조정 중이면 추가 배포 없음.
   - 아직 적용 전이면 `deploy_worker_asg.ps1` 등으로 queue_depth Lambda 배포.

5. **검증**
   - 짧은 영상으로 인코딩 → 진행률 50% 이후 계속 증가하는지 확인.
   - 3시간급 영상 1건으로 end-to-end 완료 및 SQS visibility 초과/재노출 없음 확인.

---

## 4. 요약 체크리스트

- [x] ffmpeg timeout = max(7200, int(duration*1.5)), cap 6h  
- [x] SQS visibility_timeout >= ffmpeg timeout (큐·워커 연장 모두 21600)  
- [x] 360p + 720p 인코딩  
- [x] Popen + stderr 파싱으로 진행률 갱신 (50% 정체 없음)  
- [x] ASG (visible + in_flight) 기반 유지  
- [x] 작업 중 scale-in 없음 (Lambda desired 조정)  
- [x] 중복 처리 방지 (idempotency + visibility 연장)
