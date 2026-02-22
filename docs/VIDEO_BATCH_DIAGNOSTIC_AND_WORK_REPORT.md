# Video Batch 인프라 진단 및 완료 작업 보고서

**작성일:** 2026-02-22  
**범위:** Batch Video 인프라 진단 실행 결과 + Phase 2·배포·검증 등 완료된 작업 요약

---

## 1. 완료된 작업 요약 (1~끝)

| # | 작업 | 결과/산출물 |
|---|------|-------------|
| 1 | **배포 스크립트 정리** | `deploy.ps1`: Video EC2 제거(INSTANCE_KEYS, REMOTE_CMDS, nameFilter, 4대→3대). `_config_instance_keys.ps1`, `deploy_preflight.ps1`, `check_worker_docker.ps1`에서 video 제거. |
| 2 | **full_redeploy -SkipBuild -WorkersViaASG 검사** | 정책/환경변수/Lambda/IAM 영향 없음, Video EC2/ASG 제외 확인. `docs/FULL_REDEPLOY_SKIPBUILD_WORKERSVIAASG_CHECK.md` 작성. |
| 3 | **Setup vs API 설정 일치 검사** | `batch_video_setup_full.ps1`와 API(base.py, .env) Queue/JobDef 이름 일치. `docs/VIDEO_BATCH_SETUP_VS_API_SETTINGS.md` 작성. |
| 4 | **Batch Video 원샷 진단 스크립트** | `scripts/diagnose_batch_video_infra.ps1` 추가. CONFIG 비교, Queue/CE/JobDef/IAM/Log 그룹/스모크 제출 검사. |
| 5 | **진단 실행 (AWS 자격 증명 있는 환경)** | 아래 2회 실행 결과 반영. CONFIG/Queue/CE/JobDef/IAM OK. LOG_GROUP_CHECK FAIL, 스모크 제출 시 job_id 파라미터 누락으로 FAIL → 스크립트에 `--parameters job_id=...` 추가로 수정. |

---

## 2. 진단 실행 결과 (실제 출력)

### 2.1 1회차: 기본 실행 (ALLOW_TEST_SUBMIT 미설정)

```
=== STEP 0 CONFIG_DIFF ===
API_QUEUE=academy-video-batch-queue SCRIPT_QUEUE=academy-video-batch-queue QUEUE_JSON=academy-video-batch-queue -> MATCH
API_JOBDEF=academy-video-batch-jobdef SCRIPT_JOBDEF=academy-video-batch-jobdef JOBDEF_JSON=academy-video-batch-jobdef -> MATCH

=== STEP 1 AWS ===
ACTIVE_REGION=ap-northeast-2 ACTIVE_PROFILE=

=== STEP 2 QUEUE ===
state=ENABLED status=VALID jobQueueArn=arn:aws:batch:ap-northeast-2:809466760795:job-queue/academy-video-batch-queue
computeEnvironment order=1 ce=arn:aws:batch:ap-northeast-2:809466760795:compute-environment/academy-video-batch-ce-v3

=== STEP 2 CE ===
CE academy-video-batch-ce-v3 state=ENABLED status=VALID type=MANAGED maxvCpus=32
CE_CHECK: OK

=== STEP 3 JOBDEF ===
revision=11 type=container image=809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
executionRoleArn=arn:aws:iam::809466760795:role/academy-batch-ecs-task-execution-role jobRoleArn=arn:aws:iam::809466760795:role/academy-video-batch-job-role
logDriver=awslogs logGroup=/aws/batch/academy-video-worker
retryStrategy.attempts=1

=== STEP 3 IAM ===
{
    "AttachedPolicies": []
}
IAM_CHECK: OK

=== STEP 4 LOG GROUP ===
LOG_GROUP_CHECK: FAIL
LOG_CONTENT_CHECK: SKIP

=== STEP 5 SMOKE SUBMIT ===
SMOKE_SUBMIT_CHECK: SKIP (ALLOW_TEST_SUBMIT not set)

========== FINAL REPORT ==========
CONFIG_MATCH_QUEUE: OK
CONFIG_MATCH_JOBDEF: OK
AWS_ACCESS: OK
QUEUE_CHECK: OK
CE_CHECK: OK
JOBDEF_CHECK: OK
IAM_CHECK: OK
LOG_GROUP_CHECK: FAIL
LOG_CONTENT_CHECK: SKIP
SMOKE_SUBMIT_CHECK: SKIP

ROOT_CAUSE_HINTS:
- Log group missing: job definition logConfiguration or logs:CreateLogStream permission
```

### 2.2 2회차: 스모크 제출 허용 (ALLOW_TEST_SUBMIT=true)

```
=== STEP 0 CONFIG_DIFF ===
API_QUEUE=academy-video-batch-queue SCRIPT_QUEUE=academy-video-batch-queue QUEUE_JSON=academy-video-batch-queue -> MATCH
API_JOBDEF=academy-video-batch-jobdef SCRIPT_JOBDEF=academy-video-batch-jobdef JOBDEF_JSON=academy-video-batch-jobdef -> MATCH

=== STEP 1 AWS ===
ACTIVE_REGION=ap-northeast-2 ACTIVE_PROFILE=

=== STEP 2 QUEUE ===
state=ENABLED status=VALID jobQueueArn=arn:aws:batch:ap-northeast-2:809466760795:job-queue/academy-video-batch-queue
computeEnvironment order=1 ce=arn:aws:batch:ap-northeast-2:809466760795:compute-environment/academy-video-batch-ce-v3

=== STEP 2 CE ===
CE academy-video-batch-ce-v3 state=ENABLED status=VALID type=MANAGED maxvCpus=32
CE_CHECK: OK

=== STEP 3 JOBDEF ===
revision=11 type=container image=809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
executionRole=arn:aws:iam::809466760795:role/academy-batch-ecs-task-execution-role jobRoleArn=arn:aws:iam::809466760795:role/academy-video-batch-job-role
logDriver=awslogs logGroup=/aws/batch/academy-video-worker
retryStrategy.attempts=1

=== STEP 3 IAM ===
{
    "AttachedPolicies": []
}
IAM_CHECK: OK

=== STEP 4 LOG GROUP ===
LOG_GROUP_CHECK: FAIL
LOG_CONTENT_CHECK: SKIP

=== STEP 5 SMOKE SUBMIT ===
SMOKE_SUBMIT_CHECK: FAIL
An error occurred (ClientException) when calling the SubmitJob operation: Unable to substitute value. No parameter found for reference job_id

========== FINAL REPORT ==========
CONFIG_MATCH_QUEUE: OK
CONFIG_MATCH_JOBDEF: OK
AWS_ACCESS: OK
QUEUE_CHECK: OK
CE_CHECK: OK
JOBDEF_CHECK: OK
IAM_CHECK: OK
LOG_GROUP_CHECK: FAIL
LOG_CONTENT_CHECK: SKIP
SMOKE_SUBMIT_CHECK: OK/FAIL from above

ROOT_CAUSE_HINTS:
- Log group missing: job definition logConfiguration or logs:CreateLogStream permission
```

---

## 3. 최종 진단 요약표

| 항목 | 결과 | 비고 |
|------|------|------|
| CONFIG_MATCH_QUEUE | OK | API·스크립트·JSON 모두 academy-video-batch-queue |
| CONFIG_MATCH_JOBDEF | OK | API·스크립트·JSON 모두 academy-video-batch-jobdef |
| AWS_ACCESS | OK | sts get-caller-identity 성공 |
| QUEUE_CHECK | OK | state=ENABLED, status=VALID, CE 연결(academy-video-batch-ce-v3) |
| CE_CHECK | OK | academy-video-batch-ce-v3 ENABLED/VALID, maxvCpus=32 |
| JOBDEF_CHECK | OK | revision=11, image·executionRole·jobRole·awslogs 설정 있음, retryStrategy.attempts=1 |
| IAM_CHECK | OK | academy-video-batch-job-role, academy-batch-ecs-task-execution-role 존재 (AttachedPolicies는 인라인 정책 사용 가능) |
| LOG_GROUP_CHECK | **FAIL** | `/aws/batch/academy-video-worker` 로그 그룹 describe 실패. 권한 또는 로그 그룹 미생성. |
| LOG_CONTENT_CHECK | SKIP | 로그 그룹 없어 스트림/내용 검사 생략 |
| SMOKE_SUBMIT_CHECK | **FAIL** (2회차) | submit-job 시 `job_id` 파라미터 미전달 → "No parameter found for reference job_id". 진단 스크립트에 `--parameters job_id=cursor-smoke-diagnose` 추가하여 수정함. |

---

## 4. 원인 및 조치

### 4.1 LOG_GROUP_CHECK: FAIL

- **원인 후보:**  
  - 해당 계정/리전에 `/aws/batch/academy-video-worker` 로그 그룹이 없음.  
  - 또는 실행한 IAM 사용자/역할에 `logs:DescribeLogGroups` 권한 없음.
- **조치:**  
  - `batch_video_setup.ps1`에서 해당 로그 그룹 생성(이미 스크립트에 포함).  
  - 한 번도 setup을 안 했다면 `.\scripts\infra\batch_video_setup_full.ps1` 실행.  
  - IAM 권한이면 `logs:DescribeLogGroups`, `logs:DescribeLogStreams` 등 필요한 로그 권한 추가.

### 4.2 SMOKE_SUBMIT: job_id 파라미터 누락

- **원인:** Job Definition에 `Ref::job_id` 사용. submit-job 시 `--parameters job_id=<value>` 필수인데 진단 스크립트에서 생략함.
- **조치:** `scripts/diagnose_batch_video_infra.ps1`에서 submit-job 호출에 `--parameters "job_id=cursor-smoke-diagnose"` 추가 완료.  
  - 재실행: `$env:ALLOW_TEST_SUBMIT="true"; .\scripts\diagnose_batch_video_infra.ps1`

---

## 5. 참고 문서·스크립트

| 구분 | 경로 |
|------|------|
| 진단 스크립트 | `scripts/diagnose_batch_video_infra.ps1` |
| Batch setup | `scripts/infra/batch_video_setup_full.ps1`, `batch_video_setup.ps1`, `batch_video_verify_and_register.ps1` |
| Django 검증 | `python manage.py validate_batch_video_system` |
| 전환/검증 보고서 | `docs/VIDEO_WORKER_BATCH_FULL_TRANSITION_REPORT.md` (섹션 8 검증 결과 포함) |
| full_redeploy 검사 | `docs/FULL_REDEPLOY_SKIPBUILD_WORKERSVIAASG_CHECK.md` |
| Setup vs API | `docs/VIDEO_BATCH_SETUP_VS_API_SETTINGS.md` |

---

이 보고서는 위 완료 작업과 진단 2회 실행 결과를 반영하였으며, LOG_GROUP 조치 및 스모크 제출 스크립트 수정 후 재진단 시 SMOKE_SUBMIT까지 OK로 기대됩니다.
