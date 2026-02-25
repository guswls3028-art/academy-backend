# 인프라 원테이크 검증 스크립트 (기록)

프로덕션 무결성 검증용으로 사용하는 **비디오 원테이크**와 **워커 3종 체크** 스크립트를 문서에 기록한다.

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

## 2. 워커 3종 체크 (Full Worker Infra Audit)

**목적:** AI Worker(ASG), Messaging Worker(ASG), Video Worker(Batch) **3종 워커 전체**를 한 번에 프로덕션 무결성 검증한다.

| 항목 | 내용 |
|------|------|
| **스크립트** | `scripts/infra/infra_one_take_full_audit.ps1` |
| **대상** | AI Worker (ASG), Messaging Worker (ASG), Video Worker (Batch) |
| **실행 예** | `.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2` |
| **옵션** | `-Verbose` 상세 로그, `-FixMode` 실패 시 수정 안내(자동 수정 없음) |
| **종료 코드** | 0 = PASS, 1 = FAIL |

### 검사 항목 요약

| 구분 | AI Worker (ASG) | Messaging Worker (ASG) | Video Worker (Batch) |
|------|------------------|------------------------|----------------------|
| **SSM** | `/academy/workers/env` 존재 | 동일 | JSON 유효성, 필수키, DJANGO_SETTINGS_MODULE, API_BASE_URL 퍼블릭 경고 |
| **Network** | Launch Template, SG, VPC/Subnet | 동일 | CE ENABLED/VALID, Queue ENABLED, JobDefs ACTIVE, SG |
| **Runtime** | SSM send-command 로 `curl API_BASE_URL/health` | 동일 | `run_netprobe_job.ps1` 실행 후 SUCCEEDED |
| **Image** | ECR `academy-ai-worker-cpu:latest` digest | ECR `academy-messaging-worker:latest` | ECR `academy-video-worker` + Job Def 이미지 |
| **ASG** | Desired/Min/Max, Unhealthy 없음, scaling 활동 실패 없음 | 동일 | — |
| **Batch** | — | — | CE/Queue/JobDef 상태 |
| **CloudWatch** | (Video 알람만 검사) | (동일) | 5개 알람 존재 여부 |

### 최종 출력 형식

```
===== FULL WORKER INFRA AUDIT =====

AI Worker:
  SSM: OK / FAIL
  Network: OK / FAIL
  Runtime: OK / FAIL
  ASG: OK / FAIL
  Image: OK / FAIL

Messaging Worker:
  (동일)

Video Worker:
  SSM: OK / FAIL
  Network: OK / FAIL
  Runtime: OK / FAIL
  Batch: OK / FAIL
  Image: OK / FAIL

OVERALL STATUS: PASS / FAIL
```

실패 시: **워커 | 영역(Area) | 리소스 | 메시지** 형태로 구체적 실패 내역 출력.

### 설정

- **계정**: `aws sts get-caller-identity` 로 자동 감지 (placeholder 사용 없음).
- **Queue/CE 이름**: `docs/deploy/actual_state/batch_final_state.json` 의 `FinalJobQueueName`, `FinalComputeEnvName` 이 있으면 사용.

### 필요 권한 (요약)

- sts:GetCallerIdentity  
- ssm:GetParameter, SendCommand, GetCommandInvocation  
- autoscaling:DescribeAutoScalingGroups, DescribeScalingActivities  
- ec2:DescribeLaunchTemplates, DescribeLaunchTemplateVersions, DescribeInstances, DescribeSecurityGroups, DescribeSubnets, DescribeVpcs  
- batch:DescribeComputeEnvironments, DescribeJobQueues, DescribeJobDefinitions, SubmitJob, ListJobs, DescribeJobs  
- ecr:DescribeRepositories, DescribeImages  
- cloudwatch:DescribeAlarms  
- logs:GetLogEvents, DescribeLogStreams  
- iam:PassRole (Batch netprobe 제출 시)

### 참고 문서

- `docs/infra_audit_runtime_ssm_failure_report.md` — AI/Messaging Runtime FAIL 시 원인(SSM Send Command) 및 조치

---

## 3. 비교

| 구분 | 비디오 원테이크 | 워커 3종 체크 |
|------|------------------|----------------|
| **스크립트** | production_done_check.ps1 | infra_one_take_full_audit.ps1 |
| **범위** | Video(Batch) 만 | AI(ASG) + Messaging(ASG) + Video(Batch) |
| **용도** | Video 배포 후 “원테이크” 검증 | 전체 워커 인프라 무결성 검증 |

둘 다 **Video Worker** 에 대해서는 netprobe job 제출·SUCCEEDED 확인을 수행한다.  
전체 3종 워커를 한 번에 검증할 때는 **워커 3종 체크**만 실행하면 된다.
