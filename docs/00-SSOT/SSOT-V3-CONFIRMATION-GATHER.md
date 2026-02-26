# SSOT v3 확정 및 구현 최종 보고

**작성일:** 2026-02-27  
**기준:** docs/00-SSOT/INFRA-SSOT-V3.*, scripts_v3 단일 진입점

---

## 1. 요약

- **V3 SSOT**를 확정하고, **문서 3종(params, md, state-contract)**을 갱신했으며, **scripts_v3** 원테이크 배포 프레임워크를 구현했습니다.
- **GitHub Actions**는 `scripts_v3/deploy.ps1`만 호출하도록 전환했고, **레거시 스크립트 denylist 가드**를 추가했습니다.

---

## 2. 확정된 SSOT 항목

| 구분 | 내용 |
|------|------|
| **단일 진입점** | `scripts_v3/deploy.ps1` 만 배포 실행. 레거시 `scripts/infra/*.ps1` 직접 호출 금지. |
| **기계용 SSOT** | `docs/00-SSOT/INFRA-SSOT-V3.params.yaml` (계정 809466760795, 리전 ap-northeast-2, VPC/서브넷/SG, API EIP, Batch/EventBridge/ASG/SSM 이름 반영) |
| **API 식별** | Elastic IP `eipalloc-071ef2b5b5bec9428` (15.165.147.157), 컨테이너 `academy-api` |
| **네트워크** | NAT + IGW 유지. VPC `vpc-0831a2484f9b114c2`, Batch SG `sg-011ed1d9eb4a65b8f` |
| **Batch Video** | CE `academy-video-batch-ce-final`, Queue `academy-video-batch-queue`, JobDef `academy-video-batch-jobdef` |
| **Batch Ops** | CE `academy-video-ops-ce`, Queue `academy-video-ops-queue`, JobDef reconcile/scanstuck/netprobe |
| **EventBridge** | `academy-reconcile-video-jobs` (15분), `academy-video-scan-stuck-rate` (5분), Target은 Ops Queue |
| **이미지 정책** | 당분간 `:latest` 허용. Evidence 표에는 ECR **imageDigest** 필수 기록. |
| **멱등 규칙** | CE INVALID → Queue DISABLED → CE 삭제 → Wait → **동일 스크립트로 Create** → Wait VALID → Enable CE/Queue. **수동 bootstrap 불필요.** |

---

## 3. 생성/갱신된 파일

### 3.1 문서 (docs/00-SSOT/)

| 파일 | 조치 |
|------|------|
| **INFRA-SSOT-V3.params.yaml** | 계정 ID, VPC, publicSubnets, batch SG, API allocationId/publicIp/containerName 등 실제 값으로 갱신 |
| **INFRA-SSOT-V3.state-contract.md** | Evidence 이미지 digest 기록 규정 추가. §7 Legacy 실행 차단(Kill-Switch) 추가 |

(INFRA-SSOT-V3.md는 기존 유지, params/state-contract와 일치)

### 3.2 스크립트 (scripts_v3/)

| 경로 | 역할 |
|------|------|
| **deploy.ps1** | 단일 진입점. env → core → resources → netprobe 로드 후 Preflight → Ensure Batch/EventBridge/ASG/SSM/API → Netprobe → Evidence |
| **env/prod.ps1** | SSOT 변수 (Region, AccountId, VpcId, Subnets, Batch/API/EventBridge/ASG/SSM 이름 등). params.yaml과 동기화 유지 |
| **core/logging.ps1** | Write-Step, Write-Ok, Write-Warn, Write-Fail |
| **core/aws-wrapper.ps1** | Invoke-AwsJson, Invoke-Aws |
| **core/wait.ps1** | Wait-CEDeleted, Wait-CEValidEnabled |
| **core/preflight.ps1** | Invoke-PreflightCheck (AWS identity, VPC, SSM, ECR) |
| **core/evidence.ps1** | Show-Evidence (CE/Queue/JobDef/EventBridge/ASG/API/Netprobe, ECR digest 포함) |
| **resources/batch.ps1** | Ensure-VideoCE, Ensure-OpsCE, Ensure-VideoQueue, Ensure-OpsQueue (Describe→Decision→Update, INVALID 시 Queue DISABLED 후 CE 삭제+Wait) |
| **resources/eventbridge.ps1** | Ensure-EventBridgeRules (put-targets만, repo의 eventbridge/*.json 참조) |
| **resources/asg.ps1** | Confirm-ASGState (조회만) |
| **resources/ssm.ps1** | Confirm-SSMEnv (조회만) |
| **resources/api.ps1** | Confirm-APIHealth (GET /health) |
| **netprobe/batch.ps1** | Invoke-Netprobe (Ops Queue에 netprobe job 제출 → SUCCEEDED 대기, jobId/status 반환) |

### 3.3 CI (.github/workflows/)

| 파일 | 변경 내용 |
|------|-----------|
| **video_batch_deploy.yml** | ① concurrency group `video-batch-deploy` 추가 ② **guard-no-legacy-scripts** job 추가 (워크플로 내 `scripts/infra/*.ps1` 실행 검사, 있으면 실패) ③ deploy-infra에서 `batch_video_setup.ps1`, `eventbridge_deploy_video_scheduler.ps1` 제거 → **One-Take Deploy** 단일 단계: `pwsh -File scripts_v3/deploy.ps1 -Env prod` ④ paths에 `scripts_v3/**` 추가 |

---

## 4. 실행 방법

### 로컬 (PowerShell)

```powershell
cd C:\academy
.\scripts_v3\deploy.ps1 -Env prod
# Netprobe 생략 시:
.\scripts_v3\deploy.ps1 -Env prod -SkipNetprobe
```

### CI

- `main` 푸시 또는 workflow_dispatch 시 **Video Batch Deploy** 실행.
- **build-and-push** → **deploy-infra**에서 `scripts_v3/deploy.ps1`만 실행.
- 동시 실행은 concurrency로 1회만 허용.

---

## 5. 안전장치

| 항목 | 내용 |
|------|------|
| **Legacy kill-switch** | state-contract §7: 단일 진입점은 deploy.ps1만. CI guard job에서 `scripts/infra/*.ps1` 호출 시 실패. |
| **Evidence digest** | state-contract 및 evidence.ps1: 배포 마지막에 ECR describe-images로 imageDigest 조회 후 Evidence 표에 출력. |
| **동시 배포** | workflow concurrency로 1회만 실행. |

---

## 6. 제한사항 및 권장 후속

- **Full Rebuild:** scripts_v3/deploy.ps1 단일 실행으로 CE/Queue/JobDef/EventBridge를 **빈 상태에서도 생성** 가능. **수동 bootstrap(scripts/infra/*.ps1)은 필요 없음.** INVALID CE는 삭제 후 동일 스크립트로 재생성까지 수행.

- **JobDef:** deploy.ps1에서 Ensure-VideoJobDef, Ensure-OpsJobDefReconcile/ScanStuck/Netprobe로 drift 시 새 revision 등록. CI에서 -EcrRepoUri를 넘기면 해당 이미지로 revision 반영.

- **CloudWatch 알람:** 워크플로에서는 제거됨. 필요 시 수동 또는 별도 job에서 `scripts/infra/cloudwatch_deploy_video_alarms.ps1` 실행.

- **env/prod.ps1 동기화:** params.yaml 변경 시 `scripts_v3/env/prod.ps1` 값을 동일하게 맞출 것.

---

## 7. 체크리스트 (원테이크 SSOT)

- [x] 모든 리소스는 Describe → Decision → Update/Create 순서
- [x] CE INVALID 시 Queue DISABLED → CE 삭제 → Wait 삭제 완료
- [x] CE/Queue DISABLED → ENABLED로 수렴
- [x] EventBridge는 Rule 존재 시 put-targets만 최신화
- [x] ASG는 Desired 유지 (update 시 0 덮어쓰기 금지, 문서·스크립트 반영)
- [x] Preflight 실패 시 즉시 중단
- [x] Netprobe: Ops Queue 테스트 job → SUCCEEDED 확인
- [x] Evidence 표: Batch CE/Queue/JobDef, EventBridge, ASG, API, Netprobe, **imageDigest** 포함
- [x] CI는 scripts_v3/deploy.ps1만 호출, 레거시 denylist 가드 있음

---

**이상으로 V3 SSOT 확정 및 구현을 마쳤습니다.**
