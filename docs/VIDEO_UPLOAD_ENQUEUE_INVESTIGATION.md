# Video Upload → SQS Enqueue 조사 보고서

## 1) Upload → enqueue 흐름 실제 코드 위치

| 단계 | 파일 | 함수/라인 | 설명 |
|------|------|-----------|------|
| 1 | `apps/support/video/views/video_views.py` | `upload_complete` (325) | POST `/media/videos/{pk}/upload/complete/` API |
| 2 | `video_views.py` | `_upload_complete_impl` (355) | 실제 처리: PENDING 검증 → head_object → presigned_get → ffprobe |
| 3 | `video_views.py` | 408~461 | `video.status = UPLOADED` 저장 후 `VideoSQSQueue().create_job_and_enqueue(video)` 호출 |
| 4 | `apps/support/video/services/sqs_queue.py` | `create_job_and_enqueue` (127) | Job 생성 + `enqueue_by_job(job)` |
| 5 | `sqs_queue.py` | `enqueue_by_job` (159) | `queue_client.send_message(queue_name=VIDEO_SQS_QUEUE_NAME, message=...)` |
| 6 | `libs/queue/client.py` | `SQSQueueClient.send_message` (105) | boto3 `sqs.send_message(QueueUrl, MessageBody)` |

**프론트엔드 호출:**
- `academyfront/src/features/videos/utils/videoUpload.ts` → `uploadFileToR2AndComplete` → `api.post(\`/media/videos/${videoId}/upload/complete/\`)`

## 2) enqueue가 실행되지 않는 가능 원인

| 원인 | 조건/코드 | DB status | 대응 |
|------|-----------|-----------|------|
| **upload_complete 미호출** | R2 PUT 실패, 모달 조기 닫힘 | PENDING | 프론트: R2 PUT 후 반드시 complete 호출 |
| **video.status != PENDING** | 409 Conflict (367행) | PENDING 유지 | 중복 호출 방지 |
| **head_object 실패** | R2 객체 없음/비어있음 (380행) | PENDING 유지 | R2/API 동일 버킷·자격증명 확인 |
| **create_presigned_get_url 실패** | 409 (394행) | PENDING 유지 | R2 설정 확인 |
| **create_job_and_enqueue 실패** | SQS send_message False/예외 | UPLOADED (이미 저장됨) | 503 반환, CloudWatch 로그 |
| **video.status != UPLOADED** | create_job_and_enqueue 내부 (134행) | - | 내부 버그 시에만 발생 (업로드 경로에선 UPLOADED 저장 후 호출) |

**Silent fail 여부:** `libs/queue/client.py` send_message는 예외 시 `logger.error` 후 `return False` — 예외를 삼키지만 로그는 남김. `sqs_queue.enqueue_by_job`도 `logger.error`/`logger.exception` 사용.

## 3) 수정된 코드 (요약)

- **sqs_queue.py**: enqueue 성공 로그에 `"Video {id} enqueue to SQS {queue_name}"` 추가, 실패 시 `logger.error` + `exc_info=True`
- **video_views.py**: `create_job_and_enqueue` 실패 시 `VIDEO_UPLOAD_ENQUEUE_FAILED` logger.error 추가
- **sqs_queue.py**: `create_job_and_enqueue` 내 status != UPLOADED, tenant 예외 시 `logger.error`로 상향

## 4) ACCEPTANCE TEST

```bash
# Upload 후 즉시
aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/{account}/academy-video-jobs \
  --attribute-names ApproximateNumberOfMessages
# ApproximateNumberOfMessages >= 1 이어야 함
```

**CloudWatch 로그 확인:**
- 성공: `Video {video_id} enqueue to SQS academy-video-jobs`
- 실패: `VIDEO_UPLOAD_ENQUEUE_FAILED`, `enqueue_by_job exception`, `Failed to enqueue video job`
