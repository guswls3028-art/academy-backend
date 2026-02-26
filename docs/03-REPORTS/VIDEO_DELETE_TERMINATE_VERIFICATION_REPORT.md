# Video Delete → AWS Batch TerminateJob 검증 보고서

**범위:** Video DELETE API → `terminate_batch_job()` 호출 → IAM·레이스·멱등성만 분석.  
**결론:** 현재 구현은 **IAM 누락으로 BROKEN**이며, **레이스 조건 1건**으로 DB 정합성 문제 가능. Terminate 호출 자체는 조건 만족 시 항상 실행되고 예외는 전파되지 않음.

---

## 1. `terminate_batch_job()` 실행 보장 여부

### 1.1 호출 경로 (Video Delete 전용)

| 단계 | 파일 | 라인 | 내용 |
|------|------|------|------|
| DELETE 진입점 | `apps/support/video/views/video_views.py` | 161–208 | `perform_destroy(instance)` |
| Job 조회·조건 분기 | 동일 | 174–193 | `video.current_job_id` 존재 시 `VideoTranscodeJob.objects.filter(pk=...).first()`, `cur.state in (QUEUED, RUNNING, RETRY_WAIT)`, `aws_batch_job_id` 비어 있지 않을 때만 `terminate_batch_job(...)` 호출 |
| Terminate 래퍼 | `apps/support/video/services/batch_control.py` | 16–70 | `terminate_batch_job(aws_batch_job_id, reason, ...)` → 내부에서 `Exception` 전부 catch, **호출부로 재발생 없음** |

### 1.2 코드 스니펫 (호출부)

```179:194:apps/support/video/views/video_views.py
                cur = VideoTranscodeJob.objects.filter(pk=video.current_job_id).first()
                if cur and cur.state in (
                    VideoTranscodeJob.State.QUEUED,
                    VideoTranscodeJob.State.RUNNING,
                    VideoTranscodeJob.State.RETRY_WAIT,
                ):
                    aws_batch_job_id = (getattr(cur, "aws_batch_job_id", None) or "").strip()
                    if aws_batch_job_id:
                        terminate_batch_job(
                            aws_batch_job_id,
                            "video_deleted",
                            video_id=video_id,
                            job_id=str(cur.id),
                        )
                    cur.state = VideoTranscodeJob.State.DEAD
                    cur.save(update_fields=["state", "updated_at"])
```

### 1.3 결론: “실행 보장” 의미 정리

- **조건이 만족되면** (진행 중 Job + `aws_batch_job_id` 있음) `terminate_batch_job()` **호출은 한 번 반드시 발생**한다.
- `batch_control.terminate_batch_job()` 내부는 **모든 Exception을 잡고 로그/이벤트만 남기고 호출부에는 예외를 전파하지 않음** → `perform_destroy` 쪽에서 Terminate 때문에 실패해도 삭제 요청은 계속 진행된다.
- **AWS API 성공은 보장되지 않음** (권한 부족·네트워크 등으로 실패 가능). 실패 시 `VIDEO_DELETE_TERMINATE_FAILED` 로그 및 이벤트만 남음.

**정리:** “terminate_batch_job()가 조건 만족 시 호출되는가?” → **예. 호출은 보장되며, 성공 여부는 best-effort.**

---

## 2. boto3 클라이언트 및 IAM 자격 증명

### 2.1 클라이언트 생성 위치

| 파일 | 라인 | 코드 |
|------|------|------|
| `apps/support/video/services/batch_control.py` | 49–53 | `region = _batch_region()` → `boto3.client("batch", region_name=region)` |
| `_batch_region()` | 73–80 | `settings.AWS_REGION` or `settings.AWS_DEFAULT_REGION` or `"ap-northeast-2"` |

```49:54:apps/support/video/services/batch_control.py
    region = _batch_region()
    try:
        import boto3
        client = boto3.client("batch", region_name=region)
        client.terminate_job(jobId=aws_batch_job_id, reason=(reason or "video_deleted")[:256])
```

- **자격 증명:** `boto3.client(...)`에 `aws_access_key_id`/`aws_secret_access_key` 등 미지정 → **기본 credential chain** 사용 (환경 변수 → EC2 Instance Metadata).
- **API 서버(EC2):** Instance Profile `academy-ec2-role` 사용. 코드상 올바름.
- **리전:** `_batch_region()`과 `batch_submit.py`의 `getattr(settings, "AWS_REGION", None) or ...` 동일 패턴. 일관됨.

**결론:** boto3는 **의도한 대로 기본 자격 증명(EC2 역할)** 을 사용하며, Video Delete 흐름에서 별도 STS assume 또는 커스텀 세션은 없음.

---

## 3. IAM 권한 — 누락 및 필요 권한

### 3.1 Video Delete에서 사용하는 API

- **batch:TerminateJob** — `batch_control.terminate_batch_job()` → `client.terminate_job(jobId=..., reason=...)`
- Video Delete 경로에서는 **DescribeJobs 호출 없음** (Job 정보는 DB `VideoTranscodeJob` 기준).

### 3.2 API 서버 역할 (academy-ec2-role)

- **정책 파일:** `infra/worker_asg/iam_policy_api_batch_submit.json` (및 `.min.json`)
- **현재 내용:**

```1:2:infra/worker_asg/iam_policy_api_batch_submit.json
{"Version":"2012-10-17","Statement":[{"Sid":"BatchSubmitVideoJob","Effect":"Allow","Action":"batch:SubmitJob","Resource":["arn:aws:batch:ap-northeast-2:809466760795:job-definition/academy-video-batch-jobdef","arn:aws:batch:ap-northeast-2:809466760795:job-queue/academy-video-batch-queue"]}]}
```

- **포함:** `batch:SubmitJob` 만 있음.
- **누락:** **`batch:TerminateJob` 없음** → 영상 삭제 시 TerminateJob 호출 시 **AccessDenied 가능 (실제로 BROKEN)**.

**Video Delete에 필요한 최소 권한:**  
- `batch:TerminateJob` (Resource는 동일 계정·리전의 job 식별자이므로, 정책상으로는 `"*"` 또는 해당 job queue/definition과 호환되는 범위 필요. AWS 문서상 TerminateJob은 job ID 기준이므로 `"Resource":"*"` 또는 job ARN 패턴.)

### 3.3 Reconcile 역할 (academy-video-batch-job-role) — 참고

- Video Delete API는 Reconcile 역할을 쓰지 않음. Reconcile은 orphan/duplicate terminate 시 `batch_submit.terminate_batch_job` 또는 직접 `batch_client.terminate_job` 호출.
- **정책:** `scripts/infra/iam/policy_video_job_role.json` — SSM, ECR, logs, CloudWatch만 있음. Batch 권한 없음.
- **AcademyAllowBatchDescribeJobs:** `scripts/infra/iam_attach_batch_describe_jobs.ps1` 에서 생성/부착. 내용은 `batch:DescribeJobs`, `batch:ListJobs` 만 포함. **`batch:TerminateJob` 없음** → Reconcile의 terminate는 별도 이슈.

**Video Delete 전용으로 필요한 것:**  
- **API 역할:** `batch:TerminateJob` 추가.  
- DescribeJobs는 **Video Delete 경로에서는 불필요** (Reconcile/다른 도구용).

---

## 4. 레이스 조건

### 4.1 Job이 삭제 요청 중에 SUCCEEDED로 바뀌는 경우

- **시나리오:** API가 `cur`를 RUNNING으로 읽음 → Worker가 `job_complete()`로 `VideoTranscodeJob`을 SUCCEEDED로 commit → API가 그 다음에 `cur.state = DEAD`, `cur.save(update_fields=["state", "updated_at"])` 실행.
- **원인:** `perform_destroy`에서 Job 행을 **`select_for_update()` 없이** 읽고, 이후 **같은 in-memory 객체**로 `state=DEAD` 후 `save()` 함. 따라서 Worker가 이미 SUCCEEDED로 갱신해도, API의 save()가 **SUCCEEDED를 DEAD으로 덮어씀**.
- **위치:** `apps/support/video/views/video_views.py` 179–194 (조회 및 저장).

**결과:**  
- DB 정합성: **BROKEN** — 실제로는 성공 완료한 Job이 DEAD으로 기록될 수 있음.  
- 비즈니스 영향: 해당 Video는 어차피 삭제되므로 READY 상태가 사라지지만, 감사/통계 등에서 “성공한 Job이 DEAD”로 남는 문제.

### 4.2 Worker가 삭제 요청 중에 완료하는 경우

- 위와 동일한 레이스: Worker가 `job_complete()` 트랜잭션 commit → API가 이미 읽어둔 `cur`로 DEAD 저장 → SUCCEEDED 덮어쓰기.
- Worker 쪽은 `job_complete()` 내부에서 `select_for_update()` 사용 (`academy/adapters/db/django/repositories_video.py` 684). API 쪽은 비관리 락 없음.

### 4.3 DB commit 타이밍

- `perform_destroy` 전체는 **`transaction.atomic()`으로 감싸져 있지 않음**.
- 순서: `terminate_batch_job()` 호출 → `cur.save()` (DEAD commit) → `super().perform_destroy(instance)` (Video 삭제) → `enqueue_delete_r2(...)`.
- **중간에 크래시 시:**
  - terminate 호출 후, DEAD save 전 크래시: Batch Job은 terminating, DB는 여전히 RUNNING 등. Worker가 SIGTERM 받으면 `job_fail_retry`로 RETRY_WAIT 등으로 바꿀 수 있음. 삭제는 사용자 재시도 필요.
  - DEAD save 후, `super().perform_destroy(instance)` 전 크래시: Job만 DEAD, Video는 남음. 사용자 재시도 시 `current_job_id`로 같은 Job을 다시 보지만, 이미 DEAD이므로 `cur.state in (QUEUED, RUNNING, RETRY_WAIT)`에 안 걸려 terminate는 호출되지 않고, `cur.state = DEAD`, `cur.save()`만 다시 실행될 수 있음 (멱등). Video는 두 번째 요청에서 삭제될 수 있음.

**결론:** DB commit 타이밍 자체로 인한 “삭제 실패”보다는, **SUCCEEDED → DEAD 덮어쓰기**가 유일한 명확한 레이스 버그.

---

## 5. 삭제 멱등성

- **DELETE /videos/{id}/**  
  - 첫 삭제: 204 등 성공.  
  - 동일 ID 재요청: Video 없음 → 404.  
  - **멱등:** 두 번째 요청은 “이미 삭제됨”으로 처리됨.
- **AWS Batch TerminateJob**  
  - 이미 종료된(SUCCEEDED/FAILED/등) job에 대해 호출해도 AWS는 일반적으로 no-op/성공 반환.  
  - **멱등:** TerminateJob 호출 자체는 멱등하게 설계해도 됨.
- **DEAD 저장**  
  - 이미 DEAD인 Job에 대해 `cur.state = DEAD; cur.save()` 다시 해도 결과는 동일.  
  - **멱등:** 문제 없음.

**단,** 위 4.1/4.2 레이스가 있으면 “한 번의 삭제 요청” 내에서 **의도치 않게 SUCCEEDED를 DEAD으로 바꾸는** 비멱등한 부작용이 발생함. “삭제 API를 여러 번 호출”하는 멱등성은 만족.

---

## 6. 요약: SAFE vs BROKEN

| 항목 | 상태 | 비고 |
|------|------|------|
| terminate_batch_job() 조건 만족 시 호출 | **SAFE** | 호출은 항상 되고, 예외는 호출부에 전파되지 않음 |
| boto3 자격 증명 | **SAFE** | 기본 chain, EC2 Instance Profile 사용 |
| API 역할에 batch:TerminateJob | **BROKEN** | 정책에 TerminateJob 없음 → AccessDenied 가능 |
| API 역할에 batch:DescribeJobs | 불필요 | Video Delete 경로에서는 미사용 |
| Job SUCCEEDED vs 삭제 시 DEAD 덮어쓰기 | **BROKEN** | 비관리 락 없이 save() → 레이스로 SUCCEEDED 덮어씀 |
| DB commit 타이밍 | **SAFE** | 중간 크래시 시 재시도로 복구 가능 |
| 삭제 API 멱등성 | **SAFE** | 재호출 시 404, TerminateJob/DEAD 저장 모두 멱등 |

**종합:** **현재 구현은 BROKEN.**  
- **IAM:** API 역할에 `batch:TerminateJob` 없음.  
- **레이스:** 삭제 중 Worker가 완료하면 SUCCEEDED가 DEAD으로 덮어써짐.

---

## 7. 필수 수정 사항

1. **API 역할에 batch:TerminateJob 추가**  
   - 파일: `infra/worker_asg/iam_policy_api_batch_submit.json` (및 `.min.json`).  
   - Action에 `"batch:TerminateJob"` 추가. Resource는 기존과 동일하게 job-definition, job-queue ARN 유지하거나, AWS 문서에 맞게 job ARN 범위 지정.  
   - 적용: `scripts/apply_api_batch_submit_policy.ps1` 재실행.

2. **삭제 시 Job DEAD 저장 레이스 제거**  
   - 파일: `apps/support/video/views/video_views.py` (perform_destroy 내부).  
   - “RUNNING 등이면 terminate 후 DEAD으로 저장”할 때, **in-memory `cur`로 save하지 말고**,  
     `VideoTranscodeJob.objects.filter(pk=cur.id, state__in=(QUEUED, RUNNING, RETRY_WAIT)).update(state=VideoTranscodeJob.State.DEAD, updated_at=timezone.now())`  
     처럼 **조건부 update**로만 DEAD 반영.  
   - `update()` 결과가 1이면 “우리가 DEAD으로 바꿨음” 로그, 0이면 “이미 SUCCEEDED 등으로 바뀜” 로그만 남기고 Video 삭제는 그대로 진행.

---

## 8. 누락된 재시도·백오프·에러 처리

- **batch_control.terminate_batch_job()**  
  - 현재: 1회 호출, 실패 시 로그 + `VIDEO_DELETE_TERMINATE_FAILED` 이벤트만. **재시도·지수 백오프 없음.**  
  - 권장: 일시적 오류(Throttling, 5xx, 네트워크)에 대해 **제한된 재시도(예: 2~3회) + 짧은 지수 백오프** 추가 시, 삭제 시 Terminate 성공 확률 상승. 필수는 아니나 권장.

- **AWS 에러 구분**  
  - 현재: `except Exception as e`로 일괄 처리.  
  - 권장: `botocore.exceptions.ClientError`에서 `ErrorCode == "AccessDenied"` 등 구분해 로그/메트릭에 남기면, IAM 문제 조사에 유리.  
  - **에러 처리 자체는 “호출부로 전파하지 않음”으로 충분**하며, Terminate 실패해도 삭제는 성공하는 설계는 유지 가능.

---

## 9. 참고 — 관련 파일 경로

| 용도 | 경로 |
|------|------|
| Video DELETE 진입점 | `apps/support/video/views/video_views.py` (perform_destroy 161–208) |
| Terminate 래퍼 (Video Delete용) | `apps/support/video/services/batch_control.py` (terminate_batch_job, _batch_region) |
| API Batch 정책 (Submit만 있음) | `infra/worker_asg/iam_policy_api_batch_submit.json` |
| 정책 적용 스크립트 | `scripts/apply_api_batch_submit_policy.ps1` |
| Job 완료 (Worker) | `academy/adapters/db/django/repositories_video.py` (job_complete 676–730, job_set_running 632–650) |
| Worker 메인 (SIGTERM/삭제 체크) | `apps/worker/video_worker/batch_main.py` (_video_still_exists, _handle_term) |
| Reconcile Batch Describe/terminate | `apps/support/video/management/commands/reconcile_batch_video_jobs.py` |
| DescribeJobs 정책 부착 | `scripts/infra/iam_attach_batch_describe_jobs.ps1` (AcademyAllowBatchDescribeJobs — DescribeJobs/ListJobs만) |

---

**작성 기준:** 위 경로 및 스니펫 기준 코드베이스 분석. 배포된 IAM이 리포와 다를 수 있으므로, 프로덕션에서는 API/Reconcile 역할에 실제로 `batch:TerminateJob`이 부여되었는지 콘솔/CLI로 확인 권장.
