# deploy_fullstack.ps1 설계안

**목표:** Academy 전체 인프라를 SSOT v3 기준으로 원테이크 강제 정렬. Video만이 아니라 API/Build/ASG/Batch/Ops/RDS/Redis/SSM/IAM/ECR/EventBridge/Network 전체 포함.

---

## 1. 단일 진입점

| 스크립트 | 역할 |
|----------|------|
| `scripts_v3/deploy_fullstack.ps1` | FullStack Ensure 단일 진입점. `-Env prod`, `-PruneLegacy`, `-SkipNetprobe` 등. |
| `scripts_v3/deploy.ps1` | 기존 Video/Batch 중심 원테이크 유지 (하위 호환). FullStack은 deploy_fullstack에서만 수행. |

**인자 권장:**
- `-Env prod|staging|dev`
- `-AllowRebuild` (Batch CE/Queue 재생성 허용, 기본 true)
- `-SkipNetprobe`
- `-PruneLegacy` (SSOT 외 잔재 목록 출력 후 삭제 옵션, **기본 false**)
- `-DryRun` (Describe·Decision만, 변경 없음)

---

## 2. Ensure-* 모듈 설계

| 모듈 | 파일 | 함수 | 역할 |
|------|------|------|------|
| Network | resources/network.ps1 | Ensure-NetworkVpc, Confirm-SubnetsMatchSSOT | VPC/Subnet 존재·SSOT 일치 확인. 변경은 최소(라우트 등 수동 확인 권장). |
| IAM | resources/iam.ps1 | Ensure-BatchIAM (기존) | Batch/EventBridge 역할·인스턴스 프로파일. |
| Batch | resources/batch.ps1 | Ensure-VideoCE, Ensure-OpsCE, Ensure-VideoQueue, Ensure-OpsQueue | CE/Queue Create·Recreate·Enable. |
| JobDef | resources/jobdef.ps1 | Ensure-VideoJobDef, Ensure-OpsJobDef* | Job Definition 등록·drift 시 revision. |
| EventBridge | resources/eventbridge.ps1 | Ensure-EventBridgeRules | Rule/Target 갱신. |
| ASG Messaging | resources/asg_messaging.ps1 | Ensure-ASGMessaging, Confirm-ASGMessagingState | ASG/LT 존재·Desired 유지(0 덮어쓰기 금지). |
| ASG AI | resources/asg_ai.ps1 | Ensure-ASGAi, Confirm-ASGAiState | 동일. |
| API | resources/api.ps1 | Confirm-APIHealth, Get-APIInstanceByEIP | EIP로 인스턴스 찾고 academy-api 컨테이너 health 확인. |
| Build | resources/build.ps1 | Confirm-BuildInstance | 존재/상태/태그. SSM 또는 ssh 방식 중 하나로 확인(당장은 describe-instances + 태그). |
| RDS | resources/rds.ps1 | Confirm-RDSState, Ensure-RDSSecurityGroup | 상태/엔드포인트/SG. Batch SG → 5432 인바운드 없으면 추가. |
| Redis | resources/redis.ps1 | Confirm-RedisState, Ensure-RedisSecurityGroup | 상태/엔드포인트/SG. Batch SG → 6379 인바운드 없으면 추가. |
| SSM | resources/ssm.ps1 | Confirm-SSMEnv | /academy/api/env, /academy/workers/env 존재 + 키셋 검사. 갱신은 별도 스크립트(ssm_bootstrap). |
| ECR | (기존 preflight 또는 별도 ecr.ps1) | Confirm-ECRRepos | repos 존재. create-repository 없으면 생성. |

**Delete/Wait/Recreate 규칙:** Batch뿐 아니라 ASG/EC2에도 적용 가능한 범위에서 적용.
- **Batch CE:** INVALID → Queue DISABLED → CE DISABLED → delete → Wait 삭제 → Create → Wait VALID → Enable CE/Queue.
- **ASG:** 이름 변경 불가. LT만 업데이트·Desired 절대 0으로 덮어쓰지 않기. ASG 삭제 후 재생성은 수동 권장.
- **EC2 API/Build:** 재생성은 수동. Ensure는 “존재·상태·태그·health” 확인만.

**ASG:** desired 절대 0으로 덮어쓰지 않기. update-auto-scaling-group 시 현재 DesiredCapacity를 describe로 읽어 동일 값 유지.

**API:** EIP로 인스턴스 찾고, academy-api 컨테이너 health (GET /health 200) 확인.

**Build:** 존재/상태/태그/SSM 또는 ssh 방식 중 하나로 확인. 당장은 describe-instances (tag Name=academy-build-arm64) + State.

**SSM env:** common/api/workers 3계층으로 가는 migration plan은 문서화하되, 당장은 현재 2개 파라미터(/academy/api/env, /academy/workers/env)를 SSOT로 고정.

---

## 3. 실행 순서 (권장)

1. Preflight (AWS identity, VPC, SSM workers env, ECR video-worker)
2. Network (VPC/Subnet 존재 확인)
3. IAM (Batch + EventBridge)
4. RDS / Redis (상태 확인 + SG 인바운드 Ensure)
5. ECR (repo 존재 확인/생성)
6. Batch (CE → Queue → JobDef)
7. EventBridge
8. ASG (Messaging, AI) — Confirm만 또는 Ensure
9. API health, Build 존재, SSM 존재
10. Netprobe (선택)
11. Evidence + **SSOT 외 잔재 목록** 출력
12. (선택) -PruneLegacy 시 잔재 리소스 삭제 플로우

---

## 4. Evidence 및 잔재

- **Evidence 표:** 기존 deploy.ps1 Evidence와 동일 항목 + RDS/Redis/Network 요약.
- **SSOT 외 잔재 목록:** describe-compute-environments, describe-job-queues, list-rules 등에서 **SSOT에 없는 이름**만 필터해 표로 출력. (예: 다른 CE 이름, 다른 Rule 이름)
- **Prune:** -PruneLegacy 스위치로만 동작, 기본 false. 켜면 잔재 목록 출력 후 사용자 확인 또는 단계별 삭제(구현 시 확인 프롬프트 권장).

---

## 5. TODO (확인 명령/파일)

| 항목 | 내용 | 확인 방법/파일 |
|------|------|----------------|
| ALB/API | prod에서 API 노출이 EIP 직결인지 ALB+TG인지 | describe-load-balancers, describe-target-health, scripts/check_api_alb.ps1 |
| SSM 3계층 migration | common/api/workers 파라미터 구조 설계 | docs/00-SSOT/SSM-MIGRATION-PLAN.md (작성 예정) |
| Build ssh 검증 | SSM Session Manager 또는 ssh로 빌드 서버 접근 검증 | scripts_v3/resources/build.ps1 내 optional step |
| Prune 확인 프롬프트 | -PruneLegacy 시 삭제 전 yes/no | deploy_fullstack.ps1 구현 시 |
