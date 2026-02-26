# SSOT 런북 — 배포·검증·장애·롤백·점검

**역할:** 운영 단계별 절차. 배포/검증/장애 대응/롤백/정기 점검을 하나의 문서에서 참조.

---

## 1. 배포

### 1.1 전제 조건

- AWS 자격증명 설정됨(`aws sts get-caller-identity` 성공).
- 저장소 루트에 `.env` 존재, SSM_JSON_SCHEMA 필수 키 채워짐.
- ECR 이미지: academy-video-worker **immutable tag**(`:latest` 금지).

### 1.2 Pre-flight (실패 시 즉시 중단)

```powershell
.\scripts\deploy_preflight.ps1 -Region ap-northeast-2
# 선택: SSH 검증
.\scripts\deploy_preflight.ps1 -Region ap-northeast-2 -TestSsh
```

실패 시: 출력된 [FAIL] 항목 해결 후 재실행. 배포 진행하지 않음.

### 1.3 원테이크 배포 (Video + Ops + EventBridge + Netprobe)

**Public SSOT v2.0 기준(Public Subnet + IGW):**

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

.\scripts\infra\infra_full_alignment_public_one_take.ps1 `
  -Region ap-northeast-2 `
  -VpcId vpc-0831a2484f9b114c2 `
  -EcrRepoUri "<ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:<GIT_SHA_OR_TAG>" `
  -FixMode `
  -EnableSchedulers
```

- `<ACCOUNT_ID>`: `aws sts get-caller-identity --query Account --output text`
- `<GIT_SHA_OR_TAG>`: immutable tag. `latest` 입력 시 스크립트가 FAIL.
- **성공 기준:** 콘솔에 `FINAL RESULT: PASS`, `Netprobe SUCCEEDED`.

### 1.4 단계별 수동 배포 (원테이크 불가 시)

순서만 변경하지 말 것. [SSOT-ONE-TAKE-DEPLOYMENT.md](SSOT-ONE-TAKE-DEPLOYMENT.md) 및 [docs/video_batch_production_runbook.md](video_batch_production_runbook.md) 참조.

1. SSM: `.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite`
2. API 네트워크 수집: `.\scripts\infra\discover_api_network.ps1 -Region ap-northeast-2`
3. Batch Video: `.\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region ap-northeast-2 -EcrRepoUri <URI> -ComputeEnvName academy-video-batch-ce-final -JobQueueName academy-video-batch-queue`
4. Batch Ops: `.\scripts\infra\batch_ops_setup.ps1 -Region ap-northeast-2`
5. EventBridge: `.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue`
6. Netprobe: `.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-ops-queue`
7. 프로덕션 검증: `.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2`

---

## 2. 검증

### 2.1 SSOT 검증

```powershell
.\scripts\infra\verify_video_batch_ssot.ps1 -Region ap-northeast-2
```

리소스 이름·스펙이 SSOT와 일치하는지 확인.

### 2.2 SSM 형식 검증

```powershell
.\scripts\infra\verify_ssm_env_shape.ps1 -Region ap-northeast-2
```

필수 키 존재, JSON 유효.

### 2.3 EventBridge 타깃 검증

```powershell
.\scripts\infra\verify_eventbridge_wiring.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue
```

### 2.4 전체 감사 (ReadOnly)

```powershell
.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2
```

수정 적용: `-FixMode` (Ops CE/Queue 없으면 생성, IAM 부착, EventBridge 타깃 정렬).

### 2.5 Netprobe (작동 검증)

```powershell
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-ops-queue
```

기대: exit 0, "SUCCEEDED" 출력.

---

## 3. 장애 대응

### 3.1 Job RUNNABLE 정체

- **원인 후보:** CE INVALID, 서브넷 아웃바운드 없음(NAT/IGW), SG egress 부족.
- **확인:**  
  `aws batch describe-compute-environments --compute-environments academy-video-batch-ce-final --region ap-northeast-2`  
  → statusReason, computeResources.subnets 확인.
- **조치:** CE가 INVALID면 [SSOT-IDEMPOTENCY-RULES.md](SSOT-IDEMPOTENCY-RULES.md)에 따라 Queue 분리 → CE 삭제 → 재생성 → Queue 재연결. 이후 Netprobe 재실행.

### 3.2 API healthcheck 실패

- **확인:** `curl -s -o /dev/null -w "%{http_code}\n" http://15.165.147.157:8000/`
- **조치:** EC2 상태, Docker 컨테이너, SSM `/academy/api/env`(또는 API용 env) 복구. [docs/infra/API_ENV_RECOVERY_STRICT.md](infra/API_ENV_RECOVERY_STRICT.md) 등 참조.

### 3.3 Batch Job FAILED 반복

- **확인:** CloudWatch Logs `/aws/batch/academy-video-worker`, describe-jobs의 container.reason, exitCode.
- **원인 후보:** 디스크 부족, timeout, DB/Redis/R2 연결 실패, SSM env 누락.
- **조치:** 로그·SSM 스키마 점검, JobDef timeout/메모리 검토.

### 3.4 EventBridge 규칙 비활성화

- 재개: `aws events enable-rule --name academy-reconcile-video-jobs --region ap-northeast-2`  
  `aws events enable-rule --name academy-video-scan-stuck-rate --region ap-northeast-2`
- 비활성화: `disable-rule`. 상태는 [docs/deploy/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md](deploy/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md)에 기록 권장.

---

## 4. 롤백

### 4.1 Job Definition

- 이전 revision으로 복귀: register-job-definition으로 이전 스펙 재등록 후, API/배포에서 해당 revision 사용하거나 이름만 사용(최신 ACTIVE가 이전 revision이 되도록).
- **주의:** submit 시 job definition 이름만 사용하므로, 새 revision을 ACTIVE로 두지 않으면 자동으로 이전 revision 사용됨. 문제 시 새 revision을 INACTIVE 처리하는 방식은 AWS 콘솔/CLI로 수동.

### 4.2 Job Queue 비활성화(신규 Job 중단)

```bash
aws batch update-job-queue --job-queue academy-video-batch-queue --state DISABLED --region ap-northeast-2
```

재개: `--state ENABLED`.

### 4.3 EventBridge 규칙 비활성화

reconcile/scan_stuck 중단:

```bash
aws events disable-rule --name academy-reconcile-video-jobs --region ap-northeast-2
aws events disable-rule --name academy-video-scan-stuck-rate --region ap-northeast-2
```

### 4.4 Compute Environment

- CE 삭제는 Queue에서 CE 분리 → CE DISABLED → 삭제 순서 필수. RUNNING/RUNNABLE job 있으면 drain 후 진행.
- 롤백으로 "이전 CE"를 복구하는 것은 AWS Batch에서 이름 재사용 시 새 CE 생성이므로, 스펙을 이전과 동일하게 맞춘 새 CE 생성 후 Queue 재연결.

### 4.5 SSM Parameter

- 이전 값으로 복귀: 로컬에 백업한 .env 또는 JSON으로 `ssm_bootstrap_video_worker.ps1` 재실행하거나, put-parameter로 이전 값 덮어쓰기. ParameterVersion 이력 확인: `aws ssm get-parameter-history`.

---

## 5. 정기 점검

- **일일/주간:** production_done_check.ps1, Netprobe 수동 1회.
- **배포 전:** deploy_preflight.ps1, verify_video_batch_ssot.ps1.
- **상태 스냅샷:** infra_forensic_collect.ps1로 수집한 JSON을 docs/deploy/actual_state 또는 별도 디렉터리에 보관(날짜 접미사 권장).
- **EventBridge:** 규칙 State(ENABLED/DISABLED), Schedule, Target 주기 확인. 변경 시 EVENTBRIDGE_RULES_STATE_AND_FUTURE.md 갱신.

---

## 6. 참조 문서

- [SSOT-ONE-TAKE-DEPLOYMENT.md](SSOT-ONE-TAKE-DEPLOYMENT.md) — 최종 설계·순서·리소스 규칙.
- [SSOT-RESOURCE-INVENTORY.md](SSOT-RESOURCE-INVENTORY.md) — 리소스 이름·ARN.
- [SSOT-IDEMPOTENCY-RULES.md](SSOT-IDEMPOTENCY-RULES.md) — 멱등성·Wait 루프.
- [docs/video_batch_production_runbook.md](video_batch_production_runbook.md) — 상세 runbook·환경 변수.
- [docs/deploy/SSM_JSON_SCHEMA.md](deploy/SSM_JSON_SCHEMA.md) — SSM 스키마.
