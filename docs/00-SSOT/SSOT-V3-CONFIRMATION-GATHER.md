# SSOT v3 확정을 위한 정보 수집 결과 (1단계)

**목적:** 레거시 없이 Infra SSOT v3를 확정하기 위해 필요한 사실/값을 한 번에 수집·정리.  
**제한:** 코드/파일 이동/삭제/수정 없음. 분석·정리·명령 생성만 수행.

---

## P0 강제사항 반영 요약

| P0 | 내용 | 현재 상태 |
|----|------|-----------|
| **P0-A** | Legacy execution kill-switch | CI가 `scripts/infra/batch_video_setup.ps1` 등 직접 호출. EventBridge 단계는 `-JobQueueName` 전달(스크립트는 `-OpsJobQueueName`만 정의 → 기본값 academy-video-ops-queue 사용). Denylist + Entrypoint only 계약 미적용. |
| **P0-B** | Image immutability = digest | workflow에서 `:latest` 사용(video_batch_deploy.yml 60–61행, build-and-push-ecr*.yml 전부). JobDef drift/Evidence에 digest 없음. |
| **P0-C** | Env 단일소스 + 배포 시퀀스 | SSM은 `/academy/workers/env`, `/academy/api/env` 2개 사용. common/api/workers 3계층 미적용. 배포 순서가 state-contract에 고정되지 않음. |

---

## 작업 1) 레포 “진실의 단서” 수집

### A) 리소스 이름 후보 및 등장 위치

| 리소스 유형 | 후보 값 | 등장 파일/라인 | 비고 |
|-------------|---------|----------------|------|
| Video CE | academy-video-batch-ce-final | infra_full_alignment_public_one_take.ps1:36, INFRA-SSOT-V3.*, RESOURCE-INVENTORY | 원테이크·SSOT 채택 |
| Video CE | academy-video-batch-ce | recreate_batch_in_api_vpc.ps1:10 기본값, batch_video_setup.ps1:16 기본값 | 레거시 기본값 |
| Video CE | academy-video-batch-ce-v3 | docs/01-ARCHITECTURE/VIDEO_WORKER_SCALING_SSOT.md:14 | 문서만, 미사용 |
| Video Queue | academy-video-batch-queue | 전역 사용 | 충돌 없음 |
| Ops CE | academy-video-ops-ce | batch_ops_setup, eventbridge_deploy, INFRA-SSOT-V3 | 충돌 없음 |
| Ops Queue | academy-video-ops-queue | eventbridge_deploy:9, run_netprobe:4, INFRA-SSOT-V3 | 충돌 없음 |
| Worker JobDef | academy-video-batch-jobdef | recreate_batch:11, INFRA-SSOT-V3 | 충돌 없음 |
| Ops JobDefs | academy-video-ops-reconcile, -scanstuck, -netprobe | eventbridge_deploy:26, INFRA-SSOT-V3 | 충돌 없음 |
| EventBridge 규칙 | academy-reconcile-video-jobs, academy-video-scan-stuck-rate | eventbridge_video_rule_names.ps1, eventbridge_deploy | 충돌 없음 |
| EventBridge IAM 역할 | academy-eventbridge-batch-video-role | eventbridge_deploy_video_scheduler.ps1:95 | 확정 |
| Messaging ASG | academy-messaging-worker-asg | deploy_worker_asg, deploy_preflight, INFRA-SSOT-V3 | 충돌 없음 |
| Messaging LT | academy-messaging-worker-lt | INFRA-SSOT-V3, deploy_worker_asg | 충돌 없음 |
| AI ASG | academy-ai-worker-asg | deploy_worker_asg, INFRA-SSOT-V3 | 충돌 없음 |
| AI LT | academy-ai-worker-lt | INFRA-SSOT-V3 | 충돌 없음 |
| SSM workers | /academy/workers/env | ssm_bootstrap_video_worker.ps1:23, verify_ssm_env_shape, batch_entrypoint.py, API_ENV_DEPLOY_FLOW | 충돌 없음 |
| SSM api | /academy/api/env | verify_ssm_api_env.ps1:9, upload_env_to_ssm.ps1:10, API_ENV_DEPLOY_FLOW | 충돌 없음 |
| RDS | academy-db | recreate_batch_in_api_vpc.ps1:9, discover_rds_network, INFRA-SSOT-V3 | 충돌 없음 |
| Redis | academy-redis, academy-redis-subnets, academy-redis-sg | setup_elasticache_redis.ps1, INFRA-SSOT-V3 | 충돌 없음 |
| ECR | academy-video-worker, academy-api, academy-messaging-worker, academy-ai-worker-cpu, academy-base | workflow, params.yaml | 충돌 없음 |

### B) 충돌 목록

| 리소스 | A 측 | B 측 | 해결 방향 |
|--------|------|------|-----------|
| Video CE 이름 | academy-video-batch-ce-final (원테이크·SSOT) | academy-video-batch-ce (recreate_batch, batch_video_setup 기본값) | **Canonical: ce-final.** CI/스크립트 호출 시 항상 -ComputeEnvName academy-video-batch-ce-final 전달. 레거시 스크립트 직접 호출 제거 후 scripts_v3만 사용 시 인자 통일. |
| EventBridge 대상 | 원테이크: -OpsJobQueueName academy-video-ops-queue | CI: -JobQueueName "${{ env.JOB_QUEUE_NAME }}" (academy-video-batch-queue) | 스크립트에 -JobQueueName 파라미터 없음 → 기본값 Ops queue 사용. **Canonical:** CI에서 명시적으로 -OpsJobQueueName academy-video-ops-queue 전달. |

### C) SSOT v3 Canonical 값 (params.yaml에 넣을 1개)

- **computeEnvironmentName (Video):** academy-video-batch-ce-final  
- **videoQueueName:** academy-video-batch-queue  
- **opsQueueName:** academy-video-ops-queue  
- **opsComputeEnvironmentName:** academy-video-ops-ce  
- **workerJobDefName:** academy-video-batch-jobdef  
- **reconcileRuleName:** academy-reconcile-video-jobs  
- **scanStuckRuleName:** academy-video-scan-stuck-rate  
- **eventBridgeRoleName:** academy-eventbridge-batch-video-role  
- **ssm workersEnv:** /academy/workers/env  
- **ssm apiEnv:** /academy/api/env  
- **dbIdentifier:** academy-db  
- **redis replicationGroupId:** academy-redis  

### D) 확정 불가 시 TODO + 확인 방법

| 항목 | 확인 방법 |
|------|-----------|
| AccountId | `aws sts get-caller-identity --query Account --output text` |
| Public Subnet ID 목록 | `scripts/infra/discover_api_network.ps1` 실행 후 docs/02-OPERATIONS/actual_state/api_instance.json 또는 스크립트 출력 |
| API SG / Batch SG | `aws ec2 describe-security-groups --filters Name=vpc-id,Values=vpc-0831a2484f9b114c2 --region ap-northeast-2 --query "SecurityGroups[*].[GroupName,GroupId]"` |
| ALB/TG 사용 여부 | `aws elbv2 describe-load-balancers --region ap-northeast-2`, `scripts/check_api_alb.ps1` |
| R2 bucket / CDN 도메인 | .env 또는 SSM R2_VIDEO_BUCKET, R2_PUBLIC_BASE_URL (외부 관리) |

---

## 작업 2) CI 경로 분석 + 레거시 호출 탐지

### Workflow별 pwsh 호출 목록

| Workflow | Step 이름 | 호출 스크립트 | 레거시 직접 호출 여부 |
|----------|-----------|----------------|------------------------|
| video_batch_deploy.yml | Batch video setup | scripts/infra/batch_video_setup.ps1 | **예** |
| video_batch_deploy.yml | EventBridge deploy video scheduler | scripts/infra/eventbridge_deploy_video_scheduler.ps1 | **예** |
| video_batch_deploy.yml | CloudWatch deploy video alarms | scripts/infra/cloudwatch_deploy_video_alarms.ps1 | **예** |
| build-and-push-ecr.yml | (run step 없음, uses만) | — | 레거시 ps1 직접 호출 없음 |
| build-and-push-ecr-nocache.yml | (동일) | — | 레거시 ps1 직접 호출 없음 |

**레거시로 간주할 스크립트(진입점 통일 시 호출 금지):**

- scripts/infra/batch_video_setup.ps1  
- scripts/infra/recreate_batch_in_api_vpc.ps1 (진입점은 scripts_v3에서만 호출 허용 시 예외 가능)  
- scripts/infra/batch_ops_setup.ps1  
- scripts/infra/eventbridge_deploy_video_scheduler.ps1  
- scripts/infra/cloudwatch_deploy_video_alarms.ps1  
- scripts/infra/ssm_bootstrap_video_worker.ps1  
- scripts/infra/run_netprobe_job.ps1  
- scripts/deploy_preflight.ps1  

**진입점으로만 허용:** scripts_v3/deploy.ps1 (구현 후).

### SSOT v3 Kill-Switch 설계안

**안1) Workflow에 denylist 검사 step 추가**

- 배포 job 시작 시 step 하나 추가.
- `scripts/infra/*.ps1`, `scripts/*legacy*` 등 denylist에 있는 경로가 workflow 파일 내 run: / pwsh -File / -Command 인자에 문자열로 포함되면 fail.
- 예: `grep -E 'scripts/infra/|scripts/.*legacy' .github/workflows/*.yml` 후 비어 있지 않으면 exit 1.
- **장점:** CI가 레거시를 호출하는 순간 즉시 실패. **단점:** workflow만 검사하므로 로컬에서 레거시 직접 실행은 막지 못함.

**안2) 레거시 스크립트 최상단에 deprecated guard**

- 레거시 ps1 상단에서 “직접 실행”이면 throw. 예: `if ($MyInvocation.InvocationName -ne 'DotSourced' -and -not $env:ALLOW_LEGACY_IMPORT) { throw 'DEPRECATED: Use scripts_v3/deploy.ps1' }`.
- dot-sourcing 또는 `$env:ALLOW_LEGACY_IMPORT=1`로 scripts_v3에서 호출할 때만 허용.
- **장점:** 로컬/CI 불문하고 직접 실행 시 즉시 실패. **단점:** 모든 레거시 파일 수정 필요, dot-source 시 변수/스코프 주의.

**판단:**  
- **안1을 필수**로 적용: CI가 레거시를 부르면 무조건 fail.  
- **안2는 선택**: 로컬 실수 방지용. scripts_v3가 레거시를 dot-source하지 않고 전부 재구현하면 안2 없이도 가능.

**SSOT 계약 문구 제안:**  
“배포 진입점은 scripts_v3/deploy.ps1 단일. CI에서는 scripts_v3/deploy.ps1만 호출한다. scripts/infra/*.ps1, scripts/*legacy* 등 denylist 경로를 CI step에서 직접 실행하면 빌드 실패.”

---

## 작업 3) 이미지 정책 확정 — digest 흐름

### 현재 workflow 이미지 태그

- **video_batch_deploy.yml:** `ecr_uri=${REGISTRY}/${ECR_REPO}:latest` (60행), ECR_URI에 `:latest` (61행).  
- **build-and-push-ecr.yml / build-and-push-ecr-nocache.yml:** 모든 이미지가 `:latest`.

→ **모든 워크플로에서 :latest 사용 중.** P0-B 위반.

### Immutable tag 전환 설계

1. **빌드 시:** tag를 `git rev-parse --short HEAD` 또는 `${{ github.sha }}` 앞 12자로 고정. 예: `academy-video-worker:$(git rev-parse --short HEAD)`.  
2. **푸시:** 해당 태그만 푸시 (latest 푸시 제거).  
3. **배포 단계:** ECR에서 해당 태그의 **digest** 조회 후, JobDef에는 `repo@sha256:<digest>` 또는 최소한 Evidence에 digest 기록.

### ECR digest 조회 (복붙 가능)

```bash
# REGION, REPO, TAG 설정 후 실행
REGION=ap-northeast-2
REPO=academy-video-worker
TAG=latest
# 또는 immutable: TAG=$(git rev-parse --short HEAD)

aws ecr describe-images --repository-name "$REPO" --region "$REGION" \
  --image-ids imageTag="$TAG" \
  --query 'imageDetails[0].imageDigest' --output text
```

- **이미지가 여러 개일 때(같은 tag에 여러 digest):**  
  `--query 'sort_by(imageDetails,& imagePushedAt)[-1].imageDigest' --output text`

### SSOT v3 Evidence 규칙 (digest)

- **Evidence 표에 필수 포함:**  
  - `videoWorkerImageDigest`: ECR describe-images로 조회한 academy-video-batch-jobdef에 사용된 이미지의 digest.  
  - `videoWorkerImageUri`: 배포에 사용한 이미지 URI (repo:tag 또는 repo@sha256:digest).  
- **JobDef drift 판단:**  
  - 기대값: vcpus, memory, **image digest**(또는 repo@sha256:xxx), retryStrategy, timeout.  
  - 현재 JobDef의 containerProperties.image와 ECR digest 비교. digest가 다르면 revision 등록 또는 경고.

### JobDef drift 판단 기준 표

| 항목 | 비교 기준 | Evidence 출력 |
|------|-----------|----------------|
| image | ECR digest (우선). tag만 있으면 describe-images로 digest 조회 후 비교 | imageDigest, imageUri |
| vcpus | 숫자 일치 (SSOT: 2) | vcpus |
| memory | 숫자 일치 (SSOT: 3072) | memory |
| retryStrategy.attempts | 1 | retryAttempts |
| timeout | 14400 (초) | timeout |

---

## 작업 4) SSM env 단일소스 + 배포 시퀀스 확정

### 현재 SSM 사용 (레포 근거)

| 파라미터 | 용도 | 작성 주체 | 검증 스크립트 |
|----------|------|-----------|----------------|
| /academy/workers/env | Batch·ASG 워커 공통 env (JSON) | ssm_bootstrap_video_worker.ps1 (.env → SSM) | verify_ssm_env_shape.ps1 |
| /academy/api/env | API 서버 env (전체 .env 형식) | upload_env_to_ssm.ps1 (.env → SSM) | verify_ssm_api_env.ps1 |

- upload_env_to_ssm.ps1은 동일 .env를 **두 파라미터 모두** 덮어씀 (workers + api).  
- API env 스키마는 문서(API_ENV_DEPLOY_FLOW, verify_ssm_api_env)에만 있고, workers만 SSM_JSON_SCHEMA.md로 정의됨.

### 권장 3계층 전환 설계

| 계층 | 제안 경로 | 내용 | 마이그레이션 |
|------|------------|------|--------------|
| common | /academy/env/common | DB_*, REDIS_*, R2_*, AWS_DEFAULT_REGION 등 공통 | 기존 workers/env에서 공통 키만 추출해 넣고, api/workers는 common 참조 또는 병합 규칙으로 로드 |
| api | /academy/env/api | API 전용 (DJANGO_SETTINGS_MODULE=api, INTERNAL_API_ALLOW_IPS 등) | 기존 /academy/api/env 값을 옮긴 뒤 구 경로 deprecated |
| workers | /academy/env/workers | 워커 전용 (DJANGO_SETTINGS_MODULE=worker, INTERNAL_WORKER_TOKEN 등) | 기존 /academy/workers/env 값을 옮긴 뒤 구 경로 deprecated |

**마이그레이션 단계 제안:**

1. **Phase 1:** 기존 2개 파라미터 유지. SSOT v3 배포 시퀀스만 고정 (아래 순서).  
2. **Phase 2:** common/api/workers 3개 파라미터 생성, 배포 스크립트가 3개 읽어 병합하도록 변경.  
3. **Phase 3:** 구 /academy/api/env, /academy/workers/env 읽는 코드 제거 후 deprecated 표시.

### SSM schema validation (필수 키)

**workers (기존 SSM_JSON_SCHEMA.md):**  
AWS_DEFAULT_REGION, DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT, R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_VIDEO_BUCKET, API_BASE_URL, INTERNAL_WORKER_TOKEN, REDIS_HOST, REDIS_PORT, DJANGO_SETTINGS_MODULE (값: apps.api.config.settings.worker).

**검증용 AWS CLI (복붙 가능):**

```bash
# 1) 존재 여부
aws ssm get-parameter --name "/academy/workers/env" --region ap-northeast-2 --with-decryption --query Parameter.Value --output text > /dev/null && echo "EXISTS" || echo "MISSING"

# 2) 값 길이(형식 검증은 로컬 스크립트 권장)
aws ssm get-parameter --name "/academy/workers/env" --region ap-northeast-2 --with-decryption --query Parameter.Value --output text | wc -c
```

실제 JSON 키 검증은 `scripts/infra/verify_ssm_env_shape.ps1`와 동일 로직을 scripts_v3에 구현하는 것을 권장.

### SSOT v3 배포 시퀀스 (고정)

1. **SSM desired env 확정** — Describe(현재 SSM) + validate(schema). 실패 시 즉시 중단.  
2. **Workers deploy** — SSM 반영 후 ASG/Batch 워커 배포 (이미지/설정 적용).  
3. **API deploy** — api env sync + container recreate (또는 api 전용 배포).  
4. **Netprobe** — Ops queue 테스트 job 제출 → status SUCCEEDED 대기.  
5. **API health check** — GET /health → 200.  
6. **Evidence 표 출력** — state-contract 정의 항목 전부 출력.

---

## 작업 5) AWS “현재 상태” 확인용 CLI 묶음

아래를 **한 번에 복붙해 실행 가능한 블록**으로 정리. 결과는 SSOT 확정 및 params.yaml 채우는 데 사용.

### Batch CE/Queue/JobDef

```bash
REGION=ap-northeast-2

# Video CE
aws batch describe-compute-environments --compute-environments academy-video-batch-ce-final --region $REGION --output json
# 읽을 필드: computeEnvironments[0].status, .state, .computeEnvironmentArn

# Ops CE
aws batch describe-compute-environments --compute-environments academy-video-ops-ce --region $REGION --output json
# 읽을 필드: computeEnvironments[0].status, .state, .computeEnvironmentArn

# Video Queue
aws batch describe-job-queues --job-queues academy-video-batch-queue --region $REGION --output json
# 읽을 필드: jobQueues[0].state, .jobQueueArn

# Ops Queue
aws batch describe-job-queues --job-queues academy-video-ops-queue --region $REGION --output json
# 읽을 필드: jobQueues[0].state, .jobQueueArn

# Video JobDef (최신 ACTIVE revision)
aws batch describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE --region $REGION --output json
# 읽을 필드: jobDefinitions[0].revision, .containerProperties.vcpus, .memory, .image
```

### EventBridge

```bash
# Reconcile rule
aws events describe-rule --name academy-reconcile-video-jobs --region $REGION --output json
# 읽을 필드: State, ScheduleExpression

aws events list-targets-by-rule --rule academy-reconcile-video-jobs --region $REGION --output json
# 읽을 필드: Targets[0].Arn (Queue ARN), .BatchParameters.JobDefinition

# Scan-stuck rule
aws events describe-rule --name academy-video-scan-stuck-rate --region $REGION --output json
aws events list-targets-by-rule --rule academy-video-scan-stuck-rate --region $REGION --output json
```

### ASG / Launch Template

```bash
# Messaging ASG
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-messaging-worker-asg --region $REGION --output json
# 읽을 필드: AutoScalingGroups[0].DesiredCapacity, .MinSize, .MaxSize, .LaunchTemplate.Version

# AI ASG
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-ai-worker-asg --region $REGION --output json
```

### ALB / Target Group

```bash
aws elbv2 describe-load-balancers --region $REGION --output json
# 필터: Name=academy 또는 태그로 API ALB 식별

aws elbv2 describe-target-groups --region $REGION --output json
aws elbv2 describe-target-health --target-group-arn <TG_ARN> --region $REGION --output json
# /health 타깃 상태 확인
```

### SSM

```bash
aws ssm get-parameter --name "/academy/workers/env" --region $REGION --with-decryption --query Parameter.Value --output text
# 값 shape 검증은 verify_ssm_env_shape.ps1 또는 동일 로직

aws ssm get-parameter --name "/academy/api/env" --region $REGION --with-decryption --query Parameter.Name --output text
# 존재 여부만: --query Parameter.Name
```

### RDS / Redis

```bash
aws rds describe-db-instances --db-instance-identifier academy-db --region $REGION --output json
# 읽을 필드: DBInstances[0].Endpoint.Address, .DBInstanceStatus

aws elasticache describe-replication-groups --replication-group-id academy-redis --region $REGION --output json
# 읽을 필드: ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint
```

### ECR image digest

```bash
REPO=academy-video-worker
TAG=latest
aws ecr describe-images --repository-name $REPO --region $REGION --image-ids imageTag=$TAG --query 'imageDetails[0].imageDigest' --output text
```

---

## 최종 출력 요약

### 1) SSOT v3 확정에 필요한 Canonical 값 목록 (params.yaml 형태)

- 위 “C) SSOT v3 Canonical 값” 참조.  
- network.publicSubnets, securityGroups.api, securityGroups.batch, global.accountId, r2.bucket, r2.publicBaseUrl 는 AWS CLI 묶음 실행 결과로 채움.

### 2) 충돌/누락 P0 목록 (즉시 반영할 것)

| P0 | 항목 | 조치 |
|----|------|------|
| P0-A | CI가 레거시 스크립트 직접 호출 | workflow에서 batch_video_setup, eventbridge_deploy, cloudwatch_deploy 제거 → scripts_v3/deploy.ps1만 호출. Denylist step 추가. |
| P0-A | EventBridge step 파라미터 | CI에서 -OpsJobQueueName academy-video-ops-queue 명시 (현재는 기본값에 의존). |
| P0-B | 이미지 :latest | 모든 workflow에서 tag를 git-sha로 변경, ECR digest 조회 후 Evidence 및 JobDef drift에 digest 사용. |
| P0-C | 배포 시퀀스 | state-contract에 “1) SSM validate → 2) workers → 3) api sync/recreate → 4) netprobe → 5) api health → 6) evidence” 고정. |
| P0-C | SSM schema 실패 시 중단 | 배포 시작 시 SSM validate 실행, 실패 시 exit 1. |

### 3) 지금 실행할 AWS CLI 묶음 (복붙용)

위 “작업 5)” 섹션의 블록들을 순서대로 실행. Region=ap-northeast-2 가정.  
출력 결과를 저장한 뒤, publicSubnets, SG ID, ALB/TG 유무, accountId, RDS/Redis 엔드포인트, ECR digest를 params.yaml 및 actual_state 문서에 반영.

### 4) 다음 단계(구현 프롬프트) 체크리스트

- [ ] 위 AWS CLI 묶음 실행 후 Canonical 값 중 “확정 불가” 항목 채움 (accountId, subnets, SG, ALB 여부 등).  
- [ ] params.yaml에 채운 값 반영.  
- [ ] 2단계 “구현 프롬프트” 실행: 문서(INFRA-SSOT-V3.*) 갱신, scripts_v3 구현, CI 전환, 레거시 denylist, 이미지 digest·Evidence, SSM 3계층 설계·배포 시퀀스 강제.
