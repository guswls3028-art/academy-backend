# SSOT 원테이크 멱등성 배포 — 최종 설계 문서 (Single Source of Truth)

**역할:** 이 프로젝트 배포/운영에 대한 최종 설계 문서(SSOT).  
**원칙:** Describe → Decision → Update/Create 순서, 동일 절차 10회 실행 시 리소스 중복 없이 최종 상태 동일(원테이크 멱등성).

---

## 발견한 문서/스크립트 목록

| 파일경로 | 목적 | 신뢰도(SSOT 후보) | 중복여부 | 최신추정근거 |
|----------|------|-------------------|----------|--------------|
| scripts/infra/infra_full_alignment_public_one_take.ps1 | 원테이크 전체 정렬(네트워크·API·빌드·Batch·EventBridge·Netprobe·Audit) | **높음** (실행 가능 스크립트) | 없음 | PUBLIC_V2 기준, 2026-02 사용 |
| docs/archive/deploy_legacy/VIDEO_WORKER_INFRA_SSOT_*.md | Video Worker 인프라 스펙(과거 버전) | 참고용 | SSOT는 본 문서·RESOURCE-INVENTORY | archive 보관 |
| docs/archive/deploy_legacy/VIDEO_INFRA_ONE_TAKE_ORDER.md | 원테이크 실행 순서·스크립트 목록(과거) | 참조 | runbook과 유사 | archive 보관 |
| docs/02-OPERATIONS/video_batch_production_runbook.md | 런북(환경·리소스·배포 순서·검증·롤백) | **높음** | ONE_TAKE_ORDER와 순서 중복 | 상세 단계·검증 명령 |
| docs/02-OPERATIONS/SSM_JSON_SCHEMA.md | SSM /academy/workers/env 스키마 | **높음** | runbook에 요약 반복 | 데이터 계약 SSOT |
| scripts/infra/recreate_batch_in_api_vpc.ps1 | Batch를 API VPC에 생성·재생성(Describe→SG→CE/Queue/JobDef) | **높음** | batch_video_setup과 역할 겹침 | 원테이크에서 호출, Describe 기반 |
| scripts/infra/batch_ops_setup.ps1 | Ops CE/Queue/JobDef 생성 | **높음** | 없음 | VideoCeNameForDiscovery=ce-final |
| scripts/infra/eventbridge_deploy_video_scheduler.ps1 | EventBridge 규칙·타깃 배포 | **높음** | 없음 | reconcile/scan_stuck → Ops Queue |
| scripts/infra/run_netprobe_job.ps1 | Ops 큐에 netprobe 제출·SUCCEEDED 대기 | **높음** | 없음 | 원테이크 필수 단계 |
| scripts/infra/ssm_bootstrap_video_worker.ps1 | .env → SSM /academy/workers/env | **높음** | 없음 | runbook·SSM_JSON_SCHEMA와 일치 |
| scripts/infra/verify_video_batch_ssot.ps1 | Video/Ops SSOT 검증 | 참조 | 없음 | 감사용 |
| scripts/infra/production_done_check.ps1 | 프로덕션 완료 검증 | 참조 | 없음 | runbook 참조 |
| scripts/infra/infra_one_take_full_audit.ps1 | 전체 감사(-FixMode 시 수정) | 참조 | 없음 | runbook 참조 |
| scripts/deploy_preflight.ps1 | 배포 전 AWS/ECR/SSM/빌드/ASG 점검 | **높음** | 없음 | full_redeploy 전 실행 |
| scripts/infra/infra_forensic_collect.ps1 | 인프라 상태 수집(Describe 스냅샷) | 참조 | 없음 | 원테이크 1단계 |
| .github/workflows/video_batch_deploy.yml | CI: 빌드·푸시·batch_video_setup·EventBridge·CloudWatch | 중간 | 스크립트와 경로 일치 필요 | JOB_QUEUE_NAME=academy-video-batch-queue, Ops Queue 별도 확인 필요 |
| scripts/infra/batch/video_job_definition.json | Video JobDef 템플릿(vcpus=2, memory=3072, retry=1) | **높음** | SSOT 문서와 수치 일치 | retryStrategy.attempts=1, timeout 14400 |
| scripts/infra/batch/ops_compute_env.json | Ops CE 템플릿 | 참조 | 없음 | batch_ops_setup에서 사용 |
| docs/02-OPERATIONS/actual_state/*.json | 실제 상태 스냅샷(api_instance, batch_final_state 등) | 산출물 | 없음 | 스크립트 생성 |
| docs/02-OPERATIONS/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md | EventBridge 규칙 상태·향후 조치 | 참조 | 없음 | 규칙 on/off 기록 |
| docs/02-OPERATIONS/INFRA_VERIFICATION_SCRIPTS.md | 검증 스크립트 정리 | 참조 | 없음 | 문서 |
| scripts/README.md | 스크립트 트리·용도 | 참조 | 없음 | 진입점 |
| docs/01-ARCHITECTURE/REFERENCE.md | 백엔드·설정·배포 요약 | 참조 | 없음 | R2/CDN 언급 |
| infra/worker_asg/*.json | ASG/Lambda IAM 정책(Messaging 등) | 참조 | 없음 | IaC 없을 때 스크립트·문서 우선 |

---

## 정리 필요 이슈 리스트

### 충돌 (같은 리소스에 서로 다른 값)

| 리소스 | 문서/스크립트 A | 문서/스크립트 B | 비고 |
|--------|-----------------|-----------------|------|
| Video CE 이름 | VIDEO_WORKER_INFRA_SSOT_V1/v1_1/PUBLIC_V2: `academy-video-batch-ce-final` | recreate_batch_in_api_vpc.ps1 기본값: `academy-video-batch-ce` | 원테이크는 -ComputeEnvName으로 ce-final 전달. **해결:** SSOT는 ce-final; recreate 호출 시 인자로 ce-final 사용 권장. |
| 네트워크 모델 | SSOT_V1: Private Subnet + NAT + S3 Gateway Endpoint | PUBLIC_V2: Public Subnet + IGW only, NAT 금지 | **해결:** 현재 원테이크 스크립트는 PUBLIC_V2 기준. SSOT는 PUBLIC_V2로 통일. |
| EventBridge 대상 | runbook/one_take: reconcile·scan_stuck → **academy-video-ops-queue** | video_batch_deploy.yml: -JobQueueName **academy-video-batch-queue** | **충돌.** CI에서 EventBridge 단계는 Ops Queue여야 함. 확인: `eventbridge_deploy_video_scheduler.ps1` 파라미터는 -OpsJobQueueName. **해결:** workflow에서 Ops Queue 이름 사용 필요. |

### 중복 (동일 내용 반복)

| 내용 | 위치 1 | 위치 2 | 조치 |
|------|--------|--------|------|
| 리소스 이름·스펙 | VIDEO_WORKER_INFRA_SSOT_V1, v1_1, PUBLIC_V2 | 본 SSOT·RESOURCE-INVENTORY | 본 SSOT 문서가 단일 참조; 나머지는 "과거 버전" 또는 링크만 유지. |
| 배포 순서 | VIDEO_INFRA_ONE_TAKE_ORDER | video_batch_production_runbook | 본 SSOT와 RUNBOOK에 단일 순서 정의. |
| SSM 필수 키 | SSM_JSON_SCHEMA | runbook §1 | SSOT-RESOURCE-INVENTORY·RUNBOOK에서 SSM_JSON_SCHEMA 참조만. |

### 누락 (배포에 필요하나 단일 정의 없음)

| 항목 | 현재 상태 | 확인 방법 |
|------|-----------|-----------|
| 배포 동시 실행 방지(락) | 문서/스크립트에 락 전략 없음 | 동시 실행 시 CE/Queue 중복 생성 가능. **TODO:** DynamoDB/S3/GitHub 환경 락 등 명시 필요. |
| ASG Desired Capacity 0 초기화 방지 | deploy_worker_asg.ps1 등에서 "현재값 유지" 명시 여부 불명 | `aws autoscaling describe-auto-scaling-groups`로 현재 Desired 확인 후, Update 시 0으로 덮어쓰지 않도록 규칙 명시. |
| 롤백 절차 단일 문서 | runbook §4에 일부만 있음 | SSOT-RUNBOOK에 롤백 단계 통합. |
| R2/CDN 프로비저닝 | IaC 없음, 설정만 REFERENCE·runbook | R2 버킷·CDN은 수동/외부; SSOT-RESOURCE-INVENTORY에 "외부 관리"로 기입. |
| Pre-flight 실패 시 즉시 중단 | deploy_preflight.ps1은 exit 1; 원테이크는 각 단계에서 Fail-OneTake | 문서에 "Pre-flight 실패 시 배포 중단" 명시. |

---

## 아키텍처 요약

### 현재 구성 (4개 컴포넌트 + R2/CDN)

1. **API 서버** — EC2 1대, Elastic IP 고정, Public Subnet. Django API, DB/Redis/R2 접근.
2. **Messaging Worker** — ASG 기반 EC2. SQS 소비, 메시지 발송 등.
3. **Video Worker** — AWS Batch 기반. Video Queue 1개 + CE 1개(academy-video-batch-ce-final). 영상 인코딩 → R2 업로드.
4. **Reconciler(리컨실러)** — Batch Ops Queue 1개 + Ops CE 1개. EventBridge로 reconcile(15분)/scan_stuck(5분) 스케줄, netprobe 검증용.
5. **빌드 서버** — EC2(academy-build-arm64). 이미지 빌드·ECR 푸시, Public Subnet, SSM·STS 검증.
6. **Cloudflare R2 + CDN** — 영상 스토리지·배포. 프로비저닝은 레포 IaC 밖(설정만 SSM/.env).

### 데이터/트래픽 흐름

- **API** ↔ RDS(PostgreSQL), Redis, R2(S3 호환 API), SQS(Messaging/Delete R2).
- **Video Batch** — API가 Job 제출 → Video Queue → Video CE에서 컨테이너 실행 → SSM에서 env 로드 → DB/Redis/R2/API_BASE_URL 사용.
- **Ops Batch** — EventBridge → Ops Queue → Ops CE → reconcile/scan_stuck/netprobe Job 실행.
- **R2** — 워커가 HLS 업로드; 삭제는 SQS → Lambda 등으로 비동기 처리.

---

## 환경 구분 및 SSOT 파라미터

| 파라미터 | prod(기본) | staging/dev | 비고 |
|----------|------------|-------------|------|
| AWS_REGION | ap-northeast-2 | 동일 또는 vars | |
| VpcId | vpc-0831a2484f9b114c2 | 확인 필요 | `discover_api_network.ps1` 또는 actual_state |
| API Elastic IP | 15.165.147.157 | 확인 필요 | API 서버 고정 IP |
| API_BASE_URL | http://15.165.147.157:8000 | 동일 또는 env별 | |
| Video CE | academy-video-batch-ce-final | 동일 | |
| Video Queue | academy-video-batch-queue | 동일 | |
| Ops CE | academy-video-ops-ce | 동일 | |
| Ops Queue | academy-video-ops-queue | 동일 | |
| SSM Parameter | /academy/workers/env | 동일(값만 env별) | JSON, SSM_JSON_SCHEMA 준수 |

환경별 차이는 **SSOT-RESOURCE-INVENTORY.md**에만 열거하며, 스크립트는 `--env`(또는 동등)로 구분할 수 있도록 확장 권장.

---

## 원테이크 멱등성 배포 순서 (Describe → Decision → Update/Create)

### 단계 개요

1. **Pre-flight** — 권한/IAM, VPC/Subnet, 필수 파라미터, AWS CLI v2. 실패 시 즉시 중단.
2. **Describe(스냅샷)** — 현재 네트워크·API·빌드·Batch·EventBridge 상태 수집.
3. **Decision** — 기대 스펙(SSOT-RESOURCE-INVENTORY)과 비교, 변경 필요 목록 산출. (선택: Plan 아티팩트 저장.)
4. **Update/Create** — 네트워크 정렬 → API 검증 → 빌드 검증 → Batch Video → Batch Ops → EventBridge → RUNNABLE 정리 → Netprobe.
5. **Audit** — 핵심 리소스 상태 표 출력, FINAL RESULT: PASS/FAIL.

### 단계별 상세

| 단계 | Describe | Decision | Update/Create |
|------|----------|----------|----------------|
| Network | describe-route-tables, describe-subnets, describe-internet-gateways | Public Subnet(0.0.0.0/0→IGW) 존재? MapPublicIpOnLaunch? | FixMode 시 라우트/서브넷 속성 수정 |
| API | describe-addresses(Elastic IP), describe-instances, health GET | Elastic IP 연결·VPC·Subnet·health OK? | 없음(실패 시 중단) |
| Build | describe-instances(tag:academy-build-arm64), SSM send-command | 인스턴스 존재·Public Subnet·SSM sts/curl 성공? | 없음(실패 시 중단) |
| Batch Video | describe-compute-environments, describe-job-queues, describe-job-definitions | CE=VALID/ENABLED? Queue CE 1개? JobDef vcpus/memory/retry 일치? | INVALID CE 시 Queue 분리→CE 삭제→재생성→Queue 재연결; JobDef 변경 시 새 Revision 등록 |
| Batch Ops | describe-compute-environments(ops-ce), describe-job-queues(ops-queue) | Ops CE VALID? Ops Queue 존재? | 없으면 batch_ops_setup.ps1 |
| EventBridge | describe-rule(reconcile), describe-rule(scan-stuck), list-targets-by-rule | schedule 15min/5min, target=Ops Queue? | put-targets로 타깃만 최신화; EnableSchedulers로 ENABLE/DISABLE |
| Netprobe | (없음) | (사후) | Ops Queue에 netprobe 제출 → SUCCEEDED 대기(타임아웃 시 FAIL) |

---

## 리소스별 멱등성 규칙

- **AWS Batch Compute Environment**  
  - Describe로 status 확인. **INVALID**면 Update 시도하지 말고: Queue에서 CE 분리 → CE DISABLED → CE 삭제 → 동일 이름으로 재생성 → Queue 재연결.  
  - 재생성 후 **Wait loop:** describe-compute-environments 반복 until status=VALID, state=ENABLED(타임아웃 명시).

- **ASG(Messaging 등)**  
  - Launch Template 변경 시: 새 버전 생성 후 ASG를 새 Launch Template 버전으로 업데이트.  
  - **Desired Capacity:** 현재값 유지. 0으로 초기화 금지(문서·스크립트에 명시).

- **EventBridge**  
  - Rule이 이미 있으면 **Target만** put-targets로 최신화. Rule 삭제 후 재생성 금지(멱등).

- **Job Definition**  
  - vCPU/Memory/Image 변경 시에만 **새 Revision** 등록. 동일 스펙이면 기존 ACTIVE revision 재사용.

---

## Pre-flight Check (실패 시 즉시 중단)

- **AWS Identity:** `aws sts get-caller-identity` 성공.
- **IAM:** Batch 서비스 역할, ECS 인스턴스 역할, ECS 실행 역할, Job 역할 존재·필요 정책 부착.
- **VPC/Subnet:** 대상 VpcId 존재, Public Subnet(0.0.0.0/0→IGW) 1개 이상.
- **필수 파라미터:** EcrRepoUri 제공, `:latest` 금지(immutable tag), placeholder(`<acct>` 등) 없음.
- **AWS CLI:** v2 사용 권장. `aws batch describe-compute-environments` 등 동작 확인.
- **SSM:** 배포 시 `/academy/workers/env` 존재 및 권한(스크립트에서 get-parameter 사용 시).

확인 방법: `scripts/deploy_preflight.ps1` 실행; 원테이크 전 `infra_forensic_collect.ps1`로 스냅샷 수집.

---

## Step-by-Step Logging 설계

각 단계에서 아래 필드를 출력한다.

| 단계 | 출력 필드 예 |
|------|----------------|
| Pre-flight | AccountId, VpcId, PublicSubnetCount, ApiElasticIp, ApiHealthOk |
| Network | Audit1: PASS/FAIL, Detail(VPC, IGW, PublicSubnets, MapPublicIpOnLaunch) |
| API | Audit2: PASS/FAIL, InstanceId, API_BASE_URL, healthcheck |
| Build | Audit3: PASS/FAIL, BuildInstanceId, SSM sts/curl Status |
| Batch Video | Audit4: PASS/FAIL, CE name, status, state, Queue CE count |
| Batch Ops | Audit5: PASS/FAIL, Ops CE name, status, state |
| EventBridge | Audit6: PASS/FAIL, reconcile schedule, scan_stuck schedule |
| Netprobe | Audit7: PASS/FAIL, jobId, status, logStreamName |
| Final | FINAL RESULT: PASS/FAIL, PreflightDir |

실패 시 **Evidence:** describe 출력(JSON) 또는 fail_evidence.json 경로.

---

## Netprobe

- **목적:** 배포 직후 Ops Queue가 실제로 Job을 실행할 수 있는지 검증.
- **절차:** Ops Queue(`academy-video-ops-queue`)에 netprobe Job 제출 → describe-jobs 폴링 → **SUCCEEDED** 확인. RUNNABLE 정체(설정 초과 시) 시 Evidence 출력 후 FAIL.
- **스크립트:** `scripts/infra/run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-ops-queue`.
- **성공 기준:** exit 0, 콘솔에 "SUCCEEDED" 출력.

---

## Clean Evidence (최종 출력)

마지막에 핵심 리소스 상태를 표로 출력한다.

| 리소스 | 이름/ARN/상태 |
|--------|----------------|
| Video CE | academy-video-batch-ce-final, ARN, status=VALID, state=ENABLED |
| Video Queue | academy-video-batch-queue, ARN, state=ENABLED |
| Video JobDef | academy-video-batch-jobdef, revision, vcpus=2, memory=3072, retryStrategy=1 |
| Ops CE | academy-video-ops-ce, ARN, status=VALID, state=ENABLED |
| Ops Queue | academy-video-ops-queue, ARN |
| EventBridge reconcile | academy-reconcile-video-jobs, State=ENABLED/DISABLED, Schedule=rate(15 minutes) |
| EventBridge scan_stuck | academy-video-scan-stuck-rate, State=ENABLED/DISABLED, Schedule=rate(5 minutes) |
| Netprobe | jobId, status=SUCCEEDED |

출력 형식: `02-OPERATIONS/actual_state/` 하위 JSON 및 콘솔 "VIDEO WORKER SSOT AUDIT" 블록. 자세한 필드는 **RESOURCE-INVENTORY.md** 및 **IDEMPOTENCY-RULES.md** 참조.

---

## 배포 스크립트 인터페이스 (권장)

- **--env:** 환경(prod/staging 등). 기본 prod.
- **--dry-run:** Describe·Decision만 수행, 변경 없음.
- **--plan:** Plan 아티팩트(변경 목록) 생성만.
- **--apply:** Update/Create 실행.
- **--lock:** 동시 실행 방지(락). (현재 미구현; TODO.)
- **--verbose:** 상세 로그.

현재 원테이크 스크립트: `-FixMode`, `-EnableSchedulers` 등으로 동작 제어. 위 인터페이스는 통합 시 적용 권장.

---

## 관련 문서

- [RESOURCE-INVENTORY.md](RESOURCE-INVENTORY.md) — 리소스 이름·ARN·태그·환경별 값.
- [IDEMPOTENCY-RULES.md](IDEMPOTENCY-RULES.md) — 멱등성 규칙·Wait 루프·SSOT 기준.
- [RUNBOOK.md](RUNBOOK.md) — 운영(배포·검증·장애·롤백·점검).
- [CHANGELOG.md](CHANGELOG.md) — 문서 기준 변경 로그.

---

## 남은 확인 필요 항목 (TODO)

### P0 (배포/운영 안정성에 직결)

| 항목 | 내용 | 확인 방법 |
|------|------|-----------|
| CI EventBridge 대상 | `.github/workflows/video_batch_deploy.yml`의 EventBridge 단계가 `academy-video-batch-queue`가 아닌 **academy-video-ops-queue**를 쓰는지 | workflow 파일에서 `eventbridge_deploy_video_scheduler.ps1` 호출 인자 확인. 스크립트는 `-OpsJobQueueName` 필요. |
| 원테이크 CE 이름 일치 | `recreate_batch_in_api_vpc.ps1`를 원테이크에서 호출할 때 항상 `-ComputeEnvName academy-video-batch-ce-final` 전달하는지 | `infra_full_alignment_public_one_take.ps1` 내 호출부 확인(현재 전달함). 다른 호출 경로도 ce-final 사용 여부 검색. |

### P1 (멱등성·운영 명확화)

| 항목 | 내용 | 확인 방법 |
|------|------|-----------|
| 배포 동시 실행 락 | 동시에 원테이크가 2개 이상 실행되면 CE/Queue 중복 생성 가능. 락 전략 없음 | DynamoDB conditional write, S3 기반 락, 또는 GitHub 환경/배포 락 도입 후 SSOT-IDEMPOTENCY-RULES·RUNBOOK에 명시. |
| ASG Desired Capacity 유지 | Messaging ASG 등 업데이트 시 Desired를 0으로 덮어쓰지 않는지 | `deploy_worker_asg.ps1`, `redeploy_worker_asg.ps1` 등에서 update 전 describe로 현재 Desired 읽고, 동일 값으로만 업데이트하는지 grep/검토. |
| VIDEO_WORKER 인프라 SSOT 문서 통일 | VIDEO_WORKER_INFRA_SSOT_V1, v1_1, PUBLIC_V2 세 파일이 공존. README·다른 문서 링크가 어느 것을 가리키는지 | PUBLIC_V2를 현재 설계로 고정하고, V1/v1_1은 "과거/Private 모델"로 archive 또는 링크만 유지. docs/README.md 빠른 참조 표 갱신. |

### P2 (문서·선택 개선)

| 항목 | 내용 | 확인 방법 |
|------|------|-----------|
| Plan 아티팩트 | Describe→Decision 후 변경 목록을 파일로 저장하는 단계 미구현 | 원테이크 또는 통합 스크립트에 `--plan` 시 plan.json 등 출력하도록 추가 검토. |
| --env/--dry-run 인터페이스 | 배포 스크립트에 --env, --dry-run, --plan, --apply, --lock 통일 인터페이스 없음 | 새 통합 스크립트 작성 시 SSOT-IDEMPOTENCY-RULES의 "스크립트 인터페이스" 반영. |
| R2/CDN 프로비저닝 | R2 버킷·CDN 생성이 레포 IaC/스크립트에 없음 | 외부 관리로 인벤토리에만 기입됨. 필요 시 별도 Runbook 섹션 추가. |
