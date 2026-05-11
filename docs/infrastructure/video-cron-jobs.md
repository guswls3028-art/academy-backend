# Video Encoding Cron Jobs — SSOT

**Date:** 2026-05-11 (count 정정: 7종)
**Status:** Active

영상 인코딩 파이프라인 주변에서 도는 EventBridge cron 7종의 책임 분리 SSOT.
한 video가 여러 cron에 동시에 픽업되지 않도록 각 cron은 명확히 다른 상태 집합을 본다.

## Cron 7종

| Rule (EventBridge) | 주기 | 관리 명령 | 대상 상태 | 책임 |
|---|---|---|---|---|
| `academy-v1-enqueue-uploaded-videos` | 10분 | `enqueue_uploaded_videos` | `Video.status=UPLOADED` AND active job 없음 | 동시성 limit으로 거부된 업로드 회복 (테넌트 6 동시 한도) |
| `academy-v1-detect-stuck-videos` | 30분 | `detect_stuck_videos` | `Video.status=PROCESSING` AND old + active job 없음 | API/Worker 통신 실패로 status가 PROCESSING으로 굳어진 좀비 영상 회복 |
| `academy-v1-video-scan-stuck-rate` | 1시간 | `scan_stuck_video_jobs` | `Job.state=RUNNING` AND last_heartbeat_at 오래됨 | heartbeat 끊긴 RUNNING job → RETRY_WAIT + Batch 재제출 또는 DEAD |
| `academy-v1-reconcile-video-jobs` | 1시간 | `reconcile_batch_video_jobs` | `Job.state IN (QUEUED,RUNNING,RETRY_WAIT)` AND aws_batch_job_id 존재 | DB 상태 ↔ AWS Batch 실제 상태 동기화. Batch FAILED 감지 시 자동 재제출 (5회 한도 후 DEAD) |
| `academy-v1-recover-dead-video-jobs` | 2시간 | `recover_dead_video_jobs` | `Job.state=DEAD` AND error_code IN (transient set) AND age<7d AND auto_recovered 마킹 없음 | DEAD 도달한 transient 실패 영상을 1회에 한해 자동 재시도 (loop 방지 마킹) |
| `academy-v1-purge-raw-videos` | 매일 03:00 KST (UTC 18) | `purge_raw_videos` | R2 raw 객체 (3일+ 경과) | 인코딩 완료 후 원본 .mkv/.mp4 청소 |
| `academy-v1-cleanup-orphan-video-storage` (SSM, not Batch) | 일요일 04:00 KST (UTC SAT 19) | `cleanup_orphan_video_storage` (API EC2에서 `docker exec ... manage.py ... --apply --min-age-hours=72` SSM RunShellScript) | R2 orphan HLS prefix | DB에 매칭되는 Video 없는 HLS prefix 청소. Batch jobdef 없음. |

## 책임 경계 — 한 영상이 어느 cron에 잡히는가

```
[학원장 업로드]
   ↓
status=PENDING (file_key 없음)
   ↓ presigned PUT 완료
status=UPLOADED (file_key 있음)
   ↓ create_job_and_submit_batch (동시성 limit 통과 시 즉시)
   ↓ 거부되면 → enqueue-uploaded-videos가 10분 내 회수
Job 생성: state=QUEUED + aws_batch_job_id
   ↓
state=RUNNING (worker가 job_set_running)
   │ heartbeat 정상 → 정상 진행
   │ heartbeat 끊김 (>20분/45분) → video-scan-stuck이 RETRY_WAIT 처리
   │ Batch 자체 FAILED → reconcile이 자동 재제출
   ↓
state=SUCCEEDED → Video.status=READY
   ↓ raw 파일 3일 후 → purge-raw-videos가 R2에서 삭제
```

## 만일의 경우 — 좀비 video

`status=PROCESSING`인데 active job이 없는 케이스 (Worker가 RUNNING 도중 job 자체는 SUCCEEDED됐지만 Video status를 안 바꾼 상황). detect-stuck-videos가 30분 주기로 잡아내서 새 job 생성.

`status=PENDING`인데 file_key 있고 1시간+ 안 움직인 케이스. `recover_stuck_videos` (manual 명령, 자동 cron 미설정) 으로 status=UPLOADED 전환 후 enqueue.

`Job.state=DEAD` (5회 재시도 다 쓰고 죽은 영상). `recover-dead-video-jobs` cron이 2시간마다 transient 사유(`MAX_ATTEMPTS`/`RECONCILE_MAX_ATTEMPTS`/`TIMEOUT`/empty)인 DEAD를 새 job 1회에 한해 자동 재시도. 진짜 컨텐츠 결함이면 다시 DEAD로 굳어 학원장이 admin UI에서 재업로드.

## 운영 주의사항

- **모든 cron은 `--dry-run` 지원**. 영향 큰 작업(재제출, DELETE)은 dry-run으로 먼저 확인.
- **7개 cron 모두 lock 또는 conditional UPDATE로 중복 처리 차단**. 동일 영상이 두 cron에 동시 픽업돼도 둘 중 하나만 성공.
- **Reconcile 5회 재시도 한도 + recover-dead 1회 자동 회복**. 자동 회복도 실패하면 admin UI에서 수동 재업로드. DEAD job 누적 모니터링 필요.

## 관련 파일

- 관리 명령: `backend/apps/domains/video/management/commands/`
- 코드 SSOT: `backend/apps/domains/video/services/video_encoding.py`, `batch_submit.py`
- 모델: `backend/apps/domains/video/models.py` (`Video`, `VideoTranscodeJob`, `VideoOpsEvent`)
- CE/JobDef: AWS Batch — `academy-v1-video-batch-ce-200gb`(short CE) + `academy-v1-video-batch-jobdef`(VCPU=8, MEM=16GB, retryStrategy, timeout=6h) 단일 운영. long path는 2026-05-10 완전 폐기 (queue/jobdef/CE 모두 AWS에서 삭제).
