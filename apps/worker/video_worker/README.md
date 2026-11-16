# Video Worker (AWS Batch 전용)

영상 트랜스코딩은 **AWS Batch**만 사용한다. SQS/ASG 기반 워커는 제거된 레거시이다.

## 실행 경로

```
AWS Batch 컨테이너
  → ENTRYPOINT: python -m apps.worker.video_worker.batch_entrypoint
  → Command:     python -m apps.worker.video_worker.batch_main <job_id>
  → batch_entrypoint: SSM /academy/workers/env 로드 후 exec → batch_main
  → batch_main:  job_set_running → process_video → job_complete / job_fail_retry / job_mark_dead
```

## 파일 역할

워커 entry만 이 디렉토리에 잔존. 인코딩 코어/유틸은 `academy/adapters/video/` 가 정본.

| 파일 | 역할 |
|------|------|
| `batch_entrypoint.py` | SSM 파라미터 로드, 환경 변수 설정 후 `batch_main`으로 exec |
| `batch_main.py` | Batch 1 job 실행: RUNNING 전환, heartbeat, `academy.adapters.video.processor.process_video` 호출, SIGTERM 처리 |

`academy/adapters/video/` 안의 모듈 (config, downloader, transcoder, thumbnail, duration, r2_uploader, validate, current_transcode, utils, processor) 가 실제 인코딩 로직 정본. Worker entry는 thin wrapper.

## DB 생명주기 (Batch 경로)

- **QUEUED** → API에서 Job 생성 + submit_batch_job
- **RUNNING** → batch_main에서 `job_set_running(job_id)` 호출, `job_heartbeat` 주기 갱신
- **SUCCEEDED** → `job_complete(job_id, hls_path, duration)`
- **RETRY_WAIT** → 원인이 확인되지 않은 FFmpeg 실패나 일시 장애는 기존 재시도 경로를 따른다. 배포된 최대 시도 횟수에 도달하면 `job_mark_dead`로 종료한다.
- **DEAD** → FFmpeg가 원본에 영상 스트림이 없다고 명시하면 현재 Batch 시도만 한 번 기록하고 즉시 종료한다. 작업의 오류 코드는 `SOURCE_NO_VIDEO_STREAM`이며 교사 화면에는 실패가 표시된다. 원본에 영상 트랙이 있는 파일을 다시 업로드해 복구할 수 있다. 예전 Batch 시도와 취소 요청이 진행 중인 작업은 이 경로에서 갱신하지 않는다.

## FFmpeg 진단

FFmpeg는 오류 수준 stderr와 stdout 진행률을 별도 파이프로 보낸다. 종료 코드가
0이 아니면 Batch 로그의 `BATCH_JOB_FAILED` 이벤트에 식별자, 종료 코드,
재시도 가능 여부, 최대 8줄(줄당 300자)의 오류 진단을 남긴다. 진단에서는
로컬 작업 경로와 URL을 가리고 DB에는 짧은 종료 사유만 저장한다. 원본에
영상 스트림이 없다는 명시적 진단만 자동 재시도와 DEAD 복구 대상에서 제외된다.
Spot 종료, 시간 초과, 파일 다운로드·저장소 오류는 기존 복구 경로를 따른다.

## 인프라 실패 대응

- **SIGTERM/SIGINT**: `batch_main`에서 핸들러 등록 → `job_fail_retry(job_id, "TERMINATED")` 후 종료
- **Stuck**: `scan_stuck_video_jobs` (RUNNING + last_heartbeat_at 오래됨 → RETRY_WAIT + 재제출)
- **Batch↔DB 부정합**: `reconcile_batch_video_jobs` 관리 명령 (describe_jobs → DB 반영, 선택 --resubmit)

## 검증

R2 게시 후 검증은 master/variant 재생목록을 직접 읽고, 해당 영상의 정확한
final prefix를 `ListObjectsV2`로 끝까지 순회해 모든 참조 segment와
`thumbnail.jpg`의 존재를 확인한다. R2의 strong-consistent LIST를 사용하므로
파일마다 순차 HEAD를 보내던 비용과 Batch 대기를 없애며 표본 검사로 축소하지
않는다. 2,500개 segment는 2,501회 HEAD 대신 3페이지 LIST로 검증한다.
목록 조회/페이지 오류, 재생목록·segment·썸네일 누락, variant별 최소 segment
미달은 계속 `UploadIntegrityError`로 게시 완료를 막고 기존 재시도 경로로 간다.
다른 tenant/영상 prefix의 동명 파일은 검증을 통과시키지 않는다. 기존 R2 객체,
DB 경로, tmp→final 게시 순서와 사용자 승인 데이터는 바꾸지 않는다.
병렬 tmp→final 복사도 쓰레드를 시작하기 전에 만든 단일 boto3 low-level client와
워커 수에 맞춘 연결 풀을 재사용한다. 객체마다 client/TLS 연결을 재생성하지
않으며, 복사 실패 때 tmp를 보존하는 기존 재시도 순서는 유지한다.

- 집중 회귀: `python -m unittest apps.domains.video.tests.test_video_hls_integrity_policy`
- FFmpeg/Batch 실패 회귀: `python -m pytest tests/test_video_transcoder.py apps/domains/video/tests/test_video_job_failure_status.py`
- R2 LIST 일관성: <https://developers.cloudflare.com/r2/reference/consistency/>

- 로컬 import: `python -c "import apps.worker.video_worker.batch_main as m; assert hasattr(m, 'main')"`
- 스크립트: `python scripts/check_workers.py` (Video = batch_main), `python scripts/check_workers.py --docker`
- 실제 실행: AWS Batch job 제출 후 CloudWatch Logs `/aws/batch/academy-video-worker` 확인

## 문서

- 아키텍처: `docs/video/worker/VIDEO_WORKER_ARCHITECTURE_BATCH.md`
- 프로덕션 체크리스트: `docs/video/batch/VIDEO_BATCH_PRODUCTION_MINIMUM_CHECKLIST_AND_ROADMAP.md`
- 증거 보고서: `docs/video/batch/VIDEO_BATCH_SPOT_AND_INFRA_SAFETY_EVIDENCE_REPORT.md`
