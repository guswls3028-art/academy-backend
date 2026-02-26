# SSOT·멱등성·레거시 포렌식 최종 보고서

**목표:** 문서 SSOT 검증 → 원테이크 멱등 정렬 → 레거시 제거 전략 → 운영 자동화 준비 판정.  
**원칙:** 추측 금지. 확인 필요 시 확인 명령 제시.

---

# 단계 1 — SSOT 문서 검증

## 1.1 SSOT 네이밍 충돌 리포트

| 리소스 | SSOT 이름 (RESOURCE-INVENTORY) | 다른 문서/스크립트 이름 | 충돌 여부 | 조치 필요 |
|--------|--------------------------------|-------------------------|------------|------------|
| Video CE | academy-video-batch-ce-final | recreate_batch_in_api_vpc.ps1 기본값: academy-video-batch-ce | **충돌** | recreate 기본값을 ce-final로 변경 또는 호출부에서만 전달(현재 원테이크는 전달함). |
| Video CE | academy-video-batch-ce-final | batch_video_setup.ps1 기본값: academy-video-batch-ce | **충돌** | batch_video_setup 기본값을 ce-final로 변경. |
| Video CE | academy-video-batch-ce-final | infra_one_take_full_audit.ps1 ExpectedVideoCEName 기본값: academy-video-batch-ce | **충돌** | ExpectedVideoCEName 기본값을 academy-video-batch-ce-final로 변경. |
| Video CE | academy-video-batch-ce-final | VIDEO_WORKER_SCALING_SSOT.md: academy-video-batch-ce-v3 | **충돌** | 문서를 ce-final 기준으로 수정 또는 "과거 v3" 명시. |
| Video CE | academy-video-batch-ce-final | reconcile_video_batch_production.ps1: academy-video-batch-ce-v2 | **충돌** | 스크립트 기본값을 academy-video-batch-ce-final로 변경. |
| Batch Queue | academy-video-batch-queue | (동일) | 없음 | — |
| Ops CE | academy-video-ops-ce | (동일) | 없음 | — |
| Ops Queue | academy-video-ops-queue | (동일) | 없음 | — |
| EventBridge reconcile | academy-reconcile-video-jobs | (동일) | 없음 | — |
| EventBridge scan_stuck | academy-video-scan-stuck-rate | (동일) | 없음 | — |
| Job Definition (Video) | academy-video-batch-jobdef | (동일) | 없음 | — |
| IAM Role | academy-batch-ecs-instance-role 등 | RESOURCE-INVENTORY는 "등"으로만 표기 | **부분** | 인벤토리에 batch_video_setup.ps1 기준 정확한 역할명 나열 권장. |
| ASG | academy-messaging-worker-asg | (동일) | 없음 | — |
| Launch Template | (ASG에 연결된 버전) | deploy_worker_asg: academy-ai-worker-lt, academy-messaging-worker-lt | **없음** (이름 정의만 상이) | 인벤토리에 LT 이름 명시 권장. |
| SSM Parameter | /academy/workers/env | (동일) | 없음 | — |
| R2 Bucket | (외부 관리, 이름 없음) | (동일) | 없음 | — |
| CDN | (외부 관리) | (동일) | 없음 | — |

---

## 1.2 환경 파라미터 단일화 검증

- **prod/staging/dev** 값이 **SSOT-RESOURCE-INVENTORY.md** "환경별 값 요약" 및 **SSOT-ONE-TAKE-DEPLOYMENT.md** "환경 구분 및 SSOT 파라미터" 표에 정의되어 있음.
- **문서 간 상이:** staging/dev는 "확인 필요"로만 되어 있어 상이한 고정값이 여러 문서에 흩어져 있지는 않음.  
- **조치:** staging/dev 실제 값이 정해지면 RESOURCE-INVENTORY의 "환경별 값 요약" 한 곳에만 추가.

---

## 1.3 Describe → Decision → Update/Create 구체성 검증

| 리소스 | Describe CLI/API | 비교 필드 | Update 조건 | Skip 조건 | 중단 조건 | 판정 |
|--------|-------------------|-----------|-------------|-----------|-----------|------|
| Batch CE | describe-compute-environments | status, state | INVALID 시 삭제 후 재생성 | VALID && ENABLED | (문서상) 없음 | **구체적** |
| Batch Queue | describe-job-queues | state, computeEnvironmentOrder | CE ARN 불일치 시 update-job-queue | 존재 && CE 일치 | (문서상) 없음 | **구체적** |
| Job Definition | describe-job-definitions (ACTIVE) | vcpus, memory, image, retryStrategy, timeout | (문서) 변경 시에만 새 revision | (문서) 동일 스펙 시 재사용 | (문서상) 없음 | **설계만 구체적**, 코드는 아래 단계에서 검증 |
| EventBridge | describe-rule, list-targets-by-rule | ScheduleExpression, Target Arn | (문서) Target만 put-targets | Rule 존재 시 타깃만 갱신 | (문서상) 없음 | **구체적** |
| ASG | describe-auto-scaling-groups | Launch Template 버전, DesiredCapacity | (문서) LT 변경 시 새 버전 후 ASG 업데이트, Desired 유지 | 동일 LT·min/max | (문서상) 없음 | **설계만 구체적** |
| SSM | get-parameter | 필수 키 존재 | (문서) 값 변경 시에만 put | 동일 .env | (문서상) 없음 | **구체적** |

- **설계 미완성:** "어떤 상태이면 중단하는가"가 리소스별로 표로 명시되어 있지 않음. Pre-flight 실패 시 중단은 메인 문서에 있으나, Batch Job RUNNABLE 무한 대기·CE 생성 타임아웃 등은 SSOT-IDEMPOTENCY-RULES에만 타임아웃 수치로 언급됨.

---

# 단계 2 — 코드/스크립트 멱등성 정적 분석

## 멱등 분석 표

| 스크립트 | Describe 존재 | 조건부 Update | 무조건 Create 여부 | Wait loop | 락 | 위험도 |
|----------|----------------|---------------|---------------------|------------|-----|--------|
| infra_full_alignment_public_one_take.ps1 | 예 (Preflight, Batch describe) | Batch는 recreate에 위임 | 아니오 | Netprobe 폴링만; CE는 recreate 내부 | 없음 | **WARN** (락 없음) |
| recreate_batch_in_api_vpc.ps1 | 예 (API, RDS, Subnet, SG) | batch_video_setup 호출 | CleanupOld 시에만 delete queue/CE | batch_video_setup 내부 | 없음 | **WARN** |
| batch_video_setup.ps1 | 예 (CE, Queue) | CE: INVALID 시에만 삭제·재생성; Queue: CE ARN 비교 후 update | **JobDef: 매회 register** (비교 없음) | CE 삭제 후·생성 후 wait 있음 | 없음 | **FAIL** (JobDef 무조건 register) |
| batch_ops_setup.ps1 | 예 (Video CE, Ops CE, Queue) | Ops CE DISABLED면 ENABLED만; 없으면 create | Ops CE/Queue 없을 때만 create | CE VALID 대기 일부 | 없음 | **WARN** |
| eventbridge_deploy_video_scheduler.ps1 | 예 (describe-job-queues, describe-job-definitions) | put-rule + put-targets (동일 내용 반복) | Rule 없으면 put-rule으로 생성; Target 매회 put-targets | 없음 | 없음 | **PASS** (put-targets는 덮어쓰기) |
| video_worker_infra_one_take.ps1 | 예 | CE INVALID 시 §9 루틴; JobDef는 describe 후 **이미지 URI만 비교** 후 register | 이미지 다를 때만 register (동일 시 skip) | CE disable/delete/create 후 wait | 없음 | **PASS** (JobDef 조건부) |
| deploy_worker_asg.ps1 | 예 (describe-launch-templates, create-asg 실패 시) | ASG 있으면 update-auto-scaling-group | create 실패 시 update 시 **desired-capacity 1 고정** | 없음 | 없음 | **FAIL** (DesiredCapacity 0 초기화 아님 but **항상 1로 덮어씀**) |
| ssm_bootstrap_video_worker.ps1 | (get-parameter 선택) | put-parameter | 매회 .env 기준 put | 없음 | 없음 | **PASS** (동일 .env면 멱등) |
| run_netprobe_job.ps1 | describe-jobs 폴링 | — | submit 1회 | SUCCEEDED/FAILED까지 폴링 | 없음 | **PASS** |

판정 요약:
- **무조건 create:** batch_video_setup.ps1의 register-job-definition(§5) — diff 없이 매회 register → **FAIL**.
- **describe 없이 update:** 없음. 배포 스크립트는 모두 describe 또는 get 후 분기.
- **diff 비교 없이 새 revision:** batch_video_setup.ps1 — **FAIL**. video_worker_infra_one_take.ps1은 이미지 URI 비교 후 register.
- **wait loop 없음:** eventbridge_deploy_video_scheduler, deploy_worker_asg — **WARN** (해당 리소스는 즉시 반영).
- **락 없음:** 전 스크립트 — **WARN**.

---

# 단계 3 — 리소스별 멱등 구현 검증

## Batch Compute Environment

| 항목 | 코드 일치 | 판정 | 이유 |
|------|-----------|------|------|
| describe 후 status 확인 | 예 (batch_video_setup.ps1 L197, video_worker_infra_one_take L106) | **PASS** | describe-compute-environments 후 status 사용. |
| INVALID일 때만 삭제 | 예 (batch_video_setup L202, video_worker L109) | **PASS** | status -eq "INVALID" 분기에서만 삭제. |
| 삭제 전 disable & detach | 예 (Queue DISABLED, CE DISABLED 후 delete) | **PASS** | batch_video_setup L211-216, video_worker L119-123. |
| recreate 후 VALID/ENABLED wait | 예 (batch_video_setup L234-244, L218 삭제 대기) | **PASS** | wait loop 존재. 타임아웃 120초. |

## ASG

| 항목 | 코드 일치 | 판정 | 이유 |
|------|-----------|------|------|
| 현재 DesiredCapacity 유지 | **아니오** | **FAIL** | deploy_worker_asg.ps1 L161, L176: update 시 --desired-capacity 1 고정. describe로 현재값 읽어 유지하지 않음. |
| Launch Template 변경 시 새 version | 예 (create-launch-template-version 후 modify default) | **PASS** | L105-106, L134-135. |
| ASG update 시 Desired=0 초기화 금지 | 0으로 덮어쓰진 않으나 **1로 고정** | **FAIL** | 현재값 유지 아님. |

## EventBridge

| 항목 | 코드 일치 | 판정 | 이유 |
|------|-----------|------|------|
| Rule 존재 시 Target만 최신화 | 예 (put-rule + put-targets 매회 호출) | **PASS** | put-rule은 동일 내용 시 멱등, put-targets는 기존 타깃 교체. |
| Rule recreate 금지 | 예 (삭제 후 재생성 없음) | **PASS** | put-rule만 사용. |

## Job Definition

| 항목 | 코드 일치 | 판정 | 이유 |
|------|-----------|------|------|
| vCPU/Memory/Image 변경 시에만 새 revision | **batch_video_setup: 아니오** / video_worker_infra_one_take: 예 | **부분** | batch_video_setup은 describe 없이 매회 register. video_worker_infra_one_take는 최신 revision의 image 비교 후 다를 때만 register. |

---

# 단계 4 — 레거시 인프라 탐지

## 레거시 후보 리스트

| 리소스 | 현재 상태 | SSOT 포함 여부 | 제거 전략 |
|--------|-----------|----------------|-----------|
| academy-video-batch-ce-v2 | one_shot_video_ce_final.ps1에서 OldVideoCEs로 정리 대상 | **미포함** | one_shot 또는 수동으로 Queue 분리 → CE DISABLED → 삭제. |
| academy-video-batch-ce-v3 | VIDEO_WORKER_SCALING_SSOT에 "현행"으로 명시(문서 오류) | **미포함** | 문서를 ce-final로 수정. 실제 CE가 v3면 마이그레이션 후 삭제. |
| academy-video-batch-ce-public | one_shot_video_ce_final.ps1 OldVideoCEs | **미포함** | 동일. Queue 분리 → DISABLED → 삭제. |
| academy-video-batch-ce | batch_video_setup/audit 기본값; recreate 기본값 | **이름 충돌** | SSOT는 ce-final. ce만 있는 환경은 큐를 ce-final로 옮긴 뒤 ce 삭제 또는 ce를 ce-final로 통합(이름 변경 불가이므로 새 CE ce-final 생성 후 전환). |
| academy-video-batch-queue-ce | batch_video_setup이 Queue update 실패 시 생성하는 fallback 큐 | **문서에 언급** | SSOT는 academy-video-batch-queue 단일. fallback 큐는 사용 중이면 이전 큐 비활성화 후 정리. |
| 사용되지 않는 JobDefinition revision | ACTIVE revision 다수 누적 | **미정의** | 주기적으로 INACTIVE 처리 스크립트 또는 수동 정리. 문서에 정책 명시 권장. |
| academy-reconcile-video-jobs / academy-video-scan-stuck-rate | 사용 중 | **포함** | — |
| academy-video-worker-asg (레거시 Video ASG) | VIDEO_WORKER_SCALING_SSOT "LEGACY" | **미포함** | 사용 중단 후 ASG 삭제. |

확인 명령:
- CE 목록: `aws batch describe-compute-environments --region ap-northeast-2 --query "computeEnvironments[?starts_with(computeEnvironmentName, 'academy-video')].{Name:computeEnvironmentName,State:state,Status:status}" --output table`
- JobDef revision: `aws batch describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE --region ap-northeast-2 --query "jobDefinitions[*].revision" --output text`

---

# 단계 5 — 멱등 보장 판정

1) **스크립트를 3회 연속 실행하면 JobDefinition revision이 증가하는가?**  
   - **예.** batch_video_setup.ps1은 매회 register-job-definition을 호출하며, 기존 revision과의 diff 비교가 없음.  
   - **확인:** `aws batch describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE --region ap-northeast-2 --query "length(jobDefinitions)" --output text` 실행 전후 비교.

2) **Compute Environment가 중복 생성될 가능성이 있는가?**  
   - **원테이크 경로:** recreate_batch_in_api_vpc.ps1이 -ComputeEnvName academy-video-batch-ce-final을 받아 batch_video_setup에 전달하면, CE가 이미 VALID/ENABLED일 때 create 호출하지 않음.  
   - **CI 경로:** workflow가 batch_video_setup을 직접 호출할 때 ComputeEnvName을 넘기지 않아 기본값 academy-video-batch-ce 사용. 따라서 **ce**와 **ce-final** 두 CE가 공존할 가능성 있음.  
   - **판정:** **가능성 있음** (경로·기본값 불일치).

3) **EventBridge target이 중복 등록될 가능성이 있는가?**  
   - **아니오.** put-targets는 해당 Rule의 기존 타깃을 교체함. 중복 타깃 누적 없음.

4) **ASG DesiredCapacity가 변동될 위험이 있는가?**  
   - **예.** deploy_worker_asg.ps1은 ASG가 있을 때 update-auto-scaling-group으로 **desired-capacity 1**을 고정함. 현재값이 2 이상이면 1로 줄어듦.

5) **동시 실행 시 중복 생성 가능성이 있는가?**  
   - **예.** 락이 없어 두 인스턴스가 동시에 create-compute-environment 또는 create-job-queue를 호출하면 이름 충돌 또는 리소스 증식 가능.

**최종 판정:** **멱등 실패 위험 존재.**  
- JobDef revision 증가 (batch_video_setup).  
- CE 이름 이중화(ce vs ce-final)·동시 실행 시 증식 가능.  
- ASG DesiredCapacity 고정값(1) 덮어쓰기.

---

# 단계 6 — 필요한 수정사항 제안

## FAIL 항목

### 1. Job Definition 매회 register (batch_video_setup.ps1)

- **추가할 로직:** register 전에 `describe-job-definitions --job-definition-name $JobDefName --status ACTIVE`로 최신 revision을 가져와, **image / vcpus / memory / retryStrategy / timeout**이 현재 요청과 동일한지 비교. 동일하면 register 생략.
- **비교 필드:** containerProperties.image, .vcpus, .memory, retryStrategy.attempts, timeout.attemptDurationSeconds.
- **수정 위치:** scripts/infra/batch_video_setup.ps1 §5 (L318 근처). video_worker_infra_one_take.ps1의 이미지 비교 패턴 참고.

### 2. ASG DesiredCapacity 고정 (deploy_worker_asg.ps1)

- **추가할 로직:** update-auto-scaling-group 전에 `describe-auto-scaling-groups --auto-scaling-group-names $AsgAiName`로 현재 **DesiredCapacity**를 읽고, update 시 **--desired-capacity (현재값)**을 전달. min/max만 변경할 경우 desired는 현재값 유지.
- **비교 필드:** AutoScalingGroups[0].DesiredCapacity.
- **수정 위치:** scripts/deploy_worker_asg.ps1 L161-163, L176-178. create 실패 시 update 블록에서 desired-capacity를 변수(현재 describe 결과)로 설정.

### 3. Video CE 이름 불일치 (여러 스크립트)

- **단일화:** SSOT는 academy-video-batch-ce-final.
- **수정 제안:**  
  - recreate_batch_in_api_vpc.ps1: 기본값을 `academy-video-batch-ce-final`로 변경.  
  - batch_video_setup.ps1: ComputeEnvName 기본값을 `academy-video-batch-ce-final`로 변경.  
  - infra_one_take_full_audit.ps1: ExpectedVideoCEName 기본값을 `academy-video-batch-ce-final`로 변경.  
  - reconcile_video_batch_production.ps1: VideoCEName 기본값을 `academy-video-batch-ce-final`로 변경.  
  - VIDEO_WORKER_SCALING_SSOT.md: "현행" CE 이름을 academy-video-batch-ce-final로 수정하고, ce-v3는 레거시로 표기.

### 4. CI EventBridge 단계 파라미터 오류 (.github/workflows/video_batch_deploy.yml)

- **수정:** EventBridge 단계에서 `-JobQueueName` 대신 `-OpsJobQueueName` 사용, 값은 `academy-video-ops-queue`.
- **예:** `pwsh -File scripts/infra/eventbridge_deploy_video_scheduler.ps1 -Region "${{ env.AWS_REGION }}" -OpsJobQueueName "academy-video-ops-queue"`

## WARN 항목

### 5. 동시 실행 락

- **제안:** 원테이크 또는 통합 배포 스크립트 시작 시 DynamoDB 조건부 put 또는 S3 객체 기반 락으로 "deploy_lock" 획득. 실패 시 "Another deploy in progress" 후 exit. 해제는 스크립트 종료 시(finally).
- **문서:** SSOT-IDEMPOTENCY-RULES.md, SSOT-RUNBOOK.md에 "동시 실행 금지" 또는 락 사용 절차 명시.

### 6. CE 생성 후 VALID 대기 타임아웃

- **현재:** batch_video_setup 120초. SSOT-IDEMPOTENCY-RULES는 600초 권장.
- **제안:** 120초를 300~600초로 늌 뒤, 초과 시 FAIL 및 Evidence 출력으로 명확히 실패 처리.

---

# 단계 7 — 최종 요약

## 최종 멱등성 판정

**판정: 멱등 실패 위험 존재.**

- **이유:** (1) Job Definition이 배포 시마다 새 revision 등록됨. (2) Video CE 이름이 ce / ce-final 혼재해 CE 중복 또는 잘못된 CE 참조 가능. (3) ASG 배포 시 DesiredCapacity가 현재값이 아닌 1로 고정됨. (4) 동시 실행 락 없음.
- **원테이크만 반복 시:** CE/Queue/EventBridge는 Describe 후 조건부로만 변경되어 대체로 멱등에 가깝지만, JobDef revision은 증가하고, CE 이름이 호출 경로에 따라 ce vs ce-final로 나뉘어 있어 **부분 보장** 수준.

## 남은 P0 / P1 / P2 작업

| 우선순위 | 작업 | 담당 수정 |
|----------|------|-----------|
| **P0** | batch_video_setup.ps1에서 JobDef describe 후 image/vcpus/memory/retry/timeout 비교, 동일 시 register 생략 | scripts/infra/batch_video_setup.ps1 |
| **P0** | .github/workflows/video_batch_deploy.yml EventBridge 단계를 -OpsJobQueueName academy-video-ops-queue 로 수정 | .github/workflows/video_batch_deploy.yml |
| **P0** | recreate_batch_in_api_vpc.ps1, batch_video_setup.ps1, infra_one_take_full_audit.ps1, reconcile_video_batch_production.ps1 Video CE 기본값을 academy-video-batch-ce-final 로 통일 | 해당 스크립트 4개 |
| **P1** | deploy_worker_asg.ps1에서 ASG update 시 describe로 현재 DesiredCapacity 읽어 유지 | scripts/deploy_worker_asg.ps1 |
| **P1** | VIDEO_WORKER_SCALING_SSOT.md 현행 CE 이름을 academy-video-batch-ce-final 로 수정 | docs/video/worker/VIDEO_WORKER_SCALING_SSOT.md |
| **P1** | 배포 동시 실행 락 전략 도입 또는 RUNBOOK에 "동시 실행 금지" 명시 | docs/SSOT-RUNBOOK.md, 스크립트(선택) |
| **P2** | CE VALID 대기 타임아웃 300~600초로 조정 및 실패 시 Evidence 출력 | scripts/infra/batch_video_setup.ps1 |
| **P2** | SSOT-RESOURCE-INVENTORY에 IAM 역할명·Launch Template 이름 명시 | docs/SSOT-RESOURCE-INVENTORY.md |

## 레거시 제거 순서 제안

1. **문서·스크립트 정리:** CE 이름을 ce-final로 통일한 뒤, 기존 academy-video-batch-ce만 있는 계정이면 한 번만 원테이크로 ce-final 생성·Queue 전환 후 ce 삭제.
2. **레거시 CE 제거:** academy-video-batch-ce-v2, ce-v3, ce-public — RUNNING/RUNNABLE job 0 확인 후, Queue에서 분리 → CE DISABLED → 삭제. one_shot_video_ce_final.ps1 또는 수동.
3. **fallback Queue:** academy-video-batch-queue-ce 사용 중이면 batch_final_state.json·앱 설정을 academy-video-batch-queue로 맞춘 뒤, 이전 큐 비활성화·삭제.
4. **JobDefinition INACTIVE:** 오래된 revision은 AWS 콘솔 또는 스크립트로 INACTIVE 처리(정책 문서화 후 주기 실행).
5. **Video ASG 레거시:** academy-video-worker-asg 사용 중단 확인 후 ASG 삭제.

## 운영 자동화 진입 가능 여부

- **현재:** **조건부 가능.**  
  - 원테이크 스크립트와 EventBridge/SSM/Batch CE·Queue 로직은 Describe 기반으로 되어 있어, P0 수정(JobDef 비교·CI 파라미터·CE 이름 통일)을 적용한 뒤에는 **단일 실행** 기준으로 운영 자동화에 활용 가능.  
  - **제한:** (1) JobDef revision 무한 증가 방지를 반드시 수정해야 함. (2) 동시 배포는 락 도입 전까지 수동으로 1회만 실행하도록 운영 규칙 필요. (3) ASG 배포는 Desired 유지 수정 전까지 별도 주의(재배포 시 desired=1로 바뀜).

- **P0 반영 후:** 원테이크 + 검증 스크립트를 CI 또는 배포 파이프라인에 넣고, EventBridge 단계 파라미터와 CE 이름을 SSOT에 맞춘 상태로 **운영 자동화 진입 가능**하다고 판정할 수 있음.

---

**확인 명령 정리**

- CE 목록: `aws batch describe-compute-environments --region ap-northeast-2 --query "computeEnvironments[?starts_with(computeEnvironmentName,'academy-video')].{Name:computeEnvironmentName,State:state,Status:status}" --output table`
- Video Queue 연결 CE: `aws batch describe-job-queues --job-queues academy-video-batch-queue --region ap-northeast-2 --query "jobQueues[0].computeEnvironmentOrder" --output json`
- JobDef revision 수: `aws batch describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE --region ap-northeast-2 --query "length(jobDefinitions)" --output text`
- EventBridge 타깃: `aws events list-targets-by-rule --rule academy-reconcile-video-jobs --region ap-northeast-2 --query "Targets[0].Arn" --output text`
- ASG Desired: `aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-messaging-worker-asg --region ap-northeast-2 --query "AutoScalingGroups[0].DesiredCapacity" --output text`
