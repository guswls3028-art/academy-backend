# V1 기준 기존 인프라 정리 및 재배포 플랜

**AI·Cursor 룰:** 본 문서를 포함한 리포지토리 내 **모든 문서·코드에 대해 AI(Cursor Agent)는 열람·수정 권한**이 있다. 배포·인프라 변경 시 **.cursor/rules/** 내 해당 룰(예: `07_deployment_orchestrator.mdc`, `04_cost_engine.mdc`)을 **적재적소에 항시 확인**한다.  
**배포 원칙:** 모든 배포·재배포는 **빌드 서버 경유**이며, `-SkipBuild`는 예외 상황에만 사용한다. **비용 최적화:** ECR 라이프사이클 정책이 배포 시 자동 적용된다.

**기준 문서:** `docs/00-SSOT/v1/SSOT.md`, `params.yaml`, `INFRA-AND-SPECS.md`  
**배포 스크립트:** `scripts/v1/deploy.ps1`  
**현재 인프라:** `docs/00-SSOT/v1/AWS-INFRA-REPORT.md`  
**작성일:** 2026-03-05

---

## 1. 목표

- **기존 인프라(academy-*, academy-v4-* 등) 정리**
- **SSOT v1 네이밍·구성으로 전면 재배포**
- **최종 상태:** academy-v1-* 리소스만 존재, API ASG + ALB, Batch, RDS, Redis, DynamoDB, EventBridge 등 v1 스펙 충족

---

## 2. 현재 vs V1 요약

| 구분 | 현재 (기존) | V1 목표 |
|------|-------------|---------|
| **API** | EC2 1대 (academy-api, 수동) | academy-v1-api-asg + academy-v1-api-alb + academy-v1-api-tg |
| **Build** | EC2 1대 (academy-build-arm64, stopped) | 태그 academy-build-arm64, v1 서브넷/SG |
| **Batch CE** | academy-video-batch-ce-final, academy-video-ops-ce (1개 INVALID) | academy-v1-video-batch-ce, academy-v1-video-ops-ce |
| **Batch Queue** | academy-video-batch-queue, academy-video-ops-queue | academy-v1-video-batch-queue, academy-v1-video-ops-queue |
| **RDS** | academy-db (기존) | academy-v1-db (신규 생성 또는 기존 DB 활용 정책 필요) |
| **Redis** | SG만 존재 가능 | academy-v1-redis (ElastiCache) |
| **DynamoDB** | 없음 | academy-v1-video-job-lock |
| **VPC** | vpc-0831a2484f9b114c2 등 4개 | academy-v1-vpc (신규 또는 기존 VPC 지정) |
| **ECR** | academy-base, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu | academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu (+ academy-base는 Prune 시 삭제 후보) |
| **Lambda** | academy-worker-queue-depth-metric, academy-worker-autoscale | v1 SSOT에는 Lambda 미포함 (유지/삭제 정책 결정 필요) |
| **EventBridge** | (기존 규칙 있을 수 있음) | academy-v1-reconcile-video-jobs, academy-v1-video-scan-stuck-rate |

---

## 3. 배포 전 준비 (Phase 0)

### 3.1 params.yaml 검토

- **경로:** `docs/00-SSOT/v1/params.yaml`
- **필수 확인:**
  - `network.vpcId`: 비어 있으면 **academy-v1-vpc**를 태그로 찾거나 **신규 생성**. 기존 VPC 재사용 시 해당 VPC ID 입력.
  - `networkPublicSubnets` / `networkPrivateSubnets`: 비어 있으면 스크립트가 **academy-v1-vpc** 내 서브넷을 이름(academy-v1-public-a/b, academy-v1-private-a/b)으로 찾거나 신규 생성.
  - `rds.dbIdentifier`: **academy-v1-db**. 기존 academy-db를 그대로 쓰지 않으면 새 RDS가 생성됨(스크립트는 RDS 삭제 안 함).
  - `rds.masterPasswordSsmParam`: `/academy/rds/master_password` 등 SSM SecureString에 마스터 비밀번호 저장 필수.
  - **Bootstrap** 시 SSM 비밀번호·SQS·ECR URI 등 자동 준비 가능하나, RDS/Redis는 Strict 검사에서 실패할 수 있으므로 `-SkipRds -SkipRedis`로 1차 배포 후 수동 생성 가능.

### 3.2 RDS 정책 결정

- **옵션 A:** 새 **academy-v1-db** 생성 후 애플리케이션만 v1 인프라에 연결 (데이터 초기화 또는 별도 마이그레이션).
- **옵션 B:** 기존 **academy-db** 유지 사용 시, params.yaml의 `dbIdentifier`를 academy-db로 변경하고, **DB 서브넷 그룹**을 v1 private 서브넷으로 맞추거나 기존 서브넷 그룹 이름을 params에 반영. (RDS 인스턴스 이름 변경 불가이므로 식별자만 academy-db로 두고 v1 네이밍은 다른 리소스에만 적용.)

### 3.3 AWS 자격 증명

- `aws sts get-caller-identity --profile default` 로 확인.
- Cursor/자동화에서는 `deploy.ps1 -AwsProfile default` 사용 권장.
- **토큰 만료 시:** `UnrecognizedClientException` / `AuthFailure` 발생. 자격증명 갱신 후 **같은 셸**에서 `deploy.ps1` 재실행.
- 스크립트 내부: `scripts/v1/core/aws.ps1`에서 `AWS_PROFILE`이 설정되면 모든 `aws` 호출에 `--profile`을 자동 주입(2026-03-05 반영).

### 3.4 빌드 서버 (Spot + 온디맨드 폴백)

- **경로:** `scripts/v1/resources/build.ps1`
- **동작:** 빌드 인스턴스가 없을 때 `run-instances`를 먼저 **Spot**으로 요청. 인스턴스가 0개면 **온디맨드**로 재시도.
- 빌드 시 로컬이 아닌 **빌드용 서버**에서 Docker 빌드·ECR 푸시 수행. 도커 파일은 최적화된 상태에 맞춰 사용.

### 3.5 다운타임 허용 범위

- PruneLegacy + Purge + Ensure 동안 **Batch·API·EventBridge**는 순차적으로 삭제 후 재생성되므로 **영상 배치·API 서버 다운타임** 발생.
- RDS/Redis는 스크립트가 삭제하지 않으나, 새 VPC/서브넷 사용 시 보안 그룹·라우팅 변경으로 접근 경로가 바뀔 수 있음.

---

## 4. Phase 1 — 기존 인프라 정리

### 4.1 스크립트로 정리 (PruneLegacy)

**목적:** SSOT v1에 없는 리소스만 삭제 (academy-v1-* 가 아닌 academy-* 등).

```powershell
cd C:\academy
pwsh scripts/v1/deploy.ps1 -Plan -PruneLegacy -AwsProfile default
```

- **결과:** 삭제 후보 표만 출력, 실제 변경 없음. 표에서 다음이 나올 수 있음.
  - **Batch CE:** academy-video-batch-ce-final, academy-video-ops-ce
  - **Batch Queue:** academy-video-batch-queue, academy-video-ops-queue
  - **EventBridge Rule:** (기존 규칙명이 v1과 다르면)
  - **ECR:** academy-base (v1 SSOT에는 academy-api, video-worker, messaging-worker, ai-worker-cpu만 있음)
  - **IAM Role:** (v1에 없는 역할만; v1 역할 목록은 `core/ssot.ps1`의 SSOT_IAMRoles 참고)
  - **EIP:** 연동되지 않은 EIP만

실제 삭제 실행:

```powershell
pwsh scripts/v1/deploy.ps1 -PruneLegacy -AwsProfile default
```

- **동작:** EventBridge 타깃 제거 → 규칙 삭제 → Batch Queue 비활성화·삭제 → Batch CE 비활성화·삭제 → JobDef deregister → ASG(이름이 v1이 아닌 것) 삭제 → ECS 클러스터 삭제 → IAM 역할 정리 → ECR/SSM/EIP 등 후보 삭제.
- **주의:** 현재 API는 ASG가 아니라 단일 EC2이므로 PruneLegacy에서 ASG 삭제 대상에 안 걸림. EC2 2대(academy-api, academy-build-arm64)는 **수동 정리** 필요.

### 4.2 수동 정리 (스크립트 미처리)

| 순서 | 대상 | 조치 |
|------|------|------|
| 1 | **EC2 academy-api** (i-0c8ae616abf345fd1) | 필요 시 스냅샷/백업 후 **EIP 해제 → 인스턴스 종료**. v1 배포 후 ALB 뒤 새 API 인스턴스로 대체. |
| 2 | **EC2 academy-build-arm64** (i-0133290c3502844ab) | 이미 stopped. v1 Build는 새 서브넷/SG로 태그만 academy-build-arm64 사용하므로 **종료**해도 됨. |
| 3 | **Lambda 2개** (academy-worker-queue-depth-metric, academy-worker-autoscale) | v1 SSOT에 Lambda 없음. 유지할지 **수동 삭제**할지 결정 후 `aws lambda delete-function --function-name <이름> --profile default` |
| 4 | **기존 VPC/서브넷/SG** | v1이 **새 VPC(academy-v1-vpc)** 를 만들면 기존 vpc-0831a2484f9b114c2 등은 나중에 수동 삭제 가능. RDS/ElastiCache가 기존 VPC에 있으면 삭제 시 주의. |

### 4.3 (선택) 전량 Purge 후 재생성

v1 범위 리소스까지 **전부 끄고 다시 만들고 싶을 때**:

```powershell
pwsh scripts/v1/deploy.ps1 -PurgeAndRecreate -AwsProfile default
```

- **동작:** v1 EventBridge 규칙 비활성화·타깃 제거 → v1 Batch Queue 비활성화·삭제 → v1 Batch CE 비활성화·삭제 → v1 JobDef deregister → **academy-v1-api-asg** 삭제(있을 경우) → 이후 스크립트가 **전체 Ensure** 재실행.
- **주의:** RDS·Redis·DynamoDB·ECR 이미지는 **삭제하지 않음**. 네트워크(VPC/서브넷)도 Purge 대상이 아님.

---

## 5. Phase 2 — V1 배포 (Ensure)

### 5.1 배포 순서 (SSOT 및 deploy.ps1 기준)

스크립트가 다음 순서로 Ensure 수행함.

| 단계 | 내용 |
|------|------|
| 1 | **Guard** — 동시 실행 락 (SSM `/academy/deploy-lock`) |
| 2 | **Load params.yaml** + 검증 |
| 3 | **Preflight** |
| 4 | **Drift** 계산·표 출력 |
| 5 | (선택) **PruneLegacy** 이미 실행했다면 생략 |
| 6 | **Ensure:** IAM → Network(VPC/서브넷/NAT/SG) → RDS 상태 확인/Redis 상태 확인 → SSM → ECR → DynamoDB(lock 테이블) → ASG Messaging → ASG AI → Batch CE(Video/Ops) → Batch Queue → JobDef → EventBridge → ALB → API(ASG) → Build |
| 7 | **Netprobe** (선택, -SkipNetprobe 시 생략) |
| 8 | **Evidence** 보고서 저장 |
| 9 | **Lock** 해제 |

### 5.2 실행 예시

**드라이런(변경 없음):**

```powershell
pwsh scripts/v1/deploy.ps1 -Plan -AwsProfile default
```

**실배포 (Bootstrap 포함, RDS/Redis 준비됐다고 가정):**

```powershell
pwsh scripts/v1/deploy.ps1 -Env prod -AwsProfile default
```

**RDS/Redis 아직 없을 때 (1차로 네트워크·Batch·API만):**

```powershell
pwsh scripts/v1/deploy.ps1 -Env prod -SkipRds -SkipRedis -AwsProfile default
```

**이미지 태그 지정 (immutable 태그 필수):**

```powershell
pwsh scripts/v1/deploy.ps1 -Env prod -EcrRepoUri "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:20260305-abc" -AwsProfile default
```

### 5.3 ECR 리포지토리

- v1 SSOT: **academy-api**, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu.
- **academy-api** 리포지토리가 없으면 Ensure 시 생성됨.
- **academy-base**는 PruneLegacy 시 삭제 후보가 될 수 있음. 빌드 파이프라인에서 base 이미지 필요 시 Prune 전에 이미지 백업 또는 SSOT_ECR에 포함 여부 검토.

---

## 6. Phase 3 — 검증

- **Netprobe:** Batch netprobe Job 실행으로 네트워크·Batch 동작 확인 (deploy 시 -SkipNetprobe 안 주면 자동 실행).
- **Evidence:** 스크립트가 생성한 Evidence 보고서 확인.
- **수동:** ALB URL로 `/health`, API·Batch Job 제출, SQS·DynamoDB lock 테이블 사용 여부 확인.

---

## 7. 체크리스트 요약

| # | 항목 | 담당 | 완료 |
|---|------|------|------|
| 1 | params.yaml 네트워크·RDS·Redis·SSM 설정 검토 | 사람 | ☐ |
| 2 | RDS 정책 결정 (신규 academy-v1-db vs 기존 academy-db) | 사람 | ☐ |
| 3 | `deploy.ps1 -Plan -PruneLegacy` 실행하여 삭제 후보 확인 | 사람/스크립트 | ☐ |
| 4 | `deploy.ps1 -PruneLegacy` 실행 (레거시 삭제) | 스크립트 | ☐ |
| 5 | EC2 academy-api, academy-build-arm64 종료/삭제 (필요 시) | 수동 | ☐ |
| 6 | Lambda 유지/삭제 결정 및 필요 시 삭제 | 수동 | ☐ |
| 7 | (선택) PurgeAndRecreate 실행 | 스크립트 | ☐ |
| 8 | `deploy.ps1 -Env prod` 실행 (V1 전체 Ensure) | 스크립트 | ☐ |
| 9 | Netprobe·Evidence·수동 검증 | 사람/스크립트 | ☐ |

---

## 8. 참조

- **SSOT:** `docs/00-SSOT/v1/SSOT.md`
- **params:** `docs/00-SSOT/v1/params.yaml`
- **스펙:** `docs/00-SSOT/v1/INFRA-AND-SPECS.md`
- **현재 인프라:** `docs/00-SSOT/v1/AWS-INFRA-REPORT.md`
- **배포 스크립트:** `scripts/v1/deploy.ps1`, `scripts/v1/core/prune.ps1`
- **검증:** `scripts/v1/verify.ps1` (Bootstrap → Plan → PruneLegacy → deploy 순서 점검)
- **최종 보고·스크립트 변경:** `docs/00-SSOT/v1/V1-FINAL-REPORT.md` (자격증명·Bootstrap workers env·aws.ps1 프로파일 주입·빌드 Spot 폴백 등)
