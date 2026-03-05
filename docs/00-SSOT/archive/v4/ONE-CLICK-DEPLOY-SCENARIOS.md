# 딸깍 배포 시나리오 — Final Design v1.0 (원테이크 Bootstrap)

**배포 엔트리포인트는 `scripts/v4/deploy.ps1` 단 하나만 사용한다.**  
다른 경로의 deploy 스크립트(레거시 SSH/:latest 방식)는 사용 금지이며, 루트 `deploy.ps1`은 실행 시 throw로 차단된다.

**원테이크 UX:** 사용자가 직접 해야 할 작업은 **없다**.  
한 줄만 실행하면 Bootstrap이 SSM 비밀번호·SQS·RDS engineVersion·ECR URI를 자동 준비한 뒤 Ensure가 수렴한다.

---

## 정상 배포 절차 (사전 준비 없음 · 원테이크)

**사용자 실행:**

```powershell
pwsh scripts/v4/deploy.ps1 -Env prod
```

또는 CI:

```powershell
pwsh -File scripts/v4/deploy.ps1 -Env prod -Ci
```

**Bootstrap(기본 ON)이 자동으로 수행하는 것:**

- **SSM RDS 비밀번호:** `rds.masterPasswordSsmParam` 경로에 파라미터가 없으면 랜덤 비밀번호 생성 후 SecureString 저장. (비밀번호 값은 로그에 출력하지 않음)
- **SQS:** `messagingWorker` / `aiWorker`의 `sqsQueueName`·`sqsQueueUrl`이 비어 있으면 기본 이름(`academy-v4-messaging-queue`, `academy-v4-ai-queue`)으로 큐 생성 또는 기존 큐 URL 조회 후 런타임 변수에 설정. (params.yaml은 수정하지 않음)
- **RDS engineVersion:** `rds.engineVersion`이 비어 있거나 메이저만(예: `15`)이면 `describe-db-engine-versions`로 해당 리전 최신 minor 풀버전(예: `15.16`) 자동 선택 후 `RdsEngineVersionResolved`로 사용.
- **ECR 이미지 URI:** `-EcrRepoUri`를 넘기지 않으면  
  CI면 `GITHUB_SHA`, 로컬이면 `git rev-parse HEAD`, 없으면 ECR 최근 푸시 태그로 resolved.  
  해당 이미지가 ECR에 없으면 Build 서버 자동 기동 후 SSM RunCommand로 빌드·푸시 후 계속 진행.  
  `:latest` 태그는 선택하지 않으며, 사용 시 Strict에서 실패.

**Strict Gate는 Bootstrap 이후에만 평가한다.** Bootstrap이 위 항목을 준비한 뒤 Strict로 검증해, 준비가 실패한 경우에만 throw한다.

**재실행 시:** 동일 상태면 Ensure가 No-op으로 수렴하며, "Idempotent: No changes required." 메시지가 나온다.

- **:latest 배포 금지.** Bootstrap/CI는 immutable 태그만 사용한다. `-EcrRepoUri`에 `:latest`가 포함되면 배포가 실패한다.
- **SSOT는 이름(Name tag)이다.** params.yaml에는 VPC/서브넷/SG 등 **이름**만 정의하며, **ID는 런타임 Discover**로 채워진다. 배포 스크립트가 params.yaml 파일을 수정하지 않는다.
- **예외:** IAM 권한·리전 한도·쿼터는 사용자/계정에서 준비해야 한다. (Preflight에서 계정·리전·SSM 기본 파라미터 등 검사)

---

## 옵션 플래그 요약

| 플래그 | 기본값 | 설명 |
|--------|--------|------|
| `-Bootstrap` | `$true` | SSM/SQS/RDS version/ECR 자동 준비 |
| `-StrictValidation` | `$true` | Bootstrap 이후 Strict 게이트 적용 |
| `-SkipBuild` | `$false` | ECR 이미지 없을 때 Build 서버 빌드 트리거 안 함 (기존 이미지 태그만 사용) |
| `-SkipSqs` | `$false` | SQS 자동 생성/조회 스킵 |
| `-SkipRds` | `$false` | RDS SSM 비밀번호·engineVersion 자동 준비 스킵 |
| `-SkipRedis` | `$false` | Redis 관련 자동 준비 스킵 |
| `-EcrRepoUri <uri>` | (없음) | 지정 시 Bootstrap ECR 단계 스킵하고 해당 URI 사용 |
| `-Plan` | `$false` | AWS 변경 없이 Drift/리포트만 출력 (Bootstrap·Ensure 스킵) |
| `-RelaxedValidation` | `$false` | SQS 스케일링 등 게이트 실패 시 계속 진행 |

---

## 1. 시나리오 A: 완전 신규 리빌드 (New VPC + New RDS + New Redis + All Compute)

### 전제

- 기존 인프라는 **삭제하지 않고 방치**. 새 VPC(`academy-v4-vpc`) 안에 완전 신규 인프라를 만든다.
- `docs/00-SSOT/v4/params.yaml`은 Final Design v1.0 값으로 유지하되, ID 필드는 비워둔다.
  - `network.vpcId: ""`, `networkPublicSubnets`/`networkPrivateSubnets`: `["", ""]`
  - `rds.dbIdentifier: academy-v4-db`, `rds.dbSubnetGroupName: academy-v4-db-subnets`
  - `redis.replicationGroupId: academy-v4-redis`, `redis.subnetGroupName: academy-v4-redis-subnets`
  - `dynamodb.lockTableName: academy-v4-video-job-lock`
  - `api.*`, `messagingWorker.*`, `aiWorker.*`, `videoBatch.*`, `eventBridge.*`는 academy-v4-* 네이밍
- RDS 마스터 비밀번호는 **Bootstrap이 자동 생성**한다. params에 `rds.masterPasswordSsmParam`(또는 기본 `/academy/rds/master_password`)만 있으면 된다.
- ECR 이미지는 **immutable tag**로만 사용. 없으면 Bootstrap이 Build 서버로 빌드·푸시하거나, `-EcrRepoUri`로 지정한다.

### 순서

1. **params.yaml 확인:**  
   - ID 필드는 비우고 **이름**만 유지. ID는 런타임 Discover로 채워진다.  
   - `rds.masterPasswordSsmParam`이 비어 있으면 기본 `/academy/rds/master_password` 사용.

2. **deploy.ps1 한 줄 실행:**

   ```powershell
   # Plan: 실제 생성 없이 Drift/리포트만 확인
   pwsh scripts/v4/deploy.ps1 -Env prod -Plan

   # Apply (원테이크): SSM/SQS/RDS version/ECR 자동 준비 후 전체 Ensure
   pwsh scripts/v4/deploy.ps1 -Env prod
   ```

   **이미지 URI를 직접 지정할 때:**

   ```powershell
   pwsh scripts/v4/deploy.ps1 -Env prod -EcrRepoUri "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:<sha-or-version>"
   ```

   - 내부 Ensure 순서 (요약):
     - `Ensure-Network` (academy-v4-vpc, Subnet 2+2, NAT, RT, sg-app/sg-batch/sg-data)
     - `Confirm-RDSState` (academy-v4-db 신규 생성, `available`까지 대기)
     - `Confirm-RedisState` (academy-v4-redis 신규 생성, `available` + primary endpoint까지 대기)
     - `Ensure-DynamoLockTable` (academy-v4-video-job-lock + TTL)
     - `Ensure-ASGMessaging` / `Ensure-ASGAi` (min=1, max=10, desired clamp, SQS 스케일 정책)
     - `Ensure-VideoCE` / `Ensure-OpsCE` / `Ensure-VideoQueue` / `Ensure-OpsQueue` / JobDef / EventBridge
     - `Ensure-ALBStack` + `Ensure-API` (ALB DNS → `/health` 200까지 대기)

3. **CI:**  
   원테이크로 배포만 할 경우:

   ```powershell
   pwsh scripts/v4/deploy.ps1 -Env prod -Ci
   ```

   이미지 URI를 워크플로에서 넘길 경우:

   ```powershell
   pwsh scripts/v4/deploy.ps1 -Env prod -Ci -EcrRepoUri "${{ needs.build.outputs.ecr_uri }}"
   ```

### 안전하게 갈아엎는 순서

- 리소스는 **Ensure**로만 생성·수정한다. 삭제는 PruneLegacy/PurgeAndRecreate 시에만.
- "완전 신규 리빌드"에서는 기존 VPC/ASG/Batch/RDS/Redis는 그대로 두고, academy-v4-* 네임스페이스만 사용한다.
- 첫 배포는 `-Plan`으로 드리프트와 생성 계획을 확인한 뒤, 같은 커밋에서 바로 Apply를 권장한다.

### 롤백/복구

- 배포 실패 시: Evidence/로그에서 어느 단계에서 실패했는지 확인하고, **같은 커밋으로 재실행**하면 멱등 수렴을 시도한다.
- RDS/Redis/DynamoDB 생성 중간에 실패한 경우:
  - Console에서 상태를 확인(`creating`/`failed`)하고, 불완전 리소스는 그대로 둔 채 재실행하면 Ensure가 ACTIVE/available 상태로 수렴하거나 예외를 다시 보고한다.
- 네트워크 레벨(VPC/Subnet/NAT)을 잘못 설계한 경우:
  - 새 academy-v4-* VPC를 삭제하고 params를 유지한 뒤, 한 번 더 deploy를 실행해 다시 생성하는 방식으로 재시도한다.

---

## 2. 시나리오 B: 기존 리소스 존재 시 마이그레이션

### 전제

- 기존 VPC·Public 서브넷·API(EIP)·ASG·Batch 등이 이미 있음.
- Final Design v1.0으로 옮기려면: 2-tier 도입, ALB 도입, EIP 제거, Workers min=1·스케일 정책·scale-in protection, Batch min/max 10 등.

### 안전한 순서 (권장)

1. **Discovery:**  
   `STEP-A-DISCOVERY-CURRENT-STATE.md`의 Describe 명령으로 현재 VPC/서브넷/라우팅/NAT/SG/ALB/Batch CE/ASG 상태 수집.

2. **params.yaml 백업 후 수정:**  
   - 기존 `vpcId` 유지 또는 새 VPC로 갈 경우 빈 값.  
   - `networkPublicSubnets` / `networkPrivateSubnets`를 기존 서브넷 ID 2개씩으로 채우거나, 새로 만들 예정이면 빈 값.  
   - API: `apiBaseUrl`을 나중에 ALB DNS로 바꿀 예정이면 당장은 기존 EIP URL 유지 가능.  
   - Workers: min=1, max=10, desired=1, scaleInProtection 등 FD v1 값으로 변경.  
   - Batch: minvCpus=0, maxvCpus=10 등.

3. **한 번에 전부 바꾸지 않기:**  
   - **옵션 1:** 네트워크만 먼저 Ensure(Private 서브넷·NAT 추가) → 배포 확인 → API를 ALB로 전환 → Workers 정책 적용 → Batch 서브넷/SG 수렴.  
   - **옵션 2:** params를 FD v1로 맞춘 뒤, deploy 한 번 실행(Step C~D 구현 시 API는 ALB로, EIP는 사용 안 함).

4. **EIP 처리:**  
   - EIP를 canonical에서 제거했으면 params에 `api.identification` 없음.  
   - 기존 EIP 릴리즈는 **선택**. 필요 시 수동으로 disassociate 후 release.

### 롤백/복구

- **API:** ALB 전환 전 상태로 돌리려면 params에서 다시 EIP·apiBaseUrl 넣고, ALB 관련 비우고 재배포(또는 수동으로 EIP 재연결).  
- **Workers:** desired 덮어쓰기를 쓰지 않도록 했으므로, 수동으로 desired 올린 뒤 재배포 시 clamp만 적용됨.  
- **Batch:** CE/Queue는 Ensure로 수렴. 문제 시 PurgeAndRecreate 후 재배포(구현 시 삭제 순서 준수).

---

## 3. Strict 모드에서 자주 실패하는 원인과 해결책

- **Network 단계 실패 (Step C):**
  - 증상: CIDR 겹침, AZ 2개 미만, NAT 가용 상태 전환 실패 등으로 throw.
  - 해결: VPC CIDR/서브넷 CIDR이 계정 내 다른 VPC와 충돌하지 않는지 확인하고, 리전의 AZ 가용성을 확인 후 재실행.

- **RDS 단계 실패 (Step C-2):**
  - 증상: `rds.masterPasswordSsmParam` 파라미터가 없거나 비어 있음, 엔진/인스턴스 클래스 지원 불가, status가 `available`로 오지 않음.
  - 해결: SSM SecureString에 비밀번호를 채우고(`/academy/rds/master_password`), engine/instanceClass를 해당 리전에서 지원되는 값으로 조정 후 재실행.

- **Redis 단계 실패 (Step C-3):**
  - 증상: Subnet Group 생성 실패(Private 서브넷 2개 미만), replication group status가 `available`로 수렴하지 않음.
  - 해결: Ensure-Network가 먼저 성공했는지 확인하고, Redis 파라미터(nodeType/engineVersion)가 리전에서 지원되는지 검증 후 재실행.

- **Workers 단계 실패 (Step E):**
  - 증상: Strict 모드에서 SQS QueueName 파싱 실패, Application Auto Scaling/CloudWatch 권한 문제로 throw.
  - 해결: `messagingWorker.sqsQueueUrl`/`sqsQueueName`, `aiWorker.sqsQueueUrl`/`sqsQueueName`을 올바른 큐로 설정하고, IAM 권한(SQS + Application Auto Scaling + CloudWatch)을 확인. 개발 환경에서는 `-RelaxedValidation`로 완화 가능.

- **Batch 단계 실패 (Video/Ops):**
  - 증상: CE 상태 INVALID, Queue state ENABLED로 수렴하지 않음, JobDef drift 시 register 실패.
  - 해결: IAM 역할·Instance Profile이 올바르게 생성되었는지 확인하고, 실패 이유(statusReason)를 기반으로 조정 후 동일 커밋으로 재실행.

- **API 단계 실패 (Step D):**
  - 증상: ALB DNS 기준 `/health` 200이 일정 시간 내에 나오지 않아 throw.
  - 해결: API 이미지 탑재 여부(ECR 태그), UserData/환경변수(SSM) 설정, RDS/Redis 연결성(sg-data, 서브넷)을 확인 후 재실행.

---

## 3. 실행 커맨드 요약

| 목적 | 로컬 | CI |
|------|------|-----|
| 원테이크 배포 (사전 준비 없음) | `pwsh scripts/v4/deploy.ps1 -Env prod` | `pwsh scripts/v4/deploy.ps1 -Env prod -Ci` |
| Plan (변경 없이 상태만 보기) | `pwsh scripts/v4/deploy.ps1 -Env prod -Plan` | - |
| 이미지 URI 지정 배포 | `pwsh scripts/v4/deploy.ps1 -Env prod -EcrRepoUri <uri>` | `-EcrRepoUri "${{ needs.build.outputs.ecr_uri }}" -Ci` |
| Legacy 리소스 정리 | `pwsh scripts/v4/deploy.ps1 -PruneLegacy` (전에 `-Plan -PruneLegacy`로 후보 확인) | 사용 비권장 |

---

## 4. 검증 체크리스트 (구현 완료 후)

- [ ] API: ALB DNS로 `/health` 200
- [ ] Workers: min 1 인스턴스 InService, 스케일 정책 존재, scale-in protection ON
- [ ] Batch: Job submit → RUNNING → SUCCEEDED
- [ ] Redis: 진행률 키 read/write
- [ ] DynamoDB lock: 동일 videoId 2회 submit 시 두 번째 차단
- [ ] 재실행 시 No-op 수렴
