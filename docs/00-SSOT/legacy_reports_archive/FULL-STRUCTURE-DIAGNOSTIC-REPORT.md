# 전면 구조 진단 보고서 — AI 에이전트 프로젝트 관점

**작성 목적:** 현재 레포의 문서(SSOT 포함), scripts_v3, scripts/infra, CI, 전체 디렉토리 구조를 구조적으로 분석한 진단 보고서.  
**분석 일자:** 2025-02-27  
**코드/설계 수정 없음. 현재 상태 분석 및 보고서 생성만 수행.**

---

## [1] 프로젝트 전체 구조 분석

### 1.1 루트 디렉토리 트리 (요약)

```
C:\academy\
├── .cursor/              # Cursor 규칙 (no-assumptions, project-stack-facts, ssot-folder-structure 등)
├── .github/workflows/    # CI: build-and-push-ecr, build-and-push-ecr-nocache, video_batch_deploy
├── academy/              # 백엔드 패키지 (adapters, application, domain, framework)
├── apps/                 # Django 앱: api, core, domains, infrastructure, storage, support, worker
├── docker/               # Dockerfile: api, ai-worker, ai-worker-cpu, ai-worker-gpu, messaging-worker, nginx, video-worker, build.ps1
├── docs/
│   ├── 00-SSOT/          # SSOT 문서·params·state-contract·보고서류
│   ├── 01-ARCHITECTURE/  # ADR·아키텍처 문서
│   ├── 02-OPERATIONS/    # 운영·actual_state·런북
│   └── 03-REPORTS/       # 감사·검증 리포트
├── scripts/
│   └── infra/            # 레거시 인프라: batch/, cloudwatch/, eventbridge/, iam/, JSON 템플릿·PS1
├── scripts_v3/           # SSOT v3 배포: core/, env/, netprobe/, resources/, deploy.ps1, deploy_fullstack.ps1, drift_fullstack.ps1, gather_fullstack_state.ps1
├── src/                  # 도메인·인프라 레이어 (application, domain, infrastructure)
├── manage.py, docker-compose.yml, .env*, requirements 등
└── 기타: backups/, forensic_*, one_take_preflight_*, storage/, supporting/, tools/, venv/
```

### 1.2 인프라 관련 폴더 구조 정리

| 영역 | 경로 | 역할 |
|------|------|------|
| **scripts_v3** | `scripts_v3/` | SSOT v3 단일 진입점. deploy.ps1(Batch 중심), deploy_fullstack.ps1(전체 스택). core(aws-wrapper, wait, preflight, evidence, drift, prune, ssot_canonical), env/prod.ps1, resources/*.ps1, netprobe/batch.ps1 |
| **scripts/infra** | `scripts/infra/` | 레거시: batch(JSON 템플릿·ops_compute_env.json 등), cloudwatch, eventbridge, iam(JSON), PS1(batch_ops_setup, batch_video_setup, eventbridge_deploy_video_scheduler 등). CI에서 직접 호출 금지(denylist). |
| **docs/00-SSOT** | `docs/00-SSOT/` | INFRA-SSOT-V3.md, INFRA-SSOT-V3.params.yaml, INFRA-SSOT-V3.state-contract.md, IDEMPOTENCY-RULES.md, PRUNE-DELETE-ORDER-AND-RISKS.md, RESOURCE-INVENTORY.md, RUNBOOK.md, 감사/풀스택 리포트 등 |
| **.github/workflows** | `.github/workflows/` | video_batch_deploy.yml(video-worker 빌드→scripts_v3/deploy.ps1), build-and-push-ecr.yml(전체 이미지 푸시), build-and-push-ecr-nocache.yml(수동 노캐시 푸시) |

### 1.3 API / Worker / AI / Messaging 코드 위치 정리

| 컴포넌트 | 코드 위치 | 비고 |
|----------|-----------|------|
| **API** | `apps/api/` (Django), `docker/api/` | index.ts, manage.py, v1, config. EC2+EIP·Docker academy-api. |
| **Video Worker** | `apps/worker/video_worker/`, `docker/video-worker/` | AWS Batch (CE/Queue/JobDef). 영상 인코딩→R2. |
| **AI Worker** | `apps/worker/ai_worker/`, `docker/ai-worker*` (cpu/gpu) | ASG(academy-ai-worker-asg), ECR academy-ai-worker-cpu. |
| **Messaging Worker** | `apps/worker/messaging_worker/`, `docker/messaging-worker/` | ASG(academy-messaging-worker-asg), SQS 소비. |
| **공통/도메인** | `academy/`, `src/`, `apps/support/ai`, `apps/support/messaging` | 어댑터·유스케이스·도메인·인프라. |

### 1.4 빌드 서버 관련 스크립트 위치 정리

| 구분 | 위치 | 역할 |
|------|------|------|
| **빌드 이미지** | `docker/` (Dockerfile.base, api, video-worker, messaging-worker, ai-worker-cpu 등) | ECR 푸시용 Dockerfile. |
| **로컬/배치 빌드** | `docker/build.ps1`, `scripts/build_and_push_ecr.ps1`, `scripts/build_and_push_ecr_on_ec2.sh` | 로컬·EC2에서 이미지 빌드·ECR 푸시. |
| **CI 빌드** | `.github/workflows/build-and-push-ecr.yml`, `build-and-push-ecr-nocache.yml` | base → api, messaging-worker, ai-worker-cpu, video-worker 순 빌드·푸시(ARM64). |
| **빌드 인스턴스 확인** | `scripts_v3/resources/build.ps1` (Confirm-BuildInstance) | Tag Name=academy-build-arm64 존재·상태 확인만. 생성/수정 없음. |

---

## [2] SSOT 문서 분석

### 2.1 분석 대상 문서

- **INFRA-SSOT-V3.md** — 전체 인프라 단일 기준(아키텍처·리소스 인벤토리·멱등·OneTake 순서·Evidence).
- **INFRA-SSOT-V3.params.yaml** — 기계용 파라미터(VPC·서브넷·SG·SSM·ECR·API·Build·Messaging/AI·VideoBatch·EventBridge·RDS·Redis).
- **INFRA-SSOT-V3.state-contract.md** — 멱등 규칙·Wait 루프·Netprobe·Evidence 계약·Legacy Kill-Switch·FullStack 진입점.

### 2.2 Canonical 리소스 정의 목록

- **Batch CE:** academy-video-batch-ce-final, academy-video-ops-ce  
- **Batch Queue:** academy-video-batch-queue, academy-video-ops-queue  
- **Batch JobDef:** academy-video-batch-jobdef, academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe  
- **EventBridge Rule:** academy-reconcile-video-jobs, academy-video-scan-stuck-rate  
- **ASG:** academy-messaging-worker-asg, academy-ai-worker-asg  
- **RDS:** academy-db  
- **Redis:** academy-redis (academy-redis-sg)  
- **API:** EIP eipalloc-071ef2b5b5bec9428 (15.165.147.157)  
- **Build:** Tag Name=academy-build-arm64  
- **IAM:** academy-batch-service-role, academy-batch-ecs-instance-role, academy-batch-ecs-task-execution-role, academy-video-batch-job-role, academy-eventbridge-batch-video-role  
- **SSM:** /academy/api/env, /academy/workers/env  
- **ECR:** academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu  

### 2.3 멱등 규칙 정의

- **INFRA-SSOT-V3.md §5:** Batch CE/ASG/EventBridge/JobDef/SSM/RDS·Redis SG/ECR별 Ensure 규칙(Describe→Decision→Update/Create).  
- **state-contract.md §1:** 동일 규칙 표로 정리. Wait 루프(CE 삭제 300초, CE 생성 600초, Netprobe 1200초 등) 명시.  
- **IDEMPOTENCY-RULES.md:** CE/Queue/JobDef/ASG/EventBridge/SSM별 Describe·Decision·Update/Create 상세.

### 2.4 Full Rebuild 정의

- **SSOT:** CE가 없거나 INVALID일 때 Queue 분리→CE DISABLED→삭제→Wait→동일 이름 재생성→Wait VALID/ENABLED→Queue 재연결.  
- **scripts_v3:** deploy.ps1/deploy_fullstack.ps1의 Ensure-VideoCE/Ensure-OpsCE 등에서 `$script:AllowRebuild`로 재생성 여부 제어. AllowRebuild=false면 create/recreate 스킵.

### 2.5 삭제 규칙 존재 여부

- **존재.** PRUNE-DELETE-ORDER-AND-RISKS.md 및 state-contract.md §7, deploy_fullstack.ps1 -PruneLegacy/-DryRun.  
- **삭제 순서:** EventBridge(target 제거→delete rule) → Queue(DISABLED→delete) → CE(DISABLED→delete) → JobDef(deregister, SSOT 외만) → ASG(min=0 desired=0→force-delete) → ECS cluster → IAM(detach/delete inline→delete role) → EIP(미연결만 release).  
- **Canonical 리스트 외 = DELETE CANDIDATE.** core/prune.ps1, core/ssot_canonical.ps1에 정의.

### 2.6 Drift 판정 규칙 명시 여부

- **명시됨.** state-contract.md §8: drift_fullstack.ps1 → FULLSTACK-DRIFT-TABLE.md (수정 가능/재생성 필요/수동 확인 3분류).  
- **core/drift.ps1:** CE(instanceTypes, maxvCpus, subnets, securityGroupIds), Queue(priority, computeEnvironmentOrder), JobDef(vcpus, memory), ASG(LaunchTemplate 존재) 기준 구조 비교.  
- **drift_fullstack.ps1:** CE/Queue/EventBridge/ASG/API EIP/Build/RDS/Redis/SSM/ECR/IAM/VPC 존재·일치 여부로 drift 행 추가.

### 2.7 API / AI / Messaging 포함 여부

- **API:** 포함. SSOT·params·env에 EIP·ApiBaseUrl·container·SSM /academy/api/env. scripts_v3 resources/api.ps1(Confirm-APIHealth), deploy_fullstack에서 Confirm-APIHealth.  
- **AI Worker:** 포함. ASG academy-ai-worker-asg, LT academy-ai-worker-lt, ECR academy-ai-worker-cpu. params·env·ssot_canonical·drift_fullstack에 포함.  
- **Messaging Worker:** 포함. ASG academy-messaging-worker-asg, LT academy-messaging-worker-lt, ECR academy-messaging-worker. 동일하게 SSOT·스크립트에 포함.

### 2.8 Build 서버 포함 여부

- **포함.** INFRA-SSOT-V3.md §3 Build Server 표, params.yaml build.instanceTagKey/instanceTagValue, env BuildTagKey/BuildTagValue.  
- **scripts_v3:** deploy_fullstack만 Confirm-BuildInstance 호출(존재·상태 확인). 생성·갱신은 scripts/ 또는 수동.

### 2.9 Coverage Gap 표

| 영역 | SSOT/문서 상태 | Gap 내용 |
|------|----------------|----------|
| R2/CDN | 문서만 참조(.env/SSM) | 퍼지·운영 계약 단일 문서화 미정리(TODO P2). |
| ALB/Target Group | "확인 필요" | prod 노출이 EIP 직결인지 ALB+TG인지 미확정. |
| 동시 실행 락 | 미구현 | state-contract에 "동시 실행 금지"로 운영. 락 도입 시 문서 명시 필요. |
| Plan 아티팩트 | 미구현 | --plan 변경 목록 파일 저장 미구현. |
| EventBridge Events 역할 이름 | 스크립트 변수 확인 필요 | eventbridge_deploy_video_scheduler.ps1 내 EventsRoleName 등 P2 TODO. |
| 모델 버전 / AI 스케일 전략 | 미정의 | AI Worker 이미지·태그만 SSOT. 모델 버전·스케일 정책 문서화 없음. |
| 멀티 워커(동일 타입 N개) | 미정의 | Messaging/AI 각 1 ASG만 정의. 동일 타입 추가 ASG 네이밍·Canonical 미정의. |

---

## [3] scripts_v3 구조 분석

### 3.1 deploy.ps1 실행 흐름 정리

1. **env:** `env/prod.ps1` 로드(Region·VpcId·PublicSubnets·Batch·API·EventBridge·ASG·SSM·ECR 등).  
2. **core:** logging, aws-wrapper, wait, preflight, evidence.  
3. **resources:** iam, batch, jobdef, eventbridge, asg, ssm, api.  
4. **netprobe:** batch.ps1.  
5. **순서(state-contract):**  
   Invoke-PreflightCheck → Ensure-BatchIAM → Ensure-VideoCE → Ensure-OpsCE → Ensure-VideoQueue → Ensure-OpsQueue → Ensure-VideoJobDef → Ensure-OpsJobDefReconcile/ScanStuck/Netprobe → Ensure-EventBridgeRules → Confirm-ASGState → Confirm-SSMEnv → Confirm-APIHealth → (선택) Invoke-Netprobe → Show-Evidence.  
6. **옵션:** -Env, -EcrRepoUri, -AllowRebuild, -SkipNetprobe.  
7. **범위:** Batch(Video/Ops)·EventBridge·ASG/SSM/API는 Confirm만. Network·RDS·Redis·Build 미포함.

### 3.2 resources/* 파일별 역할 요약

| 파일 | 역할 |
|------|------|
| iam.ps1 | Ensure-BatchIAM(Batch 서비스·ECS 인스턴스·실행·Job 역할·인스턴스 프로파일). |
| batch.ps1 | Ensure-VideoCE/OpsCE, Ensure-VideoQueue/OpsQueue. scripts/infra/batch/*.json 읽기·PLACEHOLDER 치환 후 create. INVALID 시 delete→Wait→recreate. AllowRebuild로 create/recreate 제어. |
| jobdef.ps1 | Ensure-VideoJobDef, Ensure-OpsJobDefReconcile/ScanStuck/Netprobe. 이미지·vcpus·memory 기준 drift 시 register. |
| eventbridge.ps1 | Ensure-EventBridgeRules. put-rule/put-targets. |
| asg.ps1 | Confirm-ASGState(Messaging·AI ASG describe만). |
| asg_messaging.ps1 | Ensure-ASGMessaging(describe만, 없으면 경고). |
| asg_ai.ps1 | Ensure-ASGAi(describe만, 없으면 경고). |
| ssm.ps1 | Confirm-SSMEnv(/academy/workers/env, /academy/api/env 존재 확인). |
| api.ps1 | Get-APIInstanceByEIP, Confirm-APIHealth(GET /health 200). |
| build.ps1 | Confirm-BuildInstance(Tag academy-build-arm64 존재·상태). |
| network.ps1 | Ensure-NetworkVpc, Confirm-SubnetsMatchSSOT. (deploy_fullstack만 사용) |
| rds.ps1 | Confirm-RDSState, Ensure-RDSSecurityGroup. (deploy_fullstack만 사용) |
| redis.ps1 | Confirm-RedisState, Ensure-RedisSecurityGroup. (deploy_fullstack만 사용) |

### 3.3 Drift 판정 로직 존재 여부

- **존재.**  
  - **core/drift.ps1:** Get-StructuralDrift(CE/Queue/JobDef/ASG Expected vs Actual), Show-StructuralDriftTable.  
  - **drift_fullstack.ps1:** CE/Queue/EventBridge/ASG/API EIP/Build/RDS/Redis/SSM/ECR/IAM/VPC describe 후 SSOT와 비교해 3분류(Updatable/Recreate required/Manual check), FULLSTACK-DRIFT-TABLE.md 출력.

### 3.4 Delete/Wait 멱등 보장 구조 여부

- **Delete:** prune.ps1의 Invoke-PruneLegacyDeletes가 삭제 순서·Wait 호출(Wait-CEDeleted, Wait-QueueDeleted, Wait-ASGDeleted, Wait-ECSClusterDeleted, Wait-IAMRoleDeleted, Wait-EventBridgeRuleDeleted) 사용. 고정 sleep 대신 폴링.  
- **Wait:** wait.ps1에 CE 삭제(300s), CE VALID/ENABLED(600s), Queue/ASG/ECS/IAM/EventBridge 삭제 후 Wait 함수 정의.  
- **멱등:** deploy/deploy_fullstack는 Describe→Decision→Update/Create만 수행. 2회차 "Idempotent: No changes required." 문서화(PRUNE-DELETE-ORDER §5).

### 3.5 Batch 외 리소스 포함 여부

- **deploy.ps1:** Batch·EventBridge·JobDef·ASG(Confirm)·SSM(Confirm)·API(Confirm). Batch 외는 생성 없음.  
- **deploy_fullstack.ps1:** Network(VPC·Subnet), IAM, RDS(Confirm+SG Ensure), Redis(Confirm+SG Ensure), Batch, EventBridge, ASG(Ensure-ASGMessaging, Ensure-ASGAi), SSM, API, Build(Confirm), Netprobe, Evidence. Batch 외에도 RDS/Redis SG·Network·ASG Confirm/Ensure 포함.

### 3.6 API/Build/AI/Messaging 관리 범위 확인

- **API:** EIP로 인스턴스 식별, GET /health 200 확인. 생성·배포는 scripts/full_redeploy.ps1 등 레거시 또는 수동.  
- **Build:** Tag academy-build-arm64 존재·상태 확인만.  
- **AI/Messaging:** ASG 존재 확인(Ensure-ASGAi, Ensure-ASGMessaging). 없으면 경고만, 생성은 deploy_worker_asg.ps1 또는 수동. 즉 scripts_v3는 ASG "확인" 수준.

### 3.7 ForceRecreate / PruneLegacy 모드 존재 여부

- **ForceRecreate:** 명시적 -ForceRecreate 옵션 없음. AllowRebuild로 CE/Queue 없거나 INVALID일 때 재생성 허용 여부만 제어.  
- **PruneLegacy:** deploy_fullstack.ps1에 -PruneLegacy, -DryRun 존재. DryRun 시 DELETE CANDIDATE 표·Drift 표·삭제 순서·리스크 출력 후 종료. PruneLegacy 시 Invoke-PruneLegacyDeletes 후 FullStack Ensure.

---

## [4] scripts/infra 레거시 분석

### 4.1 현재 사용 여부

- **CI:** video_batch_deploy.yml은 scripts_v3/deploy.ps1만 실행. scripts/infra/*.ps1 직접 호출 금지. guard-no-legacy-scripts에서 workflow 내 `scripts/infra/*.ps1` 호출 여부 grep으로 검사.  
- **scripts_v3:** resources/batch.ps1이 scripts/infra/batch/*.json을 **읽기 전용**으로 사용(video_compute_env.json, ops_compute_env.json, video_job_queue.json, ops_job_queue.json 등). 즉 JSON 템플릿은 scripts_v3에서 여전히 사용 중.  
- **직접 실행:** 원테이크·일상 배포는 scripts_v3 진입점 권장. scripts/infra/*.ps1 직접 실행은 state-contract에서 금지. deprecated guard는 "선택"으로 문서화만 됨.

### 4.2 JSON 템플릿 의존 구조

- **위치:** scripts/infra/batch/(video_compute_env.json, ops_compute_env.json, video_job_queue.json, ops_job_queue.json 등), scripts/infra/iam/*.json, scripts/infra/eventbridge/*.json, scripts/infra/cloudwatch/*.json.  
- **의존:** scripts_v3/resources/batch.ps1이 batch/*.json을 읽어 PLACEHOLDER_* 치환 후 aws batch create-compute-environment/create-job-queue 등에 전달.  
- **ops_compute_env.json:** academy-video-ops-ce 이름·MANAGED·EC2·c6g.large·min0 max2·서브넷·SG·역할 등 정의.

### 4.3 scripts_v3와 중복 영역

- **Batch CE/Queue:** scripts_v3가 Ensure 담당. 레거시 batch_video_setup.ps1, batch_ops_setup.ps1 등은 동일 CE/Queue 이름을 다룸.  
- **EventBridge:** scripts_v3 Ensure-EventBridgeRules vs 레거시 eventbridge_deploy_video_scheduler.ps1.  
- **IAM:** scripts_v3 Ensure-BatchIAM vs scripts/infra/iam/*.json·batch_video_setup 등.  
- **JSON:** 스펙은 scripts/infra/batch에 있고, 실행 경로만 scripts_v3로 통일된 상태.

### 4.4 제거 가능 여부

- **batch/*.json:** scripts_v3가 읽기 때문에 제거 시 batch.ps1을 JSON 인라인 또는 다른 템플릿 소스로 전환 필요. 즉시 제거 불가.  
- **scripts/infra/*.ps1:** CI·state-contract상 직접 호출 안 함. dot-source·수동 실행 가능성은 있음. 제거하려면 scripts_v3·문서에서 레거시 참조 제거 후 단계적 제거 가능.  
- **iam/eventbridge/cloudwatch JSON:** scripts_v3가 직접 읽는지 여부는 iam.ps1 등에서 확인 필요. IAM은 scripts_v3가 역할 생성 시 인라인 또는 별도 경로 사용할 수 있어, JSON 제거는 스크립트 변경 필요.

---

## [5] CI 파이프라인 분석

### 5.1 deploy entrypoint 확인

- **video_batch_deploy.yml:**  
  - guard-no-legacy-scripts → build-and-push(video-worker 이미지) → deploy-infra.  
  - deploy-infra에서 `pwsh -File scripts_v3/deploy.ps1 -Env prod -EcrRepoUri "${{ needs.build-and-push.outputs.ecr_uri }}"` 만 실행.  
- **build-and-push-ecr.yml / build-and-push-ecr-nocache.yml:** 이미지 빌드·ECR 푸시만. **deploy 단계 없음.**  
- **결론:** 배포 진입점은 video_batch_deploy의 scripts_v3/deploy.ps1만. deploy_fullstack.ps1은 CI에 없음(수동 실행).

### 5.2 legacy denylist 존재 여부

- **존재.** video_batch_deploy.yml의 guard-no-legacy-scripts 단계에서 `grep -E 'scripts/infra/[^"]*\.ps1' .github/workflows/video_batch_deploy.yml` 실행. workflow 파일 안에 scripts/infra/*.ps1 호출이 있으면 실패.  
- **한계:** 다른 workflow 파일(build-and-push-ecr 등)이나 다른 레포·스크립트에서 scripts/infra 호출하는지는 이 가드로 막지 않음.

### 5.3 build → deploy 연결 구조

- **video_batch_deploy:** build-and-push가 ecr_uri 출력 → deploy-infra가 needs.build-and-push.outputs.ecr_uri를 deploy.ps1 -EcrRepoUri로 전달. video-worker만 빌드→푸시→배포 연결됨.  
- **build-and-push-ecr:** api, messaging-worker, ai-worker-cpu, video-worker, base 빌드·푸시만. 배포 트리거 없음.  
- **정리:** API/Messaging/AI 이미지는 CI에서 빌드만 하고, 배포는 수동 또는 별도 파이프라인.

### 5.4 멱등/재실행 안전성 여부

- **멱등:** deploy.ps1이 Describe→Decision→Update/Create 순서·AllowRebuild 플래그로 동일 실행 시 상태 유지. state-contract·IDEMPOTENCY-RULES에 명시.  
- **재실행:** concurrency group: video-batch-deploy, cancel-in-progress: false. 동시 실행은 1개만. 락은 미구현이므로 "동시 원테이크 2회 금지"는 운영 규칙으로만 존재.  
- **Netprobe:** 실패 시 throw로 배포 중단. Evidence로 최종 상태 검증.

---

## [6] AI 에이전트 프로젝트 관점 분석

### 6.1 멀티 워커 지원 구조

- **현재:** Messaging ASG 1개, AI ASG 1개만 Canonical. 동일 타입 추가 ASG(예: ai-worker-gpu 전용 ASG)에 대한 SSOT 이름·Canonical 목록·스크립트 분기 없음.  
- **결론:** 멀티 워커(동일 역할 N개)를 위한 명시적 구조 없음.

### 6.2 환경변수 단일소스 여부

- **워커/Batch:** /academy/workers/env (SSM). SSM_JSON_SCHEMA·ssm_bootstrap_video_worker.ps1로 갱신. 단일 소스로 사용.  
- **API:** /academy/api/env. 동일하게 단일 파라미터.  
- **로컬/빌드:** .env·.env.deploy·.env.example 등 파일 다수. 어떤 것이 "단일 소스"인지 레포 전체 기준으로 명시된 정책 없음.  
- **결론:** SSM 기준으로는 API/워커 환경변수 단일소스. 로컬·빌드 env 파일은 정리·우선순위 문서화 부족.

### 6.3 모델 버전 관리 포함 여부

- **SSOT/params:** AI Worker ECR·ASG·LT 이름만 정의. 모델 버전·파일 경로·허깅페이스 등 버전 관리 미포함.  
- **문서:** AI Worker 확장·모델 버전에 대한 전용 섹션 없음.  
- **결론:** 모델 버전 관리가 SSOT·CI·문서에 포함되어 있지 않음.

### 6.4 AI worker 확장 전략 존재 여부

- **인프라:** ASG·LT·ECR(academy-ai-worker-cpu)만 정의. 스케일 정책은 fix_all_worker_scaling_policies.ps1 등 레거시에 의존.  
- **문서:** "Scaling policies | Application Auto Scaling" 수준 언급만. AI 전용 스케일·큐 전략·우선순위 문서 없음.  
- **결론:** AI worker 확장을 위한 명시적 전략 문서·SSOT 확장 없음.

### 6.5 Messaging worker 확장 전략 존재 여부

- **상황:** AI와 유사. ASG·LT·ECR만 SSOT. 스케일 정책은 레거시 스크립트 참조.  
- **결론:** Messaging worker 확장 전략도 문서·SSOT에 명시되어 있지 않음.

### 6.6 배포 자동화 수준

- **자동화됨:** video-worker만 CI에서 빌드→ECR→scripts_v3/deploy.ps1까지 일괄.  
- **수동:** API·Build·Messaging·AI 이미지 빌드는 CI에 있으나 배포는 수동. deploy_fullstack.ps1·레거시 full_redeploy.ps1 등은 수동 실행.  
- **결론:** Video Batch는 자동화 수준 높음. 전체 스택·API·워커 일괄 배포는 수동 또는 부분 자동화.

---

## [7] 총평

### 7.1 현재 구조 강점

- **SSOT 일원화:** INFRA-SSOT-V3.md·params.yaml·state-contract로 리소스 이름·멱등·Wait·Evidence·삭제 순서가 문서화됨.  
- **단일 진입점:** deploy.ps1 / deploy_fullstack.ps1로 배포 경로가 명확하고, Legacy Kill-Switch·CI guard로 scripts/infra 직접 호출을 막음.  
- **멱등·Wait:** Describe→Decision→Update/Create·폴링 기반 Wait로 재실행 안전성 확보.  
- **Drift·Prune:** drift_fullstack.ps1·FULLSTACK-DRIFT-TABLE.md·PruneLegacy·DryRun으로 SSOT 외 리소스 정리·구조 비교 가능.  
- **Evidence·Netprobe:** 배포 후 Evidence 표·Netprobe로 Ops Batch 동작 검증.

### 7.2 현재 구조 약점

- **이중 구조:** scripts_v3가 scripts/infra/batch JSON에 의존. 레거시 PS1과 역할 중복(EventBridge·IAM·Batch).  
- **CI 범위:** deploy는 video_batch_deploy만. API·Messaging·AI는 빌드만 하고 배포 미연결.  
- **ASG 생성 부재:** scripts_v3는 ASG 존재 확인만. 생성은 레거시 deploy_worker_asg.ps1 또는 수동.  
- **동시 실행 락 미구현:** 운영 규칙으로만 "동시 실행 금지".  
- **Plan/--dry-run:** deploy_fullstack -DryRun은 Prune 표·Drift 위주. 일반 배포 plan 아티팩트(변경 목록 파일)는 미구현.

### 7.3 완전 청산 모드 대비 부족 요소

- **PruneLegacy 리스크:** API/Build가 academy-* IAM 역할 사용 시 SSOT_IAMRoles에 없으면 DELETE CANDIDATE. 제외 목록 명시·점검 필요.  
- **삭제 순서·의존성:** 문서화·prune.ps1 순서는 있으나, 실제 의존성(예: Lambda·기타 서비스가 academy-* 역할 참조) 전수 조사 부족.  
- **Rollback 정의:** Prune 또는 Full Rebuild 후 롤백 절차·스냅샷 복구가 문서화되어 있지 않음.

### 7.4 AI 에이전트 풀패키지화 대비 부족 요소

- **모델·버전:** 모델 버전·아티팩트 경로·ECR/이미지와의 매핑 미정의.  
- **멀티 워커·스케일:** 동일 타입 N개 ASG·큐·스케일 정책의 SSOT 확장 없음.  
- **AI/Messaging 배포 자동화:** CI에서 이미지 푸시 후 deploy_fullstack 또는 워커 전용 배포 단계 없음.  
- **환경변수 정책:** 로컬·SSM·빌드 env 우선순위·단일소스 정책이 한곳에 정리되어 있지 않음.

### 7.5 재현성(다른 컴퓨터에서 딸깍 배포) 점수: **C+**

- **근거:** SSOT·params·env/prod.ps1·scripts_v3로 동일 절차 재현 가능. 다만 (1) AWS 자격·시크릿·로컬 .env 설정이 선행되어야 하고, (2) API/워커 ASG 생성은 scripts_v3 밖(deploy_worker_asg 등)에 의존, (3) Build 인스턴스·RDS·Redis·VPC는 이미 존재한다고 가정. 새 계정·새 PC에서 "클론 후 한 번에" 배포하려면 전제 조건·순서 문서화와 일부 스크립트 보강 필요.

### 7.6 운영 난이도 점수: **Medium**

- **근거:** SSOT·단일 진입점·Evidence로 일상 운영은 추적 가능. 다만 레거시/scripts_v3 이중 구조·CI 미연결 구간·PruneLegacy 리스크·동시 실행 제한을 이해해야 하므로 낮다고 보기 어렵고, 높은 수준의 장애 대응·롤백 문서는 부족해 High까지는 아님.

### 7.7 기술 부채 영역

- **scripts/infra vs scripts_v3:** JSON은 scripts_v3가 읽음. PS1은 직접 호출 금지이지만 파일·역할 중복 잔존.  
- **env 이중화:** env/prod.ps1과 params.yaml 수동 동기화. 기계는 params, 스크립트는 prod.ps1 사용.  
- **CI 불균형:** Video만 빌드+배포. API·워커는 빌드만 또는 수동 배포.  
- **ASG 생성:** scripts_v3는 Confirm/Ensure(존재 확인)만. ASG 최초 생성은 레거시 또는 수동.  
- **문서 TODO:** ALB/EIP·R2 퍼지·Plan 아티팩트·락·EventBridge 역할 이름 등 P0/P1/P2 미해결 항목.  
- **AI/메시징 확장:** 모델 버전·멀티 워커·스케일 전략이 SSOT·문서에 없음.

---

*본 보고서는 코드·설계 변경 없이 현재 상태만 구조적으로 분석한 결과입니다.*
