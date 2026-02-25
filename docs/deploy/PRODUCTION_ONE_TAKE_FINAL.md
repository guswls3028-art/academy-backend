# Production One-Take: 최종 변경 목록 및 원테이크 실행

## 1) 수정된 파일 목록

| 파일 | 구분 |
|------|------|
| `scripts/infra/ssm_bootstrap_video_worker.ps1` | A |
| `scripts/infra/batch_video_setup.ps1` | B |
| `scripts/infra/recreate_batch_in_api_vpc.ps1` | C |
| `scripts/infra/verify_batch_network_connectivity.ps1` | D |
| `scripts/infra/production_done_check.ps1` | E |

---

## 2) 변경 요약

### A) ssm_bootstrap_video_worker.ps1
- **ordered hashtable** → `ConvertTo-Json -Compress -Depth 10` → JSON 유효성 검증(ConvertFrom-Json round-trip, DJANGO_SETTINGS_MODULE=worker) 유지.
- **AWS CLI 호출을 ArgumentList(배열) 방식으로 통일**: `put-parameter`는 `& aws @('ssm','put-parameter','--name',$ParamName,'--value',$valueBase64,...)`, `get-parameter`는 `& aws @getArgs` 형태로 변경.
- put 후 get-parameter로 Base64 디코딩 후 JSON 재검증 로직 유지.

### B) batch_video_setup.ps1
- **EcrRepoUri 엄격 검증** 유지: 정규식 `^\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/...`, `<` `>`/공백 포함 시 즉시 exit 1.
- **JobDef 등록 후 image URI 로그**: Worker jobdef 등록 직후 `JobDef image URI: $EcrRepoUri` 출력.
- **ECR image digest 출력**: EcrRepoUri에서 repo/tag 파싱 후 `aws ecr describe-images`로 imageDigest 출력(가능 시).
- `register-job-definition` 호출을 `& aws @('batch','register-job-definition',...)` 배열 방식으로 변경.

### C) recreate_batch_in_api_vpc.ps1
- **AccountId 자동 감지**: 스크립트 시작 시 `aws sts get-caller-identity --query Account --output text`로 계정 ID 조회.
- **`<acct>` placeholder 자동 치환**: EcrRepoUri에 `<acct>`가 있으면 위에서 구한 AccountId로 치환 후 기존 EcrRepoUri 검증 실행. 그 외 `<`/`>` 포함 시 기존대로 즉시 에러.

### D) verify_batch_network_connectivity.ps1
- **ECS cluster identifier**: containerInstanceArn에서 cluster ARN 추출 후 `describe-container-instances --cluster $clusterArn`에 **전체 ARN** 전달하도록 유지.
- **Null 배열 방지**: `$ci.containerInstances`를 `@(if ($ci -and $ci.containerInstances) { $ci.containerInstances } else { @() })`로 감싸서 빈 배열 처리.
- `describe-container-instances`, `describe-instances` 호출을 ArgumentList 방식으로 변경.

### E) production_done_check.ps1
- **CE 검증 강화**: CE가 ENABLED·VALID가 아니면 FAIL (state/status 명시).
- **Netprobe SUCCEEDED 시 container exitCode 확인**: exitCode가 0이 아니면 FAIL.
- **PASS 시 출력 추가**: `PRODUCTION DONE CHECK: PASS` 다음 줄에 `VIDEO WORKER PRODUCTION READY` 출력.

---

## 3) 원테이크 실행 블록 (복붙 PowerShell)

저장소 루트에서 PowerShell로 실행. `.env`는 `.env.example`에서 복사 후 필수 값 채워 둔 상태여야 함.

```powershell
# UTF-8
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

# Account auto-detect
$acctId = (aws sts get-caller-identity --query Account --output text)

# 1) .env -> SSM (JSON, SecureString)
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite

# 2) Docker build & push (Video Worker only)
.\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly

# 3–4) Batch CE/Queue 정합 확인 및 Job Definitions 재등록
$ecrUri = "${acctId}.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
.\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region ap-northeast-2 -EcrRepoUri $ecrUri

# 5) EventBridge wiring
$q = (Get-Content (Join-Path $PWD "docs\deploy\actual_state\batch_final_state.json") -Raw | ConvertFrom-Json).FinalJobQueueName
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -JobQueueName $q

# 6) CloudWatch alarms
.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName $q

# 7) Netprobe SUCCESS
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName $q

# 8) production_done_check PASS
.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
```

**성공 시 마지막에 출력되는 문구:**
- `PRODUCTION DONE CHECK: PASS`
- `VIDEO WORKER PRODUCTION READY`

모든 단계는 실패 시 exit code 비0으로 종료된다.
