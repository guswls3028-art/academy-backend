# 프로젝트 전체 인프라 통합 분석 보고서

**문서 버전:** 1.0  
**작성일:** 2025-02-27  
**대상:** Academy SSOT v4 기반 인프라 (scripts/v4, params.yaml)  
**목적:** 현재 인프라의 실제 구성 상태를 통합·정리하고, 배포 순서·멱등·정렬·리스크를 명시.

---

## 1. 전체 인프라 구성 다이어그램 (텍스트)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  Region: ap-northeast-2  │  VPC: vpc-0831a2484f9b114c2  │  Public Subnets x4             │
└─────────────────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────────────────────┐
  │  단일 진입점: scripts/v4/deploy.ps1  (Guard: SSM /academy/deploy-lock, 2h)           │
  └──────────────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐     ┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐
  │   API       │     │   Build     │     │ Messaging ASG   │     │   AI ASG        │
  │ academy-api │     │ Tag-based  │     │ academy-        │     │ academy-ai-     │
  │ ASG 1/1/1   │     │ EC2 1대     │     │ messaging-      │     │ worker-asg      │
  │ + EIP       │     │ (arm64)     │     │ worker-asg      │     │ min/max/desired │
  │ 15.165.x.x  │     │             │     │ (params)        │     │ (params)        │
  └──────┬──────┘     └──────┬──────┘     └────────┬────────┘     └────────┬────────┘
         │                   │                     │                       │
         │                   │                     │                       │
         ▼                   ▼                     ▼                       ▼
  ┌─────────────┐     ┌─────────────┐     ┌─────────────────────────────────────────────┐
  │ SSM         │     │ ECR         │     │  AWS Batch (Video + Ops)                      │
  │ /academy/   │     │ academy-api │     │  ┌─────────────────┐  ┌─────────────────┐  │
  │ api/env     │     │ academy-    │     │  │ Video CE         │  │ Ops CE           │  │
  │ /academy/   │     │ video-      │     │  │ academy-video-   │  │ academy-video-   │  │
  │ workers/env │     │ worker      │     │  │ batch-ce-final   │  │ ops-ce           │  │
  └─────────────┘     │ academy-    │     │  │ minvCpus=0       │  │ minvCpus=0       │  │
         │            │ messaging-  │     │  │ maxvCpus=32      │  │ maxvCpus=2       │  │
         │            │ worker      │     │  └────────┬─────────┘  └────────┬─────────┘  │
         │            │ academy-ai- │     │           │                     │            │
         │            │ worker-cpu  │     │           ▼                     ▼            │
         │            └─────────────┘     │  academy-video-batch-queue   academy-video-  │
         │                                │  academy-video-batch-jobdef   ops-queue      │
         │                                │  + EventBridge: reconcile(15m), scanstuck(5m)│
         │                                └─────────────────────────────────────────────┘
         │
  ┌──────┴──────────────────────────────────────────────────────────────────────────────┐
  │  검증 전제 (수정 없음): RDS academy-db, Redis academy-redis, SG batch/api/workers    │
  └─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 서비스별 역할

| 서비스 | 형태 | 역할 | 식별자 |
|--------|------|------|--------|
| **API 서버** | EC2 ASG 1대 + Elastic IP | Django API, `/health` 노출, Public Subnet, Docker `academy-api` | ASG: academy-api-asg, EIP: eipalloc-071ef2b5b5bec9428 |
| **빌드 서버** | EC2 1대 (태그 기반) | 이미지 빌드·ECR 푸시 (ARM64), Stopped 허용 | Tag: Name=academy-build-arm64 |
| **ASG 워커 (Messaging)** | EC2 ASG | 메시징 워커, SQS 소비 | academy-messaging-worker-asg, academy-messaging-worker-lt |
| **ASG 워커 (AI)** | EC2 ASG | AI 워커 (CPU) | academy-ai-worker-asg, academy-ai-worker-lt |
| **Video 배치 워커** | AWS Batch | 영상 인코딩·R2 업로드, JobDef로 Task 실행 | CE: academy-video-batch-ce-final, Queue: academy-video-batch-queue, JobDef: academy-video-batch-jobdef |
| **Ops 배치** | AWS Batch + EventBridge | reconcile(15분), scan_stuck(5분), netprobe(배포 시 검증) | CE: academy-video-ops-ce, Queue: academy-video-ops-queue, Rule: academy-reconcile-video-jobs, academy-video-scan-stuck-rate |
| **RDS** | Validate only | PostgreSQL, 삭제 금지(기본) | academy-db |
| **Redis** | Validate only | ElastiCache, 삭제 금지(기본) | academy-redis |
| **ECR** | Ensure | API, video-worker, messaging-worker, ai-worker-cpu, base | params.yaml ecr 섹션 |
| **SSM** | Ensure | API/Workers env | /academy/api/env, /academy/workers/env |

---

## 3. ASG min/max 및 스케일 정책 (현재 코드 기준)

| 리소스 | Min | Max | Desired | 비고 |
|--------|-----|-----|---------|------|
| **API ASG** | 1 | 1 | 1 | params.yaml `api.asg` + api.ps1. 고정 1대, EIP 연동. |
| **Messaging ASG** | 0 | 4 | 0 | params.yaml `messagingWorker.scaling`. Ensure 시 drift면 SSOT 값으로 update. |
| **AI ASG** | 0 | 4 | 0 | params.yaml `aiWorker.scaling`. 동일. |
| **Video Batch CE** | 0 vCPU | 32 vCPU | 0 | templates/batch/video_compute_env.json. minvCpus=0, maxvCpus=32. |
| **Ops Batch CE** | 0 vCPU | 2 vCPU | 0 | templates/batch/ops_compute_env.json. |

**참고:** `infra/worker_asg/asg-scalable-target.yaml`(CloudFormation)은 AI 워커용 Application Auto Scaling Min=0, Max=20으로 정의되어 있으나, **실제 배포는 scripts/v4만 사용**하며 params.yaml이 단일 소스. 해당 CFN 스택 배포 여부는 계정 상태에 따름(중복 리소스 섹션 참고).

---

## 4. 서비스 간 의존성 순서 (현재 상태 기준)

- **배포(Ensure) 시:**  
  IAM → Network(VPC/Subnet 검증) → RDS/Redis 검증·SG → SSM → ECR → **ASG Messaging → ASG AI** → Video CE → Ops CE → Video Queue → Ops Queue → Video JobDef → Ops JobDef(reconcile, scanstuck, netprobe) → EventBridge Rules → **API Ensure** → **Build Ensure** → (선택) Netprobe.
- **의존 관계:**  
  - Batch IAM → 모든 Batch CE/Queue/JobDef.  
  - Ops Queue 존재 → EventBridge put-targets.  
  - Video/Ops CE → 해당 Queue.  
  - API Ensure는 EIP 연동·SSM·/health 200 대기.

---

## 5. 배포 시 실제 실행 순서

**정상 One-Take (deploy.ps1, -Plan 없음):**

1. Guard: 동시 실행 락 획득 (SSM `/academy/deploy-lock`).
2. Load params.yaml + 검증.
3. Preflight (AWS identity, region, describe 권한).
4. Drift 계산 → 표 출력 및 리포트 저장.
5. (옵션) PruneLegacy: SSOT 외 academy-* 삭제 후 6번으로.
6. (옵션) PurgeAndRecreate: SSOT 범위 Batch/EventBridge/API ASG 삭제 후 7번 풀 Ensure.
7. **Ensure 순서:**  
   `Ensure-BatchIAM` → `Ensure-NetworkVpc` → `Confirm-SubnetsMatchSSOT` → `Confirm-RDSState` → `Ensure-RDSSecurityGroup` → `Confirm-RedisState` → `Ensure-RedisSecurityGroup` → `Confirm-SSMEnv` → `Ensure-ECRRepos` → **Ensure-ASGMessaging** → **Ensure-ASGAi** → Ensure-VideoCE → Ensure-OpsCE → Ensure-VideoQueue → Ensure-OpsQueue → Ensure-VideoJobDef → Ensure-OpsJobDefReconcile → Ensure-OpsJobDefScanStuck → Ensure-OpsJobDefNetprobe → Ensure-EventBridgeRules → **Ensure-API** → **Ensure-Build**.
8. Netprobe (Ops Queue netprobe Job SUCCEEDED 대기, 또는 -SkipNetprobe).
9. Evidence 표 출력 및 리포트 저장.
10. Lock 해제.

**PruneLegacy 삭제 순서 (state-contract.md·prune.ps1):**  
EventBridge targets 제거 → rules 삭제 → Batch Queues DISABLED→삭제 → Batch CEs DISABLED→삭제 → JobDef deregister(SSOT 외) → ASG(min=0, desired=0 → force-delete) → ECS cluster → IAM(detach/delete) → ECR(SSOT 외) → SSM(SSOT 외) → EIP(미연결만 release).  
RDS/Redis·params.yaml 정의 리소스·API EIP allocationId는 보호.

**PurgeAndRecreate(SSOT 범위) 삭제 순서 (prune.ps1):**  
EventBridge targets 제거 → rules DISABLED → SSOT Queues DISABLED→삭제 → SSOT CEs DISABLED→삭제 → SSOT JobDef deregister → API ASG scale 0→삭제 → (선택) PruneLegacy → 이후 풀 Ensure.

---

## 6. 상태 저장 방식

| 구분 | 저장 위치 | 용도 |
|------|-----------|------|
| **단일 소스(SSOT)** | `docs/00-SSOT/v4/params.yaml` | 기계용 설정 전부 (리전, VPC, 서브넷, ASG 이름, Batch 이름, ECR, SSM 경로, EIP 등). |
| **락** | AWS SSM Parameter Store `/academy/deploy-lock` | 동시 배포 방지, 최대 2시간 유효. |
| **Drift/Evidence** | `docs/00-SSOT/v4/reports/` (drift.latest.md, evidence 등) | 배포 시 생성·갱신. |
| **인프라 상태** | AWS API (Describe-*) | Terraform/CloudFormation 등 백엔드 없음. 코드+params가 진실. |

---

## 7. 멱등성이 보장되는 부분 / 안 되는 부분

**보장되는 부분:**

- **Batch CE:** 없으면 Create, INVALID면 Queue DISABLED → CE DISABLED → Delete → Wait 삭제 → Create → Wait VALID/ENABLED → Queue 재연결. 동일 스펙 재실행 시 No-op.
- **Batch Queue:** 없으면 Create(CE ARN), state/order SSOT 일치 시 update만. 있으면 No-op.
- **JobDef:** Describe ACTIVE 후 drift 시에만 새 revision 등록. 동일 스펙이면 기존 revision 유지.
- **ASG (API/Messaging/AI):** Describe 후 capacity drift 있을 때만 update. **DesiredCapacity는 SSOT 값으로 덮어씀**(params에 0이면 0으로 맞춤). LT drift 시에만 새 버전·instance-refresh.
- **EventBridge:** Rule 없으면 put-rule + put-targets, 있으면 **Target만** put-targets. Rule 삭제 후 재생성 금지.
- **SSM:** get-parameter 후 put-parameter --overwrite. 동일 값이면 버전만 증가.
- **ECR:** create-repository 없을 때만 생성.
- **API EIP:** associate-address --allow-reassociation. 이미 올바른 인스턴스에 붙어 있고 health 200이면 No-op.
- **Build:** Describe by tag, 없으면 Create, AMI drift면 Terminate→Create. 동일 인스턴스·동일 AMI면 No-op.

**보장이 깨지기 쉬운 부분 / 비멱등 요소:**

- **동시 실행:** 락 없이 두 배포가 겹치면 순서·상태 꼬임. 락 사용 시에만 멱등 전제 성립.
- **수동 변경:** 콘솔에서 ASG desired, Batch CE/Queue, EventBridge target 등을 바꾸면 다음 deploy 시 SSOT로 다시 맞추므로 “의도치 않은 0으로 스케일” 가능(Messaging/AI desired=0인 경우).
- **PurgeAndRecreate:** SSOT 범위 전체 삭제 후 재생성. 한 번 실행 시 데이터/연결 끊김 구간 존재.
- **PruneLegacy:** SSOT 외 리소스 삭제. 삭제 후보 잘못 식별되면 보호 리소스 삭제 가능성(코드상 RDS/Redis/params 리소스는 제외).
- **Netprobe 실패:** RUNNABLE 정체 등으로 실패 시 배포 스크립트가 throw. 재실행 시 이전 Job 상태에 따라 동작이 달라질 수 있음(보통 재실행으로 수렴).

---

## 8. 중복 리소스 / 충돌 가능성

- **Application Auto Scaling vs params:**  
  `infra/worker_asg/asg-scalable-target.yaml`은 AI ASG에 대해 MinCapacity=0, MaxCapacity=20 정의.  
  scripts/v4는 params.yaml의 min/max/desired만 사용하며, 해당 CFN을 배포하지 않음.  
  **충돌:** CFN으로 별도 스택을 배포한 경우, ASG desired는 v4 스크립트가 갱신하고 Application Auto Scaling은 desired 범위만 0~20으로 제한. 목표 정책이 “min=1 max=10” 등이면 params와 CFN 스택 둘 다 정리 필요.

- **Legacy 스크립트:**  
  `scripts/infra/*`, `scripts/archive/*`는 deploy.ps1 및 CI 가드에서 실행 금지.  
  워크플로에서 `scripts/infra` 실행 시 실패하도록 되어 있음.  
  **중복 실행 방지:** 단일 진입점은 `scripts/v4/deploy.ps1`만 사용.

- **ECR:**  
  build-and-push-ecr.yml은 base, api, messaging-worker, ai-worker-cpu, video-worker 푸시.  
  video_batch_deploy.yml은 video-worker만 빌드·푸시 후 deploy.ps1에 EcrRepoUri 전달.  
  **충돌:** 동일 리포 이름에 서로 다른 워크플로가 푸시할 수 있으나, 리포 자체는 하나. 태그(:latest 등) 덮어쓰기만 주의.

---

## 9. 정렬이 깨질 가능성이 있는 부분

- **Ensure 순서는 코드에 고정:**  
  deploy.ps1에서 리소스 Ensure 호출 순서가 정해져 있어, 단일 프로세스로 실행되는 한 정렬은 유지됨.

- **동시 배포:**  
  락을 우회하거나 락 만료(2시간) 내에 다른 프로세스가 배포하면, EventBridge·Batch·ASG·API가 서로 다른 단계에서 동시에 변경되어 순서가 깨질 수 있음.

- **PurgeAndRecreate 중 실패:**  
  Purge 중간에 실패하면 EventBridge는 비활성화됐는데 Queue/CE는 아직 살아 있거나, 그 반대 상태가 될 수 있음. 재실행 시 Ensure가 남은 리소스를 정리·재생성하므로, “한 번에 한 방향으로만” 정렬은 복구 가능하나, 중간 상태에서는 일시적으로 정렬이 깨진 것처럼 보일 수 있음.

- **ASG capacity:**  
  params에 Messaging/AI desired=0이면, 매 배포마다 “현재 desired를 0으로 맞춘다”는 의미. 수동으로 1 이상 올려둔 경우 배포 시 다시 0이 됨. **정렬**이라기보다 **의도치 않은 스케일 다운** 리스크.

- **Batch CE 재생성:**  
  INVALID 시 Queue DISABLED → CE DISABLED → Delete → Wait → Create → Wait → Queue 재연결 순서가 코드에 고정되어 있어, 단일 실행에서는 정렬 유지. 이 구간에서 타임아웃·예외 시 수동 개입 없으면 반쪽만 적용될 수 있음.

---

## 10. 현재 구조의 근본 철학 (코드 기반 추론)

- **단일 진입점·단일 SSOT:**  
  모든 인프라 변경은 `scripts/v4/deploy.ps1` 한 곳에서만 수행. 기계용 진실은 `params.yaml` 하나. IaC 백엔드(Terraform/CloudFormation 상태) 없이 “코드 + AWS Describe”로 현재 상태를 판단하고, 부족하면 Ensure, 넘치면 PruneLegacy로 정리.

- **Describe → Decision → Update/Create:**  
  모든 Ensure는 먼저 AWS Describe로 현재 상태를 읽고, SSOT와 비교한 뒤, 필요할 때만 Update/Create. 재실행 시 2회차는 대부분 No-op이 되도록 설계.

- **정렬·의존성은 스크립트 순서로 보장:**  
  IAM → 네트워크·DB·캐시 검증 → SSM·ECR → 워커 ASG → Batch CE/Queue/JobDef → EventBridge → API → Build 순으로 의존 관계를 반영. “원테이크”로 한 번에 한 방향만 적용.

- **동시성은 락으로만 제어:**  
  분산 락이나 상태 기반 순서 제어는 없고, SSM 파라미터 기반 단일 락으로 “한 번에 한 배포만” 보장.

- **검증 전제·삭제 금지:**  
  RDS·Redis는 “존재·SG 확인”만 하고 삭제하지 않음. API EIP allocationId는 canonical로 보호. PruneLegacy도 params에 정의된 리소스는 삭제 후보에서 제외.

- **Legacy 제거:**  
  scripts/infra, scripts/archive는 사용 금지. CI에서 호출 시 실패. v4만 사용해 구식 스크립트와의 이중 배포·정렬 꼬임을 방지.

- **배포 성공 게이트:**  
  Netprobe(Ops Queue Job SUCCEEDED)로 Batch 경로가 실제로 동작하는지 확인. 실패 시 배포를 throw로 중단해 “반쯤만 반영된” 상태로 끝나지 않도록 함.

---

## 11. 현재 구조의 문제점 요약

| 문제 | 설명 | 권장 |
|------|------|------|
| **Messaging/AI min=0, desired=0** | params 현재값이 0/4/0. “항상 최소 1대” 요구와 불일치 시 의도치 않은 스케일 다운 가능. | 요구가 min=1 max=10이면 params.yaml `messagingWorker.scaling` / `aiWorker.scaling` 수정. |
| **Video Batch CE maxvCpus=32** | 목표가 “min=0 max=10”이면 템플릿과 불일치. | `templates/batch/video_compute_env.json`의 maxvCpus를 10 등으로 조정. |
| **Application Auto Scaling CFN** | infra/worker_asg/asg-scalable-target.yaml과 v4 params가 별도 소스. 이중 관리·숫자 불일치 가능. | v4만 쓸 경우 CFN 스택 미배포 또는 제거; 쓸 경우 min/max를 params와 맞추고 문서화. |
| **params.yaml 파서** | 2단계 키만 파싱. `scaling.minSize` 등 중첩은 플랫폼에 따라 키가 섞일 수 있음. | 중첩 키 사용 시 파서 확장 또는 단일 레벨 키로 평탄화. |
| **PurgeAndRecreate 시 API ASG 삭제** | SSOT Purge 시 API ASG까지 0→삭제함. 재 Ensure 전까지 API 단절. | 필요 시 Purge 단계에서 API ASG 제외 옵션 검토. |
| **고정 Sleep 잔존** | Batch CE/Queue 상태 대기 시 batch.ps1 내부에 5~10초 고정 Sleep 루프 있음. state-contract는 “describe 기반 Wait만” 권장. | wait.ps1의 Wait-*로 통합해 describe 폴링만 사용하도록 정리. |
| **JobDef :latest** | IDEMPOTENCY-RULES에서는 “immutable tag 필수, :latest 사용 시 원테이크 FAIL”이라 했으나, jobdef.ps1은 EcrRepoUri 미지정 시 `:latest` 사용. | 운영에서 :latest 대신 커밋/빌드 기반 태그 사용 권장하고, CI에서 EcrRepoUri 전달로 일관화. |

---

## 12. 포함 리소스 체크리스트 (API·빌드·ASG 워커·Video 배치)

| 항목 | 포함 여부 | 위치 |
|------|-----------|------|
| API 서버 | ✅ | api.ps1, params.yaml api, deploy.ps1 Ensure-API |
| 빌드 서버 | ✅ | build.ps1, params.yaml build, deploy.ps1 Ensure-Build |
| ASG 워커 (AI) | ✅ | asg_ai.ps1, params.yaml aiWorker, deploy.ps1 Ensure-ASGAi |
| ASG 워커 (Messaging) | ✅ | asg_messaging.ps1, params.yaml messagingWorker, deploy.ps1 Ensure-ASGMessaging |
| Batch 워커 (Video) | ✅ | batch.ps1, jobdef.ps1, params.yaml videoBatch, templates/batch, EventBridge |

---

*이 문서는 코드·params·state-contract·SSOT·runbook 기준으로 작성되었으며, 실제 AWS 계정 상태는 Describe 결과为准으로 별도 확인이 필요합니다.*
