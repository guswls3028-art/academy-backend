# Video Worker (AWS Batch 전용)

영상 트랜스코딩은 **AWS Batch**만 사용한다. SQS/ASG 기반 워커는 제거된 레거시이다.

## 실행 경로

```
AWS Batch 컨테이너
  → ENTRYPOINT: python -m apps.worker.video_worker.batch_entrypoint
  → Command:     python -m apps.worker.video_worker.batch_main <job_id>
  → batch_entrypoint: SSM /academy/workers/env 로드 후 exec → batch_main
  → batch_main:  job_set_running → process_video → job_complete / job_fail_retry
```

## 파일 역할

| 파일 | 역할 |
|------|------|
| `batch_entrypoint.py` | SSM 파라미터 로드, 환경 변수 설정 후 `batch_main`으로 exec |
| `batch_main.py` | Job 1건 처리: RUNNING 전환, heartbeat, process_video, SIGTERM 처리 |
| `config.py` | 워커 설정 로드 |
| `video/` | 트랜스코더, 썸네일, R2 업로드 등 실제 인코딩 로직 |
| `download.py`, `utils.py` | 다운로드/유틸 |

## DB 생명주기 (Batch 경로)

- **QUEUED** → API에서 Job 생성 + submit_batch_job
- **RUNNING** → batch_main에서 `job_set_running(job_id)` 호출, `job_heartbeat` 주기 갱신
- **SUCCEEDED** → `job_complete(job_id, hls_path, duration)`
- **RETRY_WAIT** → 예외/SIGTERM 시 `job_fail_retry(job_id, reason)`; attempt_count >= 5면 `job_mark_dead`

## 인프라 실패 대응

- **SIGTERM/SIGINT**: `batch_main`에서 핸들러 등록 → `job_fail_retry(job_id, "TERMINATED")` 후 종료
- **Stuck**: `scan_stuck_video_jobs` (RUNNING + last_heartbeat_at 오래됨 → RETRY_WAIT + 재제출)
- **Batch↔DB 부정합**: `reconcile_batch_video_jobs` 관리 명령 (describe_jobs → DB 반영, 선택 --resubmit)

## 검증

- 로컬 import: `python -c "import apps.worker.video_worker.batch_main as m; assert hasattr(m, 'main')"`
- 스크립트: `python scripts/check_workers.py` (Video = batch_main), `python scripts/check_workers.py --docker`
- 실제 실행: AWS Batch job 제출 후 CloudWatch Logs `/aws/batch/academy-video-worker` 확인

## 문서

- 아키텍처: `docs/video/worker/VIDEO_WORKER_ARCHITECTURE_BATCH.md`
- 프로덕션 체크리스트: `docs/video/batch/VIDEO_BATCH_PRODUCTION_MINIMUM_CHECKLIST_AND_ROADMAP.md`
- 증거 보고서: `docs/video/batch/VIDEO_BATCH_SPOT_AND_INFRA_SAFETY_EVIDENCE_REPORT.md`
