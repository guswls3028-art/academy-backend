# Final Design v1.0 인프라 구현 요약 (Step C + D + E + G)

## Final Hardening — Clean V4 Only Mode

- **배포 엔트리 단일화:** Academy 인프라 배포는 **`scripts/v4/deploy.ps1` 단 하나**만 사용한다. 루트 `deploy.ps1`(레거시 SSH/:latest 방식)은 실행 시 즉시 throw로 차단된다. GitHub Actions에서도 `scripts/v4/deploy.ps1`만 호출한다.
- **Strict 기본값:** 검증 모드는 기본 **Strict**이다. `-RelaxedValidation` 스위치가 있을 때만 완화된다. params.yaml의 `validationMode`는 무시된다.
- **SQS Scaling Strict 필수:** Strict 모드에서는 `messagingWorker.sqsQueueUrl` 또는 `sqsQueueName`, `aiWorker.sqsQueueUrl` 또는 `sqsQueueName`이 반드시 설정되어 있어야 한다. 비어 있으면 배포 실패. "SQS 설정 없으면 스킵" 로직은 제거되었다.
- **Immutable Tag 강제:** `:latest` 배포 금지. `-EcrRepoUri` 필수이며, 값에 `:latest`가 포함되면 즉시 throw. JobDef 등록 시에도 `:latest` 사용 시 throw. CI에서는 commit SHA 태그 사용.
- **Discover 기반 SSOT:** params.yaml에는 **이름(Name tag)**만 정의한다. VPC/Subnet/SG/RDS/Redis의 **ID는 런타임에 AWS Describe + Name 태그로 재발견**한다. 런타임에 params.yaml을 수정하는 로직은 없다.

---

## 변경된 파일 목록

| 파일 | 변경 내용 |
|------|------------|
| `docs/00-SSOT/v4/params.yaml` | `network`(academy-v4-* 네이밍), `rds`(dbSubnetGroupName, engine, instanceClass, allocatedStorage, masterUsername, masterPasswordSsmParam), `redis`(replicationGroupId, subnetGroupName, nodeType), `messagingWorker`/`aiWorker` SQS 스케일 임계값, `videoBatch`·`eventBridge`·`dynamodb.lockTableName`를 FD1 기준으로 정리 |
| `scripts/v4/core/ssot.ps1` | 네트워크·SG·RDS·Redis·DynamoDB·Workers 스케일 파라미터 로드 |
| `scripts/v4/resources/network.ps1` | 2-tier VPC, IGW, NAT, Public/Private RT, sg-app/sg-batch/sg-data Ensure (academy-v4-* 네이밍) |
| `scripts/v4/resources/rds.ps1` | **신규 구현** — PostgreSQL RDS 인스턴스 신규 생성(Private Subnet, sg-data, SSM 비밀번호), `available` 상태까지 대기 |
| `scripts/v4/resources/redis.ps1` | **신규 구현** — ElastiCache Redis replication group 신규 생성(Private Subnet, sg-data), `available` + primary endpoint까지 대기 |
| `scripts/v4/resources/dynamodb.ps1` | **신규** — `academy-v4-video-job-lock` 테이블 Ensure (PK=videoId, TTL=ttl) |
| `scripts/v4/resources/alb.ps1` | ALB, Target Group, Listener Ensure (ALB DNS → ApiBaseUrl) |
| `scripts/v4/resources/api.ps1` | EIP 제거, ALB Target Group 연동, sg-app, `/health` 기준 대기 |
| `scripts/v4/resources/asg_messaging.ps1` | sg-app LT, desired clamp, scale-in protection, SQS 기반 스케일 정책 (임계값 params화) |
| `scripts/v4/resources/asg_ai.ps1` | 동일 (sg-app, clamp, protection, SQS scaling, 임계값 params화) |
| `scripts/v4/deploy.ps1` | FD1 순서로 Ensure 재정렬 (Network → RDS/Redis → SSM/ECR → DynamoDB → Workers → Batch → API → Build) |

---

## 새로 추가된 함수 목록

### network.ps1
- `Get-FirstTwoAzs`
- `Get-VpcByTagOrId`
- `Ensure-Vpc`
- `Ensure-Subnets`
- `Ensure-InternetGateway`
- `Ensure-NatGateway`
- `Ensure-RouteTables`
- `Ensure-SecurityGroups`
- `Ensure-Network` (진입점)
- `Ensure-NetworkVpc`, `Confirm-SubnetsMatchSSOT` (검증)

### alb.ps1
- `Ensure-ALB`
- `Ensure-TargetGroup`
- `Ensure-Listener`
- `Ensure-ALBStack` (ALB + TG + Listener 통합)

### asg_messaging.ps1
- `Ensure-MessagingSqsScaling` (SQS backlog → Application Auto Scaling + CloudWatch 알람)

### asg_ai.ps1
- `Ensure-AiSqsScaling` (동일)

---

## deploy.ps1 Ensure 순서 (관련 부분, FD1 기준)

```text
Ensure-BatchIAM
Ensure-Network                  ← Step C (2-tier VPC/NAT/SG)
Ensure-NetworkVpc
Confirm-SubnetsMatchSSOT
Confirm-RDSState                ← Step C-2 (신규 RDS Ensure + available 게이트)
Confirm-RedisState              ← Step C-3 (신규 Redis Ensure + available 게이트)
Confirm-SSMEnv
Ensure-ECRRepos
Ensure-DynamoLockTable          ← Step G (DynamoDB video lock 테이블 Ensure)
Ensure-ASGMessaging             ← Step E
Ensure-ASGAi                    ← Step E
Ensure-VideoCE
Ensure-OpsCE
Ensure-VideoQueue
Ensure-OpsQueue
Ensure-VideoJobDef
Ensure-OpsJobDefReconcile
Ensure-OpsJobDefScanStuck
Ensure-OpsJobDefNetprobe
Ensure-EventBridgeRules
Ensure-ALBStack                 ← Step D
Ensure-API
Ensure-Build
```

---

## 실행 커맨드 예시

```powershell
# 전체 적용 (한 번에 C + D + E 포함) — 기본 Strict 검증
pwsh scripts/v4/deploy.ps1 -Env prod

# 첫 배포 권장 (Strict: SQS 스케일링/알람 실패 시 deploy 전체 실패)
pwsh scripts/v4/deploy.ps1 -Env prod

# 개발 편의용: Step E SQS 스케일링 실패해도 계속 진행 (Evidence에 "SQS scaling NOT enforced" 기록)
pwsh scripts/v4/deploy.ps1 -Env prod -RelaxedValidation

# 계획만 보기 (AWS 변경 없음)
pwsh scripts/v4/deploy.ps1 -Env prod -Plan

# 기존 리소스 정리 후 재구성 시 (Guard 출력 후 삭제·재생성)
pwsh scripts/v4/deploy.ps1 -Env prod -PurgeAndRecreate
```

---

## Step E 검증 모드 (Strict / Relaxed)

| 구분 | Strict (기본) | Relaxed |
|------|----------------|---------|
| **설정** | `params.yaml` `global.validationMode: "Strict"` 또는 플래그 없음 | `params.yaml` `global.validationMode: "Relaxed"` 또는 `deploy.ps1 -RelaxedValidation` |
| **RegisterScalableTarget 실패** | deploy 전체 실패 (throw) | Write-Warning 후 계속, Evidence에 "SQS scaling NOT enforced" |
| **PutScalingPolicy 실패** | deploy 전체 실패 | 동일 |
| **PutMetricAlarm 실패** | deploy 전체 실패 | 동일 |
| **알람 alarm-actions가 정책 ARN 미참조** | deploy 전체 실패 | 동일 |
| **ScalableTarget min/max가 SSOT와 불일치** | deploy 전체 실패 | 동일 |
| **QueueName 파싱 실패** | throw | scaling 스킵 후 계속 |

- **첫 배포 권장**: Strict(기본)로 실행해 모든 기능이 실제로 적용되는지 확인.
- **Relaxed**: 로컬/개발에서 큐·권한 미준비 시 deploy는 성공시키고, Evidence/Report에서 SQS scaling 미적용 여부 확인 가능.

---

## 빈 계정에서 배포 시 예상 생성 리소스 목록

### Step C — Network
| 리소스 유형 | 수량 | 이름/설명 |
|-------------|------|-----------|
| VPC | 1 | `academy-v4-vpc` (10.0.0.0/16, Tag Project=academy) |
| Subnet (Public) | 2 | AZ 2개 (`academy-v4-public-a`, `academy-v4-public-b`) |
| Subnet (Private) | 2 | AZ 2개 (`academy-v4-private-a`, `academy-v4-private-b`) |
| Internet Gateway | 1 | VPC attach (`academy-v4-igw`) |
| EIP | 1 | NAT Gateway용 (`academy-v4-nat-eip`) |
| NAT Gateway | 1 | Public Subnet 1개에 생성 (`academy-v4-nat`) |
| Route Table (Public) | 1 | 0.0.0.0/0 → IGW (`academy-v4-public-rt`) |
| Route Table (Private) | 1 | 0.0.0.0/0 → NAT (`academy-v4-private-rt`) |
| Security Group | 3 | `academy-v4-sg-app`, `academy-v4-sg-batch`, `academy-v4-sg-data` |

### Step D — API (ALB + ASG)
| 리소스 유형 | 수량 | 이름/설명 |
|-------------|------|-----------|
| ALB | 1 | internet-facing, Public Subnets (`academy-v4-api-alb`) |
| Target Group | 1 | port 8000, health path `/health` (`academy-v4-api-tg`) |
| Listener | 1 | HTTP 80 → Target Group |
| Launch Template | 1 | API용 (t4g.medium, sg-app, Private Subnets) |
| ASG | 1 | API ASG (min=1, max=2, desired=1, `academy-v4-api-asg`), Target Group 연결 |
| EC2 인스턴스 | 1 | ASG 의해 기동 (Private Subnet), ALB DNS 기반 ApiBaseUrl |

### Step E — Workers ASG
| 리소스 유형 | 수량 | 이름/설명 |
|-------------|------|-----------|
| Launch Template | 2 | Messaging, AI 각 1 (t4g.medium, sg-app) |
| ASG | 2 | `academy-v4-messaging-worker-asg`, `academy-v4-ai-worker-asg` (min=1, max=10, desired clamp, scale-in protection) |
| Application Auto Scaling Target | 2 | ASG당 1 (min=1, max=10) — sqsQueueUrl 설정 시 |
| Scaling Policy | 2×2 | scale-out / scale-in 각 ASG당 2개 (sqsQueueUrl 설정 시, 임계값은 params로 조정 가능) |
| CloudWatch Alarm | 2×2 | SQS ApproximateNumberOfMessagesVisible 기준 (기본 out>20, in==0, sqsQueueUrl 설정 시) |
| EC2 인스턴스 | 2 | Messaging 1 + AI 1 (최소) |

### Step G — DynamoDB Lock

| 리소스 유형 | 수량 | 이름/설명 |
|-------------|------|-----------|
| DynamoDB Table | 1 | `academy-v4-video-job-lock` (PK=videoId, TTL=ttl, PAY_PER_REQUEST) |

### 기타 (기존 Ensure)
- IAM 역할/정책 (Batch 등)
- ECR 리포지토리
- RDS/ElastiCache SG 규칙
- SSM 파라미터
- Batch CE/Job Queue/Job Definition, EventBridge 규칙 등

---

## 게이트 조건 요약

| 단계 | 게이트 조건 |
|------|-------------|
| Step C | VPC 존재, Public 2 / Private 2 서브넷 존재, NAT 상태 available, SG 3개 존재 |
| Step D | ALB DNS 조회 가능, `/health` 200 응답 |
| Step E | InService 인스턴스 수 ≥ min, (sqsQueueUrl 설정 시) Scaling policy 존재 |

SQS 기반 스케일 정책은 `params.yaml`의 `messagingWorker.sqsQueueUrl`/`sqsQueueName`, `aiWorker.sqsQueueUrl`/`sqsQueueName` 중 하나가 유효할 때 적용된다. **QueueName**은 `sqsQueueName`이 있으면 그 값, 없으면 **QueueUrl의 마지막 path segment**로 확정 파싱한다 (파싱 실패 시 Strict에서는 throw, Relaxed에서는 scaling 스킵).

### Step E 실행 안정화 (3가지 케이스)

| 케이스 | params 설정 | deploy 동작 |
|---|---|---|
| **SQS URL 비어 있음** | `sqsQueueUrl=""` 이고 `sqsQueueName=""` | **SQS scaling 완전 스킵**. 대신 ASG/LT Ensure + desired clamp + scale-in protection은 정상 수행 |
| **SQS URL 있으나 권한 없음/큐 미존재/메트릭 누락** | URL 또는 Name 설정됨 | SQS scaling 구간에서 **try/catch로 경고 출력 후 스킵**, deploy는 계속 진행. ASG/LT Ensure + desired clamp + scale-in protection은 반드시 수행 |
| **정상** | URL 또는 Name 설정됨 + 권한/큐/메트릭 정상 | Application Auto Scaling(타겟+정책) + CloudWatch 알람(2개)까지 Ensure로 수렴. `TreatMissingData=notBreaching`로 메트릭 누락 시 불필요한 scale-out 방지 및 INSUFFICIENT_DATA 방지 |
