# 인프라 원테이크 검증 스크립트 (기록)

프로덕션 무결성 검증용으로 사용하는 **비디오 원테이크**와 **원테이크 전체 감사** 스크립트를 문서에 기록한다.

---

## 1. 비디오 원테이크 (Video One-Take)

**목적:** Video Worker(AWS Batch) 전용 프로덕션 준비 여부를 한 번에 검증한다.

| 항목 | 내용 |
|------|------|
| **스크립트** | `scripts/infra/production_done_check.ps1` |
| **대상** | Video Worker (Batch) 만 — CE, Queue, JobDefs, EventBridge, SSM, netprobe |
| **실행 예** | `.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2` |
| **종료 코드** | 0 = PASS, 1 = FAIL |

### 검사 항목 요약

- **Batch CE**: 존재, state=ENABLED, status=VALID, (선택) API VPC 일치
- **Job Queue**: 존재, state=ENABLED
- **Job Definitions**: academy-video-batch-jobdef, academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe 가 ACTIVE
- **EventBridge**: reconcile/scan-stuck 규칙 및 Batch 타깃 연동 확인 (`verify_eventbridge_wiring.ps1`)
- **SSM**: `/academy/workers/env` 존재 및 JSON/필수키/DJANGO_SETTINGS_MODULE 검증 (`verify_ssm_env_shape.ps1`)
- **API_BASE_URL**: VPC 모드일 때 Private IP 사용 여부
- **Log groups**: `/aws/batch/academy-video-worker`, `/aws/batch/academy-video-ops` 존재
- **CloudWatch 알람**: Video Batch 알람 5개 존재 여부 (없으면 WARN)
- **Netprobe**: netprobe job 제출 후 SUCCEEDED(exitCode=0) 여부

### 설정

- `docs/deploy/actual_state/batch_final_state.json` 에 `FinalJobQueueName` 이 있으면 해당 큐 이름 사용.
- `docs/deploy/actual_state/api_instance.json` 에서 API VpcId 를 읽어 CE가 같은 VPC인지 검사할 수 있음.

### 참고 문서

- `docs/video_batch_production_runbook.md` — 배포·검증 순서
- `docs/deploy/PRODUCTION_ONE_TAKE_FINAL.md` — 원테이크 최종 체크리스트

---

## 2. 원테이크 전체 감사 (Video/Ops Batch + EventBridge + IAM)

**목적:** Video Worker(Batch), Ops Queue/CE 분리, EventBridge(reconcile/scan-stuck), IAM(DescribeJobs), JobDefinition을 한 번에 검증한다.

| 항목 | 내용 |
|------|------|
| **스크립트** | `scripts/infra/infra_one_take_full_audit.ps1` |
| **대상** | Video CE/Queue, Ops CE/Queue, EventBridge 규칙·타깃, IAM(academy-video-batch-job-role 등), JobDef |
| **실행 예** | `.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2` |
| **Region** | 생략 시 `aws configure get region` 사용 |
| **옵션** | `-Verbose` 상세 로그, `-FixMode` 실패 항목 자동 수정(Ops CE/Queue 생성, IAM 부착, EventBridge 정렬), `-FixModeWithCleanup` reconcile RUNNING 1개 초과 시 나머지 terminate |
| **출력** | Category \| Check \| Expected \| Actual \| Status(PASS/WARN/FAIL) \| FixAction 테이블, Summary(PASS/WARN/FAIL count), Result: PASS/NEEDS_ACTION/FAIL |
| **종료 코드** | 0 = PASS 또는 NEEDS_ACTION, 1 = FAIL |

### 검사 항목 요약

- **Batch:** Video CE/Ops CE 존재·상태(VALID/ENABLED)·instanceTypes·min/max, Video/Ops Queue 상태, Video/Ops Queue job 수, reconcile RUNNING 1개 이하 여부
- **EventBridge:** reconcile/scan-stuck 규칙 존재, schedule rate(5 minutes), target queue=OpsQueue, jobDefinition
- **IAM:** reconcile job definition의 jobRoleArn 역할에 AcademyAllowBatchDescribeJobs(batch:DescribeJobs, batch:ListJobs) 부착 여부
- **JobDef:** academy-video-batch-jobdef vcpus/memory, academy-video-ops-reconcile / academy-video-ops-scanstuck command·jobRoleArn

### 참고 문서

- `docs/video_batch_production_runbook.md` — 배포·검증 순서, 원테이크 운영 점검(3b)
- `docs/RECONCILE_STABILIZATION_DEPLOY.md` — Reconcile 안정화 변경 요약·배포/롤백 순서
- `docs/video/RECONCILE_STABILIZATION_VERIFICATION_COMMANDS.md` — 운영 검증 커맨드

---

## 3. 비교

| 구분 | 비디오 원테이크 | 원테이크 전체 감사 |
|------|------------------|----------------|
| **스크립트** | production_done_check.ps1 | infra_one_take_full_audit.ps1 |
| **범위** | Video(Batch) 만 | AI(ASG) + Messaging(ASG) + Video(Batch) |
| **용도** | Video 배포 후 “원테이크” 검증 | 전체 워커 인프라 무결성 검증 |

둘 다 **Video Worker** 에 대해서는 netprobe job 제출·SUCCEEDED 확인을 수행한다.  
전체 3종 워커를 한 번에 검증할 때는 **워커 3종 체크**만 실행하면 된다.
