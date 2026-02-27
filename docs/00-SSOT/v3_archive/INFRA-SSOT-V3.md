# INFRA SSOT v3 — 전체 인프라 단일 기준 문서

**역할:** 이 문서가 **유일한 기준(SSOT)**. 다른 문서는 참고/아카이브. 모든 스크립트는 이 SSOT의 값만 사용한다.

---

## 0) SSOT 선언

- 이 문서가 **전체 인프라의 단일 기준(SSOT)** 이다.
- 다른 문서(docs/01-ARCHITECTURE, 02-OPERATIONS, 03-REPORTS, archive)는 **참고/아카이브** 용도다.
- 모든 배포/검증 스크립트는 **이 SSOT의 리소스 이름·파라미터·순서**만 사용한다.
- 기계용 값은 **INFRA-SSOT-V3.params.yaml** 에서 읽는다. 멱등 규칙·Wait·Evidence 계약은 **INFRA-SSOT-V3.state-contract.md** 를 따른다.

---

## 1) 아키텍처 한 장 요약

### 구성요소

| # | 컴포넌트 | 형태 | 비고 |
|---|----------|------|------|
| 1 | **API 서버** | EC2 1대 + Elastic IP 고정, Docker `academy-api` | Public Subnet. SSM `/academy/api/env` 또는 .env. ALB 사용 여부는 환경별(확인 필요). |
| 2 | **빌드 서버** | EC2 (Tag: academy-build-arm64) | 이미지 빌드·ECR 푸시. Public Subnet, SSM·STS 검증. |
| 3 | **Messaging Worker** | ASG (academy-messaging-worker-asg) | EC2, SQS 소비. Launch Template + UserData. |
| 4 | **Video Worker** | AWS Batch | 단일 CE(academy-video-batch-ce-final), Video Queue, JobDef. 영상 인코딩 → R2. |
| 5 | **Reconcile 1개** | Batch Ops Queue + EventBridge | reconcile(15분) / scan_stuck(5분) → Ops Queue. Netprobe로 검증. |
| 6 | **AI Worker** | ASG (academy-ai-worker-asg) | EC2, SQS 소비. Launch Template. |
| 7 | **DB** | RDS (academy-db) | PostgreSQL. API/Batch/워커 접근. |
| 8 | **Redis** | ElastiCache (academy-redis) | Replication Group. API/워커 공유. |
| 9 | **Storage/CDN** | Cloudflare R2 + CDN | 프로비저닝은 레포 밖. 설정만 SSM/.env (R2_*, R2_PUBLIC_BASE_URL 등). |

### 데이터 흐름 요약

- **API** ↔ RDS, Redis, R2(S3 호환), SQS(Messaging/Delete).
- **Video Batch:** API가 Job 제출 → Video Queue → Video CE → SSM env → DB/Redis/R2/API_BASE_URL.
- **Ops Batch:** EventBridge → Ops Queue → Ops CE → reconcile/scan_stuck/netprobe.
- **R2:** 워커 HLS 업로드; 삭제는 SQS → Lambda 등 비동기.

---

## 2) 환경/계정/리전/VPC 고정 표

| 항목 | prod 값 | 확인 방법 |
|------|---------|-----------|
| **Region** | ap-northeast-2 | |
| **AccountId** | (배포 시 확인) | `aws sts get-caller-identity --query Account --output text` |
| **VPC (prod)** | vpc-0831a2484f9b114c2 | `scripts/infra/discover_api_network.ps1` 또는 `docs/02-OPERATIONS/actual_state/api_instance.json` |
| **Public Subnet** | (API/Build/Batch와 동일 VPC 내 0.0.0.0/0→IGW 서브넷) | `aws ec2 describe-route-tables --filters Name=vpc-id,Values=$VpcId` → IGW 연결된 서브넷 |
| **Private Subnet** | 확인 필요(일부 문서에 subnet-049e711f41fdff71b 등) | describe-subnets, describe-route-tables. API가 Private이면 해당 서브넷. |
| **Route/NAT/IGW** | Public 모델: IGW만. NAT 사용 안 함(원테이크 PUBLIC_V2 기준). | describe-internet-gateways, describe-route-tables |
| **VPC Endpoints** | Batch용: ECR, ECS, logs, SSM 등 필요 시 설정. | `scripts/infra/setup_video_batch_vpc_endpoints.ps1` 또는 describe-vpc-endpoints |
| **Naming** | 리소스 이름은 아래 서비스별 표 준수. 태그 Project=academy 등 선택. | |

---

## 3) 리소스 인벤토리 (서비스별)

### API

| 리소스 | 이름/식별 | 비고 |
|--------|-----------|------|
| EC2 | Tag Name=academy-api (또는 Elastic IP로 연동) | describe-instances, describe-addresses |
| Elastic IP | 15.165.147.157 (prod) | actual_state/api_instance.json |
| API_BASE_URL | http://15.165.147.157:8000 | 트레일링 슬래시 없음 |
| SSM Parameter | /academy/api/env | API 전용 env. REFERENCE·배포.md 참조. |
| ALB/Target Group | **확인 필요.** 운영.md에 "ALB, Target Group /health" 언급. | `scripts/check_api_alb.ps1`, describe-load-balancers, describe-target-groups |
| SG | API용 SG (Batch SG→API 8000 허용 등) | describe-security-groups (tag 또는 academy-api 인스턴스 연동) |
| Logs/Alarms | (선택) CloudWatch 로그 그룹 | 확인 필요 |

### Build Server

| 리소스 | 이름/식별 | 비고 |
|--------|-----------|------|
| EC2 | Tag Name=academy-build-arm64 | describe-instances filter |
| Subnet | Public Subnet (API와 동일 VPC) | SSM·STS 검증 필수 |
| IAM Role | EC2 인스턴스 프로파일(ECR push 권한 등) | 확인: describe-instances → IamInstanceProfile |
| 빌드/푸시 | 이미지 태그: **immutable SHA**. `:latest` 금지. | build_and_push_ecr.ps1, build_and_push_ecr_on_ec2.sh |

### Messaging Worker (ASG)

| 리소스 | 이름/식별 | 비고 |
|--------|-----------|------|
| ASG Name | academy-messaging-worker-asg | deploy_worker_asg.ps1, deploy_preflight.ps1 |
| Launch Template | academy-messaging-worker-lt | UserData: ECR pull, docker run |
| Desired/Min/Max | Desired 유지(0으로 덮어쓰기 금지) | IDEMPOTENCY-RULES |
| Scaling policies | Application Auto Scaling (SQS queue depth 등) | fix_all_worker_scaling_policies.ps1, check_all_worker_scaling_policies.ps1 |
| SSM env | /academy/workers/env (Batch와 동일 파라미터명, 값 공유 가능) | |
| Instance Tag | Name=academy-messaging-worker | |

### Video Worker (Batch)

| 리소스 | 이름 | 비고 |
|--------|------|------|
| Compute Environment | academy-video-batch-ce-final | 단일 CE. MANAGED EC2, c6g.large, min=0 max=32, Public Subnet. |
| Video Job Queue | academy-video-batch-queue | CE 단일 연결 |
| Job Definition (worker) | academy-video-batch-jobdef | 이름만 사용, revision 하드코딩 금지. vcpus=2, memory=3072, timeout=14400, retry=1. |
| CloudWatch Logs | /aws/batch/academy-video-worker | batch_video_setup.ps1 |
| IAM (Batch) | 아래 IAM 표 참조 | |

### Ops Batch (Reconcile / Scan Stuck / Netprobe)

| 리소스 | 이름 | 비고 |
|--------|------|------|
| Compute Environment | academy-video-ops-ce | default_arm64, min=0 max=2, Public Subnet |
| Job Queue | academy-video-ops-queue | CE 단일 연결 |
| Job Definition (reconcile) | academy-video-ops-reconcile | timeout 900, vcpus=1, memory=2048, retry=1 |
| Job Definition (scan_stuck) | academy-video-ops-scanstuck | timeout 900, vcpus=1, memory=2048, retry=1 |
| Job Definition (netprobe) | academy-video-ops-netprobe | timeout 120, vcpus=1, memory=512, retry=1 |
| Logs | /aws/batch/academy-video-ops | |

### EventBridge

| 리소스 | 이름 | Schedule | Target |
|--------|------|----------|--------|
| Reconcile rule | academy-reconcile-video-jobs | rate(15 minutes) | academy-video-ops-queue, JobDef academy-video-ops-reconcile |
| Scan stuck rule | academy-video-scan-stuck-rate | rate(5 minutes) | academy-video-ops-queue, JobDef academy-video-ops-scanstuck |

Rule 상태: 정상 운영 시 ENABLED. 유지보수 시 DISABLED 가능(상태는 docs/02-OPERATIONS/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md 에 기록 권장).

### AI Worker (ASG)

| 리소스 | 이름/식별 | 비고 |
|--------|-----------|------|
| ASG Name | academy-ai-worker-asg | deploy_worker_asg.ps1 |
| Launch Template | academy-ai-worker-lt | UserData: ECR pull, docker run academy-ai-worker-cpu |
| Desired/Min/Max | Desired 유지 | 동일 멱등 규칙 |
| Scaling policies | Application Auto Scaling | |
| Instance Tag | Name=academy-ai-worker-cpu | |
| ECR | academy-ai-worker-cpu | |

### Storage / CDN (R2 + CDN)

| 항목 | 관리 주체 | 비고 |
|------|-----------|------|
| R2 bucket / endpoint | 외부(Cloudflare) | 설정만 .env/SSM: R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_VIDEO_BUCKET |
| CDN 도메인/캐시 | 외부 | R2_PUBLIC_BASE_URL 등. REFERENCE.md. |
| 퍼지 정책 | 운영 계약 수준 문서화 권장 | 확인 필요: 별도 Runbook 여부 |

### Data (RDS / Redis)

| 리소스 | 이름/식별 | 비고 |
|--------|-----------|------|
| RDS identifier | academy-db | recreate_batch_in_api_vpc.ps1, discover_rds_network.ps1 |
| Redis replication group | academy-redis | setup_elasticache_redis.ps1 |
| Redis subnet group | academy-redis-subnets | |
| Redis SG | academy-redis-sg | 6379 인바운드: API/Worker/Batch SG |
| RDS SG | (RDS 인스턴스에 연결된 SG) | 5432 인바운드: API/Batch SG 허용 필요 |

### IAM (Batch 관련)

| 역할 | 이름 | 용도 |
|------|------|------|
| Batch 서비스 역할 | academy-batch-service-role | Batch가 CE/Queue 관리 |
| ECS 인스턴스 역할 | academy-batch-ecs-instance-role | EC2 인스턴스 프로파일 |
| Instance Profile | academy-batch-ecs-instance-profile | 위 역할 연결 |
| ECS 실행 역할 | academy-batch-ecs-task-execution-role | 이미지 pull, 로그 |
| Job 역할 | academy-video-batch-job-role | Job 내부 AWS/API 호출 |
| EventBridge 역할 | (eventbridge_deploy_video_scheduler.ps1 내 EventsRoleName) | Batch SubmitJob 호출용. 확인: 스크립트 내 변수. |

정확한 역할/정책: `scripts/infra/batch_video_setup.ps1`, `scripts/infra/iam/*.json` 참조.

### SSM

| Parameter | 타입 | 용도 |
|-----------|------|------|
| /academy/workers/env | SecureString | Batch·워커 공통 환경 변수(JSON). 스키마: docs/02-OPERATIONS/SSM_JSON_SCHEMA.md |
| /academy/api/env | SecureString | API 전용 env (문서·스크립트 참조) |

### ECR

| Repository | 이미지 태그 정책 |
|------------|------------------|
| academy-api | 배포 시 태그 정책(immutable 권장) |
| academy-video-worker | **immutable tag 필수.** `:latest` 금지. |
| academy-messaging-worker | 배포 스크립트 기준 |
| academy-ai-worker-cpu | 배포 스크립트 기준 |

---

## 4) Desired State 계약 (정상 상태 정의)

| 리소스 | 정상 상태 |
|--------|-----------|
| **Batch CE (Video)** | status=VALID, state=ENABLED. computeResources: maxvCpus=32, instanceTypes=c6g.large. Public Subnet, SG 일치. |
| **Batch CE (Ops)** | status=VALID, state=ENABLED. |
| **Video Queue** | state=ENABLED. computeEnvironmentOrder에 CE 1개(academy-video-batch-ce-final). |
| **Ops Queue** | state=ENABLED. computeEnvironmentOrder에 academy-video-ops-ce. |
| **JobDef (Video)** | 최신 ACTIVE revision: vcpus=2, memory=3072, retryStrategy.attempts=1, timeout 14400. image immutable tag. |
| **JobDef (Ops)** | reconcile/scanstuck/netprobe: 위 표 스펙. |
| **EventBridge** | rule State=ENABLED(또는 유지보수 시 DISABLED). Target: Ops Queue, JobDef 일치. Schedule: rate(15 minutes) / rate(5 minutes). |
| **ASG (Messaging/AI)** | Launch Template 최신 버전. DesiredCapacity 현재값 유지(0으로 초기화 금지). |
| **SSM** | 파라미터 존재. /academy/workers/env 값은 SSM_JSON_SCHEMA 필수 키 만족. |
| **API** | EC2 running, Docker academy-api 실행, health GET 200. |
| **RDS** | DBInstanceStatus=available. Batch SG에서 5432 허용. |
| **Redis** | ElastiCache 사용 시 클러스터 사용 가능. Batch SG에서 6379 허용. |

---

## 5) 멱등 규칙 (리소스별 Ensure)

| 리소스 | Ensure 규칙 |
|--------|-------------|
| **Batch CE** | Describe → status. INVALID면 Queue 분리 → CE DISABLED → 삭제 → **Wait 삭제 완료** → 동일 이름 재생성 → **Wait VALID/ENABLED** → Queue 재연결. |
| **ASG** | LT 변경 시 새 버전 생성 → ASG를 새 LT 버전으로만 업데이트. **Desired 유지.** 0으로 덮어쓰기 금지. |
| **EventBridge** | Rule 있으면 **Target만** put-targets로 최신화. Rule 삭제 후 재생성 금지. |
| **Job Definition** | vCPU/Memory/Image 변경 시에만 새 revision 등록. 동일 스펙이면 기존 ACTIVE 재사용. |
| **SSM** | get-parameter 후 put-parameter --overwrite. ssm_bootstrap_video_worker.ps1로만 갱신. |
| **RDS/Redis SG** | describe-security-groups → 기존 규칙 확인 후, 없으면 authorize-security-group-ingress (Batch SG → 5432/6379). |
| **ECR** | create-repository 없으면 생성. 이미지 태그는 배포 파이프라인에서 immutable 보장. |

상세 Wait 타임아웃·순서: **INFRA-SSOT-V3.state-contract.md** 참조.

---

## 6) OneTake 배포 순서 (전체 인프라)

1. **Preflight** — `scripts/deploy_preflight.ps1`. AWS Identity, IAM, VPC/Subnet, SSM /academy/workers/env 존재, ECR, ASG. 실패 시 즉시 중단.
2. **Discover** — `scripts/infra/discover_api_network.ps1`, `discover_rds_network.ps1`. (선택) `infra_forensic_collect.ps1` 스냅샷.
3. **Network** — Public Subnet/IGW 확인. 필요 시 라우트/서브넷 정렬(원테이크 내부 또는 network_minimal_bootstrap.ps1).
4. **IAM/SSM** — Batch IAM 역할·인스턴스 프로파일 존재 확인/생성. `scripts/infra/ssm_bootstrap_video_worker.ps1` (-EnvFile .env -Overwrite).
5. **ECR** — Repository 존재. 이미지 푸시는 빌드 단계에서. 원테이크는 EcrRepoUri(immutable tag) 필수.
6. **Batch Video** — `scripts/infra/recreate_batch_in_api_vpc.ps1` (또는 batch_video_setup.ps1). ComputeEnvName=academy-video-batch-ce-final, JobQueueName=academy-video-batch-queue, WorkerJobDefName=academy-video-batch-jobdef.
7. **Batch Ops** — `scripts/infra/batch_ops_setup.ps1`. VideoCeNameForDiscovery=academy-video-batch-ce-final.
8. **EventBridge** — `scripts/infra/eventbridge_deploy_video_scheduler.ps1`. OpsJobQueueName=academy-video-ops-queue. EnableSchedulers 시 ENABLED.
9. **ASG (Messaging/AI)** — `scripts/deploy_worker_asg.ps1` (필요 시). Desired 유지 규칙 준수.
10. **API 서버** — 배포는 full_redeploy.ps1 또는 수동: .env 복사, docker pull/run, health 확인.
11. **Build 서버** — 설치/권한만 Ensure. 빌드는 수동 또는 CI.
12. **Netprobe** — Ops Queue에 netprobe Job 제출 → SUCCEEDED 대기. `scripts/infra/run_netprobe_job.ps1`.
13. **Evidence** — 아래 Evidence 표 출력. `scripts/infra/production_done_check.ps1`, `infra_one_take_full_audit.ps1` 참조.

---

## 7) Evidence 표 (SSOT “클린 증거”)

배포 완료 후 아래 항목을 표로 출력한다.

| 리소스 | 출력 항목 |
|--------|-----------|
| Batch Video CE | academy-video-batch-ce-final, ARN, status=VALID, state=ENABLED |
| Video Queue | academy-video-batch-queue, ARN, state=ENABLED |
| Video JobDef | academy-video-batch-jobdef, revision, vcpus=2, memory=3072, retryStrategy=1 |
| Ops CE | academy-video-ops-ce, ARN, status=VALID, state=ENABLED |
| Ops Queue | academy-video-ops-queue, ARN, state=ENABLED |
| EventBridge reconcile | academy-reconcile-video-jobs, State=ENABLED/DISABLED, Schedule=rate(15 minutes) |
| EventBridge scan_stuck | academy-video-scan-stuck-rate, State=ENABLED/DISABLED, Schedule=rate(5 minutes) |
| Netprobe | jobId, status=SUCCEEDED |
| ASG (Messaging) | academy-messaging-worker-asg, desired/min/max, LaunchTemplate version |
| ASG (AI) | academy-ai-worker-asg, desired/min/max, LaunchTemplate version |
| API | InstanceId, API_BASE_URL, health GET 200 |
| SSM | /academy/workers/env 존재, shape check PASS |

출력 위치: `docs/02-OPERATIONS/actual_state/*.json` 및 콘솔 "VIDEO WORKER SSOT AUDIT" 블록.

---

## 확인 필요(TODO) 분류

### P0 (배포/운영 안정성)

| 항목 | 내용 | 확인 방법 |
|------|------|-----------|
| CI EventBridge 대상 | .github/workflows/video_batch_deploy.yml 가 Ops Queue(academy-video-ops-queue) 사용하는지 | workflow 내 eventbridge_deploy_video_scheduler.ps1 호출 인자 확인. |
| 원테이크 CE 이름 | recreate_batch_in_api_vpc.ps1 호출 시 항상 -ComputeEnvName academy-video-batch-ce-final 전달 | infra_full_alignment_public_one_take.ps1 및 기타 호출처 grep. |

### P1 (멱등성·운영 명확화)

| 항목 | 내용 | 확인 방법 |
|------|------|-----------|
| 배포 동시 실행 락 | 동시 원테이크 2회 이상 시 중복 생성 방지 락 없음 | 락 도입 시 state-contract.md 에 명시. |
| ASG Desired 유지 | deploy_worker_asg.ps1 / redeploy_worker_asg.ps1 에서 update 시 Desired 0 미설정 | 스크립트 내 update 전 describe → 동일 값 유지 여부 검토. |
| ALB/API 공개 구성 | prod에서 API 노출이 Elastic IP 직결인지 ALB+TG인지 | describe-load-balancers, describe-target-health, check_api_alb.ps1. docs/02-OPERATIONS/운영.md “ALB, Target Group /health”. |

### P2 (문서·선택)

| 항목 | 내용 | 확인 방법 |
|------|------|-----------|
| R2/CDN 퍼지·운영 계약 | R2 버킷·CDN 퍼지 정책 단일 문서화 여부 | REFERENCE·runbook 보강. |
| Plan 아티팩트 | Describe→Decision 후 변경 목록 파일 저장(--plan) 미구현 | 원테이크 또는 통합 스크립트에 --plan 출력 추가 검토. |
| EventBridge Events 역할 이름 | eventbridge_deploy_video_scheduler.ps1 내 EventsRoleName 값 | 스크립트 내 변수 검색. |
