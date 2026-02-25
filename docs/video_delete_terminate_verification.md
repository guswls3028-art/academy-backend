# 영상 삭제 시 Batch Terminate 검증 가이드

## 개요

프론트에서 Video 삭제(또는 삭제 API 호출) 시, 해당 영상에 연결된 **진행 중** AWS Batch 인코딩 Job이 있으면 `TerminateJob`으로 즉시 중단한다.  
삭제 요청 자체는 Terminate 실패와 무관하게 성공한다.

## 로그/이벤트로 확인하는 방법

### 1) API 서버 로그

- **VIDEO_DELETE_TERMINATE_OK**  
  Terminate 요청이 성공했을 때  
  - `video_id`, `job_id`, `aws_batch_job_id` 포함

- **VIDEO_DELETE_TERMINATE_FAILED**  
  Terminate 실패(권한/네트워크/없는 job 등)  
  - 동일 키 + `error` 메시지

- **Video delete: job DEAD**  
  DB에서 해당 Job을 DEAD로 마킹했을 때  
  - `video_id`, `job_id` 포함

### 2) DB 이벤트 (VideoOpsEvent)

- **VIDEO_DELETE_TERMINATE_REQUESTED**  
  Terminate 시도 시 1건 생성 (video_id, job_id, aws_batch_job_id, payload.reason)

- **VIDEO_DELETE_TERMINATE_FAILED**  
  Terminate 실패 시 1건 생성 (payload.error 포함)

조회 예:

```sql
SELECT type, video_id, job_id, aws_batch_job_id, payload, created_at
FROM video_videoopsevent
WHERE type IN ('VIDEO_DELETE_TERMINATE_REQUESTED', 'VIDEO_DELETE_TERMINATE_FAILED')
ORDER BY created_at DESC
LIMIT 20;
```

### 3) Worker(Batch) 로그 (구조화 JSON)

- **WORKER_CANCELLED_BY_VIDEO_DELETE**  
  영상이 삭제되었거나 취소된 상태라 워커가 job_complete 없이 정상 종료했을 때  
  - `event`, `job_id`, `tenant_id`, `video_id`, `aws_batch_job_id`, `reason` 포함

CloudWatch Logs 등에서 `"event":"WORKER_CANCELLED_BY_VIDEO_DELETE"`로 검색하면 된다.

## 재현 방법

1. **Terminate 호출 재현**  
   - 영상 업로드 후 인코딩이 **진행 중**(RUNNING) 또는 **대기 중**(QUEUED/RETRY_WAIT)일 때  
   - 해당 영상 삭제 API 호출 (DELETE /api/.../videos/{id}/ 등)  
   - 위 API/이벤트 로그에서 `VIDEO_DELETE_TERMINATE_REQUESTED` 및 (성공 시) `VIDEO_DELETE_TERMINATE_OK` 확인

2. **SUCCEEDED Job은 Terminate 안 함**  
   - 이미 인코딩이 완료(SUCCEEDED)된 영상을 삭제  
   - `VIDEO_DELETE_TERMINATE_REQUESTED` 이벤트가 생성되지 않아야 함

3. **Worker 방어 동작**  
   - 인코딩 진행 중에 같은 영상을 삭제  
   - Batch 워커 로그에서 `WORKER_CANCELLED_BY_VIDEO_DELETE` 또는 AWS Terminate로 인한 종료 확인

## 단위 테스트

```bash
cd C:\academy
python manage.py test apps.support.video.tests.test_video_delete_terminate --settings=apps.api.config.settings.base
```

- RUNNING/QUEUED + `aws_batch_job_id` 있으면 `terminate_batch_job` 호출되는지  
- SUCCEEDED면 호출되지 않는지  
- Terminate 예외가 나도 삭제는 성공하는지  
를 검증한다.
