# Video Worker — Job 기반 로직 Patch 요약

## 변경 파일

1. `apps/worker/video_worker/sqs_main.py` — Job 기반 처리, VIDEO_FAST_ACK 제거
2. `apps/support/video/services/sqs_queue.py` — enqueue_by_job, receive에 job_id 반환
3. `academy/adapters/db/django/repositories_video.py` — Job repository 함수 추가
4. `apps/support/video/models.py` — VideoTranscodeJob, Video.current_job
5. `apps/support/video/migrations/0003_*.py` — 마이그레이션

## sqs_main 변경 포인트

- **job_id 필수**: 메시지에 job_id 없으면 NACK (legacy 포맷)
- **VIDEO_FAST_ACK 제거**: receive 직후 delete 금지, 성공 후에만 DeleteMessage
- **heartbeat**: 60초마다 ChangeMessageVisibility + job_heartbeat
- **claim**: job_claim_for_running(job_id, worker_id) — rowcount=1일 때만 실행

## 다음 단계

- retry API: Job 생성 + enqueue_by_job
- internal_views: BacklogCount를 job_count_backlog()로 변경
- reconcile → scan_stuck_video_jobs (Job 기반)
