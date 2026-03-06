# V1 인벤토리 및 레거시/드리프트 제거 계획

**역할:** 1인 개발·운영, 복잡도 낮고 안정성 높은 최신화.  
**SSOT:** `docs/00-SSOT/v1/params.yaml`  
**배포:** `scripts/v1/deploy.ps1`  
**갱신:** 2026-03-06

---

## 1. SSOT 기반 자산 인벤토리

### 1.1 academy-v1-* 네이밍 기준 (SSOT에 있는 리소스)

| 유형 | 이름 | params/SSOT 키 | 비고 |
|------|------|----------------|------|
| **ASG** | academy-v1-api-asg | api.asgName | min/desired/max 2/2/4 |
| | academy-v1-messaging-worker-asg | messagingWorker.asgName | |
| | academy-v1-ai-worker-asg | aiWorker.asgName | |
| **ALB/TG** | academy-v1-api-alb | api.albName | |
| | academy-v1-api-tg | api.targetGroupName | |
| **Launch Template** | academy-v1-api-lt | api.asgLaunchTemplateName | |
| | academy-v1-messaging-worker-lt | messagingWorker.launchTemplateName | |
| | academy-v1-ai-worker-lt | aiWorker.launchTemplateName | |
| **Batch CE** | academy-v1-video-batch-ce | videoBatch.standard.computeEnvironmentName | |
| | academy-v1-video-batch-long-ce | videoBatch.long.computeEnvironmentName | |
| | academy-v1-video-ops-ce | videoBatch.opsComputeEnvironmentName | |
| **Batch Queue** | academy-v1-video-batch-queue | videoBatch.standard | |
| | academy-v1-video-batch-long-queue | videoBatch.long | |
| | academy-v1-video-ops-queue | videoBatch.opsQueueName | |
| **Batch JobDef** | academy-v1-video-batch-jobdef | videoBatch.standard | |
| | academy-v1-video-batch-long-jobdef | videoBatch.long | |
| | academy-v1-video-ops-reconcile, -scanstuck, -netprobe | videoBatch.opsJobDefs | |
| **EventBridge** | academy-v1-reconcile-video-jobs | eventBridge.reconcileRuleName | |
| | academy-v1-video-scan-stuck-rate | eventBridge.scanStuckRuleName | |
| **SQS** | academy-v1-messaging-queue | messagingWorker.sqsQueueName | |
| | academy-v1-messaging-queue-dlq | messagingWorker.dlqSuffix | |
| | academy-v1-ai-queue | aiWorker.sqsQueueName | |
| | academy-v1-ai-queue-dlq | aiWorker.dlqSuffix | |
| **ECR** | academy-api | ecr.apiRepo | |
| | academy-video-worker | ecr.videoWorkerRepo | |
| | academy-messaging-worker | ecr.messagingWorkerRepo | |
| | academy-ai-worker-cpu | ecr.aiWorkerRepo | |
| **SSM** | /academy/api/env | ssm.apiEnv | |
| | /academy/workers/env | ssm.workersEnv | |
| | /academy/rds/master_password | rds.masterPasswordSsmParam | Prune 제외(SSOT 보호) |
| | /academy/deploy-lock | ssm.deployLockParam | |
| **DynamoDB** | academy-v1-video-job-lock | dynamodb.lockTableName | |
| | academy-v1-video-upload-checkpoints | dynamodb.uploadCheckpointTableName | |
| **RDS** | academy-db | rds.dbIdentifier | 기존 식별자 유지 |
| **Redis** | academy-v1-redis | redis.replicationGroupId | |
| **IAM** | academy-ec2-role | api/worker instanceProfile | |
| | academy-v1-eventbridge-batch-video-role | eventBridge.roleName | |
| | academy-batch-* (Batch 관련 역할 4종) | SSOT_IAMRoles | |

### 1.2 네트워크(VPC/서브넷/SG)

- **VPC:** params `network.vpcId` (예: vpc-0831a2484f9b114c2) 또는 태그 academy-v1-vpc.
- **SG:** academy-v1-sg-app, academy-v1-sg-batch, academy-v1-sg-data (params `network.sg*Name`).
- **EIP:** NAT용 1개만 유지(SSOT_EIP). 미연결 EIP는 Prune 대상.

### 1.3 Cloudflare(R2/CDN) — SSOT 외부, 문서만 관리

- **R2 버킷(5개):** academy-admin, academy-ai, academy-excel, academy-storage, academy-video. (S3 미사용·R2만 원칙.)
- **도메인:** app/API/CDN 라우팅은 params `front.domains`로 표준화.

---

## 2. 제거 후보 분류 (SSOT에 없거나 구형/중복)

### 2.1 삭제 전 확인 의무

- **실사용 여부:** CloudWatch 지표(RequestCount, InvocationCount 등), 최근 로그, 태그, 연결 관계(ALB 타깃, EventBridge 타깃, 큐 구독)로 확인.
- **삭제 순서:** 의존성 역순(타깃 제거 → 규칙/큐 비활성화 → 삭제).
- **롤백:** 삭제 후 복구가 필요한 리소스는 PruneLegacy 실행 전 표에서 "롤백 전략" 확인.

### 2.2 제거 후보 목록 (WHY / 의존성 / 삭제 순서 / 롤백)

| 단계 | 유형 | 식별자 | WHY | 의존성 | 삭제 순서 | 롤백 |
|------|------|--------|-----|--------|-----------|------|
| **(1) 미사용·무해** | EIP | 연동 안 된 AllocationId | 비용·정리 | 없음 | PruneLegacy 10번째 | EIP 재할당 가능 |
| | SSM | /academy 아래 SSOT_SSM 외 | 레거시/테스트 파라미터 | 앱이 참조하면 실패 | PruneLegacy 9번째 | put-parameter로 재생성 |
| | ECR | academy-base | SSOT_ECR에 없음. base 이미지 사용 안 하면 제거 | 빌드 파이프라인이 base 참조 시 실패 | PruneLegacy 8번째 | create-repository + 푸시 |
| **(2) 중복·대체됨** | Batch CE | academy-video-batch-ce-final, academy-video-ops-ce(구 이름) | v1 CE로 대체됨 | 해당 큐가 CE 참조 중이면 큐 먼저 전환 | 1) 큐 비활성화·삭제 2) CE 비활성화·삭제 | Ensure로 v1 CE 재생성 |
| | Batch Queue | academy-video-batch-queue, academy-video-ops-queue | v1 큐로 대체됨 | Job 제출처가 구 큐 사용 중이면 중단 | 1) EventBridge 타깃을 v1 큐로 변경 2) 구 큐 비활성화·삭제 | Ensure로 v1 큐 재생성 |
| | EventBridge | v1 아닌 규칙명 | v1 규칙으로 대체 | 타깃 제거 후 삭제 | 1) remove-targets 2) delete-rule | put-rule + put-targets |
| | ASG | academy-v1 아닌 academy-* ASG | 단일 EC2(academy-api) 등 구 구성 | 인스턴스 0으로 스케일 후 삭제 | PruneLegacy 5번째 | 수동 ASG 재생성 |
| **(3) 핵심·교체 완료 후** | Lambda | academy-worker-queue-depth-metric, academy-worker-autoscale | v1 SSOT에 Lambda 없음. SQS 스케일은 ASG 정책으로 | 없음 | 수동 delete-function | Lambda 재배포 |
| | EC2 | (레거시) academy-api 등 | API는 ASG로 통합, **빌드 서버는 사용하지 않음** | 없음 | 수동 종료 | AMI/스냅샷에서 복구 |

**주의:** `/academy/rds/master_password`는 SSOT 보호 목록에 포함되어 PruneLegacy 시 삭제 대상에서 제외된다(scripts/v1/core/ssot.ps1).

### 2.3 안전 삭제 실행 플랜 (트래픽 영향 최소화)

| Phase | 내용 | 트래픽 영향 |
|-------|------|--------------|
| **Phase 0** | `deploy.ps1 -Plan -PruneLegacy`로 삭제 후보 표만 확인. 실사용 여부는 CloudWatch/로그/태그로 별도 확인. | 없음 |
| **Phase 1** | 미사용 EIP 해제, SSOT 외 SSM 파라미터 삭제(필요 시 제외 목록 추가), academy-base ECR 삭제(빌드가 base 미사용 시). | 없음(연결된 리소스 없을 때) |
| **Phase 2** | EventBridge 구 규칙 타깃 제거 → 규칙 삭제. Batch 구 큐 비활성화 → 삭제. Batch 구 CE 비활성화 → 삭제. JobDef deregister. | 구 규칙/큐 사용 중이면 해당 Job/이벤트 중단 |
| **Phase 3** | v1 아닌 ASG min=0 max=0 desired=0 후 force-delete. ECS 클러스터 삭제(후보일 때). IAM 역할 detach/delete. | 해당 ASG 서비스 중단 |
| **Phase 4** | Lambda/EC2 수동 정리(필요 시). VPC/서브넷은 RDS·Redis가 같은 VPC 사용 시 삭제 금지. | 수동 판단 |

실행 예시:

```powershell
# 후보만 확인 (삭제 없음)
pwsh scripts/v1/deploy.ps1 -Plan -PruneLegacy -AwsProfile default

# Phase 1~3 자동 실행 (PruneLegacy가 위 순서로 삭제)
pwsh scripts/v1/deploy.ps1 -PruneLegacy -AwsProfile default
```

---

## 3. ‘신식 표준’ 최적화 범위 (과도한 변경 금지)

| 항목 | 내용 | SSOT/스크립트 반영 |
|------|------|---------------------|
| **ECR** | lifecycle: 최신 20개 유지(v1-, bootstrap- 태그), untagged 1일 만료. 전체 repo에 동일 정책. | `docs/00-SSOT/v1/scripts/ecr-lifecycle-policy.json` — Ensure-ECRRepos에서 일괄 적용됨. immutable tag 필수(params ecr.immutableTagRequired). |
| **SSM** | /academy/api/env, /academy/workers/env 구조 일관화. 필수 키: workers 쪽 DB_*, R2_*, API_BASE_URL, INTERNAL_WORKER_TOKEN, REDIS_* 등. | Bootstrap이 workers env를 .env에서 채움. SSM 삭제 후보에서 /academy/rds/master_password 제외(ssot.ps1). |
| **로그/알람** | retention 30일. 알람 최소세트(API 5xx, Target Unhealthy, SQS depth/DLQ, Batch backlog/failed/stuck, RDS/Redis). 평가 5~15분, 노이즈 최소화. | params observability.*. CloudWatch 알람 생성은 스크립트 또는 수동 가이드(V1-DEPLOYMENT-VERIFICATION.md). |
| **배포 재현성** | GitHub Actions 워크플로로 빌드/푸시 재현. | `.github/workflows/v1-build-and-push-latest.yml` |
| **보안 최소선** | SG 인바운드 최소화, IAM 최소 권한(R2/SSM read 범위 점검), SSH 대신 SSM Session Manager. | params network.securityGroupApp 등. 문서에 "SSH 의존 최소화, SSM 우선" 명시. |

---

## 4. 프론트 배포 + R2 + CDN (요약)

- **params.yaml:** `front`, `cdn`, `r2` 섹션 추가(§5 산출물 참고).
- **배포:** `deploy.ps1`에 `-DeployFront` 옵션 및 프론트 단계(빌드 → R2 업로드 → purge → 검증). `deploy-front.ps1` 호출.
- **도메인:** app.<domain> → CDN → R2(static), api.<domain> → ALB(또는 Cloudflare proxy 정책 명시).
- **CORS:** 허용 origin을 app 도메인으로 제한, credentials 사용 시 true.
- **캐시:** 정적 asset long max-age + immutable, index.html no-cache 또는 짧은 TTL + 배포 시 purge.
- **업로드/다운로드:** multipart/resume + 서명 URL 또는 서버 중계 — V1 방식과 호환되는 단순 방식 SSOT 문서화.

---

## 5. 산출물 체크리스트

| 산출물 | 상태 |
|--------|------|
| (a) 제거 후보 목록(WHY/의존성/삭제 순서/롤백) | §2.2, §2.3 |
| (b) 안전 삭제 실행 플랜(단계별) | §2.3 |
| params.yaml 프론트/R2/CDN 섹션 | 별도 편집 |
| deploy.ps1 프론트 배포 단계 또는 deploy-front.ps1 | 별도 편집 |
| V1-DEPLOYMENT-VERIFICATION.md 검증 시나리오 추가 | 별도 편집 |
| Evidence/Drift 갱신 루틴 | 배포 후 check-v1-infra.ps1 |
| SSM RDS 비밀번호 Prune 제외 | ssot.ps1 반영 완료 |

---

**문서 끝.**
