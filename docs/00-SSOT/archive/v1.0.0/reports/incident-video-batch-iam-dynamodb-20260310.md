# 인시던트 보고서: Video Batch DynamoDB 권한 누락 (2026-03-10)

## 요약

`academy-video-batch-job-role`에 DynamoDB 권한이 없어 video batch job 완료 후 lock_release 실패 → stale lock 누적 → 후속 upload_complete 503 발생.

## 증상

- `POST /api/v1/media/videos/{id}/upload/complete/` → 503 Service Unavailable
- 영상 207, 208, 209 (최초 증상), 이후 210~214

## 근본 원인

`academy-video-batch-job-role`에 DynamoDB 권한 없음. 배치 워커가 job 완료 후 `lock_release()` (dynamodb:DeleteItem) 호출 시 AccessDeniedException 발생 → stale lock 남음 → 다음 `upload_complete` 호출 시 `lock_acquire` ConditionalCheckFailed (PK 이미 존재) → `create_job_and_submit_batch` returns None → API 503.

## CloudWatch 에러 로그 (확인됨)

```
VIDEO_JOB_LOCK_RELEASE_ERROR | video_id=211
  error=An error occurred (AccessDeniedException) when calling the DeleteItem operation:
  User: arn:aws:sts::809466760795:assumed-role/academy-video-batch-job-role/...
  is not authorized to perform: dynamodb:DeleteItem on resource:
  arn:aws:dynamodb:ap-northeast-2:809466760795:table/academy-v1-video-job-lock
```

## 조치

### 1. IAM 정책 즉시 적용 (live fix)

```bash
aws iam put-role-policy \
  --role-name academy-video-batch-job-role \
  --policy-name academy-video-batch-job-inline \
  --policy-document file://scripts/v1/templates/iam/policy_video_job_role.json
```

추가된 Statement (`DynamoDBVideoJobLock`):
- Action: PutItem, DeleteItem, GetItem, UpdateItem, ConditionCheckItem
- Resource: academy-v1-video-job-lock, academy-v1-video-upload-checkpoints

또한 `BatchReconcile` statement에 long queue/jobdef 리소스 추가:
- academy-v1-video-batch-long-jobdef
- academy-v1-video-batch-long-queue

### 2. SSM Workers env 수정 (v89)

기존 잘못된 값:
- `VIDEO_BATCH_JOB_QUEUE=academy-video-batch-queue` (v1- prefix 누락)
- `API_BASE_URL=http://172.30.3.142:8000` (종료된 EC2 IP 하드코딩)

수정된 값:
- `VIDEO_BATCH_JOB_QUEUE=academy-v1-video-batch-queue`
- `VIDEO_BATCH_LONG_JOB_QUEUE=academy-v1-video-batch-long-queue`
- `API_BASE_URL=http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com`

### 3. IAM 템플릿 파일 업데이트 및 커밋

`scripts/v1/templates/iam/policy_video_job_role.json` 업데이트 후 커밋 (`83a28ef8`).
`Ensure-BatchIAM` (iam.ps1)이 이 파일을 기준으로 inline policy를 자동 적용하므로 재배포 시 동일 문제 재발하지 않음.

### 4. Stale DynamoDB lock 수동 삭제

영상 211, 212, 213 — 배치 job은 완료되었으나 lock 해제 실패. 수동 삭제 완료.
영상 210, 214 — 배치 job 여전히 RUNNING (진행 중). lock은 job 완료 후 자동 해제 예정.

## 영향받은 영상

| video_id | 상태 | 처리 |
|----------|------|------|
| 207, 208, 209 | 503 (초기 증상) | — |
| 210 | 재시도 후 batch job RUNNING | lock 자동 해제 예정 |
| 211 | batch SUCCEEDED, lock stale | lock 수동 삭제 ✓ |
| 212 | batch SUCCEEDED, lock stale | lock 수동 삭제 ✓ |
| 213 | batch SUCCEEDED, lock stale | lock 수동 삭제 ✓ |
| 214 | 재시도 후 batch job RUNNING | lock 자동 해제 예정 |

## 재발 방지

1. `policy_video_job_role.json` 템플릿에 DynamoDB 권한 명시 → 재배포 시 `Ensure-BatchIAM`이 자동 적용
2. `INFRA-AND-SPECS.md` 7절에 IAM 역할별 필수 권한 문서화 (DynamoDB 중요성 명시)
3. `SSOT.md` canonical resources에 `academy-v1-video-upload-checkpoints` DynamoDB 추가
4. SSM workers env는 인스턴스 IP 하드코딩 금지; ALB DNS 사용

## 관련 파일

- `scripts/v1/templates/iam/policy_video_job_role.json` (수정됨)
- `scripts/v1/resources/iam.ps1` (Ensure-BatchIAM 함수)
- `apps/support/video/services/video_job_lock.py`
- `apps/support/video/services/video_encoding.py` (create_job_and_submit_batch)
- `apps/support/video/views/video_views.py` (upload_complete)
