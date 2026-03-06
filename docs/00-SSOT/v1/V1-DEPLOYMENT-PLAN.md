# V1 기준 기존 인프라 정리 및 재배포 플랜

**AI·Cursor 룰:** 본 문서를 포함한 리포지토리 내 **모든 문서·코드에 대해 AI(Cursor Agent)는 열람·수정 권한**이 있다. 배포·인프라 변경 시 **.cursor/rules/** 내 해당 룰(예: `07_deployment_orchestrator.mdc`, `04_cost_engine.mdc`)을 **적재적소에 항시 확인**한다.  
**배포 원칙:** 모든 배포·재배포는 **빌드 서버 경유**이며, `-SkipBuild`는 예외 상황에만 사용한다. **비용 최적화:** ECR 라이프사이클 정책이 배포 시 자동 적용된다.

**기준 문서:** `docs/00-SSOT/v1/SSOT.md`, `params.yaml`, `INFRA-AND-SPECS.md`  
**배포 스크립트:** `scripts/v1/deploy.ps1`  
**현재 인프라:** `docs/00-SSOT/v1/AWS-INFRA-REPORT.md`  
**작성일:** 2026-03-05 · **갱신:** 2026-03-06

---

## 1. 목표

- **기존 인프라(academy-*, academy-v4-* 등) 정리**
- **SSOT v1 네이밍·구성으로 전면 재배포**
- **최종 상태:** academy-v1-* 리소스만 존재, API ASG + ALB, Batch, RDS, Redis, DynamoDB, EventBridge 등 v1 스펙 충족

---

## 2. 현재 vs V1 요약

| 구분 | 현재 (기존) | V1 목표 |
|------|-------------|---------|
| **API** | EC2 1대 (academy-api, 수동) | academy-v1-api-asg + ALB + TG. **V1:** min/desired/max 2/2/4, 롤링 MinHealthyPercentage=100 |
| **Build** | GitHub Actions(OIDC) only | 빌드 서버 0대. ECR push 후 deploy.ps1로 배포 |
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
  - `rds.dbIdentifier`: **academy-v1-db**. 기존 academy-db를 그대로 쓰지 않으면 새 RDS가 생성됨(스크립트는 RDS 삭제 안 함). **V1:** Performance Insights 7일·Multi-AZ toggle(SSOT). 슬로우 쿼리 등 운영 최소선 활성화.
  - `rds.masterPasswordSsmParam`: `/academy/rds/master_password` 등 SSM SecureString에 마스터 비밀번호 저장 필수.
  - **Bootstrap** 시 SSM 비밀번호·SQS·ECR URI 등 자동 준비 가능하나, RDS/Redis는 Strict 검사에서 실패할 수 있으므로 `-SkipRds -SkipRedis`로 1차 배포 후 수동 생성 가능.

### 3.2 RDS 정책 결정

- **옵션 A:** 새 **academy-v1-db** 생성 후 애플리케이션만 v1 인프라에 연결 (데이터 초기화 또는 별도 마이그레이션).
- **옵션 B:** 기존 **academy-db** 유지 사용 시, params.yaml의 `dbIdentifier`를 academy-db로 변경하고, **DB 서브넷 그룹**을 v1 private 서브넷으로 맞추거나 기존 서브넷 그룹 이름을 params에 반영. (RDS 인스턴스 이름 변경 불가이므로 식별자만 academy-db로 두고 v1 네이밍은 다른 리소스에만 적용.)

### 3.3 AWS 자격 증명

- **.env 자동 로드:** `deploy.ps1`, `verify.ps1`, `bootstrap.ps1`, `cleanup-unused-ec2.ps1`, `aws-diagnose.ps1` 는 실행 시 **프로젝트 루트의 .env를 자동 로드**합니다. 동일 세션에서 수동으로 `.env`를 로드할 필요 없습니다.
- `.env` 형식: `AWS_ACCESS_KEY_ID=...`, `AWS_SECRET_ACCESS_KEY=...`, `AWS_DEFAULT_REGION=ap-northeast-2` (한 줄에 하나씩, `KEY=value`).
- 자격 증명이 없거나 만료되면 **Preflight 단계에서 한글 오류 메시지**로 안내합니다.
- 프로파일 사용 시: `deploy.ps1 -AwsProfile default` 등으로 실행.
- Cursor/자동화에서는 `deploy.ps1 -AwsProfile default` 사용 권장.

### 3.4 빌드/푸시 (GitHub Actions only)

- **정책:** 빌드 서버(EC2) 사용하지 않음(0대).
- **빌드/푸시:** GitHub Actions(OIDC)로 `academy-*` 이미지 빌드 후 ECR 푸시.
- **배포:** `deploy.ps1`는 ECR의 이미지를 pull/refresh만 수행한다.

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
- **주의:** 레거시 단일 EC2(예: academy-api)가 남아있다면 v1 ASG 전환 이후 수동 정리 필요.

### 4.2 수동 정리 (스크립트 미처리)

| 순서 | 대상 | 조치 |
|------|------|------|
| 1 | **EC2 academy-api** (i-0c8ae616abf345fd1) | 필요 시 스냅샷/백업 후 **EIP 해제 → 인스턴스 종료**. v1 배포 후 ALB 뒤 새 API 인스턴스로 대체. |
| 2 | (레거시) 불필요 EC2 | 빌드 서버는 정책상 사용하지 않음. 남아있다면 **종료/삭제**. |
| 3 | **Lambda 2개** (academy-worker-queue-depth-metric, academy-worker-autoscale) | v1 SSOT에 Lambda 없음. 유지할지 **수동 삭제**할지 결정 후 `aws lambda delete-function --function-name <이름> --profile default` |
| 4 | **기존 VPC/서브넷/SG** | v1이 **새 VPC(academy-v1-vpc)** 를 만들면 기존 vpc-0831a2484f9b114c2 등은 나중에 수동 삭제 가능. RDS/ElastiCache가 기존 VPC에 있으면 삭제 시 주의. |

### 4.3 (선택) 전량 Purge 후 재생성

v1 범위 리소스까지 **전부 끄고 다시 만들고 싶을 때**:

```powershell
pwsh scripts/v1/deploy.ps1 -PurgeAndRecreate -AwsProfile default
```

- **동작:** v1 EventBridge 규칙 비활성화·타깃 제거 → v1 Batch Queue 비활성화·삭제 → v1 Batch CE 비활성화·삭제 → v1 JobDef deregister → **academy-v1-api-asg** 삭제(있을 경우) → 이후 스크립트가 **전체 Ensure** 재실행.
- **주의:** RDS·Redis·DynamoDB·ECR 이미지는 **삭제하지 않음**. 네트워크(VPC/서브넷)도 Purge 대상이 아님.

### 4.4 EC2 미사용 리소스 정리 (cleanup-unused-ec2.ps1)

**목적:** v1에서 쓰는 EC2 리소스만 남기고, 미사용 EIP·고아 인스턴스·미사용 보안 그룹을 정리.

**유지 대상**
- **EIP:** 미연결 EIP만 release 후보. (NAT/EIP=0 정책이면 NAT 연동 EIP도 제거 대상)
- **인스턴스:** 다음에 속한 것만 유지  
  - ASG: `academy-v1-api-asg`, `academy-v1-messaging-worker-asg`, `academy-v1-ai-worker-asg`, `academy-v1-video-ops-ce-asg-*`
- **보안 그룹:** `academy-v1-sg-app`, `academy-v1-sg-batch`, `academy-v1-sg-data`, default, 및 ENI가 붙어 있는 SG

**실행 (동일 PowerShell 세션에서 .env 로드 후):**

```powershell
cd C:\academy
Get-Content .env | Where-Object { $_ -match '^AWS_ACCESS_KEY_ID=' -or $_ -match '^AWS_SECRET_ACCESS_KEY=' -or $_ -match '^AWS_DEFAULT_REGION=' } | ForEach-Object { $n,$v = ($_ -split '=',2); if ($n) { [Environment]::SetEnvironmentVariable($n.Trim(), $v.Trim(), 'Process') } }
pwsh scripts/v1/cleanup-unused-ec2.ps1
```

- **기본:** `-DryRun` — 삭제 없이 후보만 출력.
- **실제 삭제:** `-Execute` (미사용 EIP 해제, v1 유지 목록에 없는 인스턴스 종료).
- **옵션:** `-EIPOnly`(EIP만), `-InstancesOnly`(인스턴스만), `-RemoveUnusedSGs`(ENI 없는 SG도 삭제 시도).

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
| 5 | (레거시) 불필요 EC2 종료/삭제 | 수동 | ☐ |
| 6 | Lambda 유지/삭제 결정 및 필요 시 삭제 | 수동 | ☐ |
| 7 | (선택) PurgeAndRecreate 실행 | 스크립트 | ☐ |
| 8 | `deploy.ps1 -Env prod` 실행 (V1 전체 Ensure) | 스크립트 | ☐ |
| 9 | Netprobe·Evidence·수동 검증 | 사람/스크립트 | ☐ |

---

## 8. V1 마스터 설계 반영

| 영역 | 반영 내용 |
|------|-----------|
| **API** | ASG 2/2/4, instance-refresh MinHealthyPercentage=100·InstanceWarmup=300. Gunicorn/ALB 타임아웃 60s 권장. |
| **RDS** | Performance Insights 7일, Multi-AZ toggle. Django/워커 CONN_MAX_AGE=60. |
| **SQS** | DLQ·VisibilityTimeout·dlqMaxReceiveCount SSOT(params messagingWorker/aiWorker). Graceful shutdown·멱등성 유지. |
| **Observability** | params `observability.*`(로그 30일, 알람 threshold). CloudWatch 알람은 SSOT 기반 스크립트 또는 수동 생성 가이드 참조. |
| **검증** | `V1-DEPLOYMENT-VERIFICATION.md` §9·§10, `V1-OPERATIONS-GUIDE.md` §4. Evidence/Drift: 배포 후 `check-v1-infra.ps1` 갱신. |
| **인벤토리/레거시** | `V1-INVENTORY-AND-LEGACY-REMOVAL-PLAN.md`: 제거 후보·안전 삭제 단계·실사용 확인. PruneLegacy 전 반드시 참조. |
| **프론트 배포** | `deploy.ps1 -DeployFront`: build → R2 업로드 → CDN purge → 검증. `scripts/v1/deploy-front.ps1` 호출. params `front.*` SSOT. |

---

## 9. 참조

- **SSOT:** `docs/00-SSOT/v1/SSOT.md`
- **params:** `docs/00-SSOT/v1/params.yaml`
- **스펙:** `docs/00-SSOT/v1/INFRA-AND-SPECS.md`
- **현재 인프라:** `docs/00-SSOT/v1/AWS-INFRA-REPORT.md`
- **배포 스크립트:** `scripts/v1/deploy.ps1`, `scripts/v1/core/prune.ps1`
- **검증:** `scripts/v1/verify.ps1` (Bootstrap → Plan → PruneLegacy → deploy 순서 점검)
- **최종 보고·스크립트 변경:** `docs/00-SSOT/v1/V1-FINAL-REPORT.md` (자격증명·Bootstrap workers env·aws.ps1 프로파일 주입 등)
