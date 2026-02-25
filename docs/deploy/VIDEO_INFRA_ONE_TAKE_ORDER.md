# Video Batch 인프라 원테이크 실행 순서 (SSOT)

이 문서는 **현재 레포 기준** 인프라 설정 순서와 파라미터를 하나로 고정한 것이다.  
방향이 흔들리지 않도록 단일 순서만 적어 둠.

---

## 역할 구분 (혼동 금지)

| 구분 | 큐 | CE | 용도 |
|------|-----|-----|------|
| **Video 워커** | `academy-video-batch-queue` | `academy-video-batch-ce-v2` (또는 academy-video-batch-ce) | 영상 인코딩 |
| **Ops** | `academy-video-ops-queue` | `academy-video-ops-ce` | reconcile, scan_stuck, netprobe |

- EventBridge(reconcile/scan_stuck) 타깃 = **Ops 큐** (`academy-video-ops-queue`). Video 큐 아님.
- CloudWatch 알람 = **Video 큐** (`academy-video-batch-queue` 또는 batch_final_state.json의 FinalJobQueueName).
- Netprobe 제출 = **Ops 큐** (`academy-video-ops-queue`).

---

## A) 처음부터 전부 세팅 (API VPC + Video + Ops + EventBridge + 알람 + 검증)

저장소 루트에서 PowerShell. `.env` 준비됨 가정.

```powershell
# UTF-8 (Windows cp949 / SSM JSON 방지)
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$Region = "ap-northeast-2"

# 0) API Private IP 추출 (api_instance.json → SSM에서 API_BASE_URL 치환에 사용)
.\scripts\infra\discover_api_network.ps1 -Region $Region

# 1) .env → SSM (Private IP로 API_BASE_URL 설정)
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region $Region -EnvFile .env -Overwrite -UsePrivateApiIp

# 2) (선택) 이미지 빌드·푸시
.\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly

# 3) Batch in API VPC (Video CE/Queue/JobDef). batch_final_state.json 생성됨.
$acctId = (aws sts get-caller-identity --query Account --output text)
$ecrUri = "${acctId}.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
.\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region $Region -EcrRepoUri $ecrUri

# 4) Ops CE + Ops Queue (reconcile/scan_stuck/netprobe 전용, min0 max2 vCPU)
.\scripts\infra\batch_ops_setup.ps1 -Region $Region

# 5) EventBridge: reconcile/scan_stuck → Ops 큐로 제출 (파라미터는 OpsJobQueueName)
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region $Region -OpsJobQueueName academy-video-ops-queue

# 6) CloudWatch 알람: Video 큐 기준 (batch_final_state.json 사용)
$q = (Get-Content docs\deploy\actual_state\batch_final_state.json -Raw | ConvertFrom-Json).FinalJobQueueName
.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region $Region -JobQueueName $q

# 7) Netprobe: Ops 큐에 제출 (Video 큐 아님)
.\scripts\infra\run_netprobe_job.ps1 -Region $Region -JobQueueName academy-video-ops-queue

# 8) 검증
.\scripts\infra\production_done_check.ps1 -Region $Region

# 9) (선택) 전체 감사
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region -FixMode
```

---

## B) Ops + EventBridge + 감사만 (Video CE/Queue는 이미 있음)

Video Batch는 이미 만들어져 있고, Ops·EventBridge·IAM·감사만 맞추고 싶을 때.

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$Region = "ap-northeast-2"

.\scripts\infra\batch_ops_setup.ps1 -Region $Region
.\scripts\infra\iam_attach_batch_describe_jobs.ps1 -Region $Region
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region $Region -OpsJobQueueName academy-video-ops-queue
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region
# 필요 시 수정까지 적용
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region -FixMode
```

이후 앱/코드 배포 (reconcile_batch_video_jobs.py 등 포함).

---

## 사용자가 적어둔 블록에서 고칠 점

- **EventBridge:** `-JobQueueName $q` 가 아님. 스크립트 파라미터는 **-OpsJobQueueName** 하나뿐이고, 값은 **academy-video-ops-queue** 여야 함.  
  `$q`(Video 큐)를 넘기면 안 됨.
- **Netprobe:** `-JobQueueName $q` 가 아님. Netprobe는 **Ops 큐**에만 제출.  
  `-JobQueueName academy-video-ops-queue` 로 고정.
- **CloudWatch:** `-JobQueueName $q` 는 맞음. 단 `$q`는 **Video 큐** 이름이어야 하므로 `batch_final_state.json`의 `FinalJobQueueName`에서 읽는 것이 맞음.

즉, 넣어둔 순서는 대체로 맞고, **EventBridge는 OpsJobQueueName, Netprobe는 Ops 큐**로만 쓰면 된다.

---

## 현재 인프라 상태 확인

- Video 큐/CE: `docs\deploy\actual_state\batch_final_state.json` → `FinalJobQueueName`, `FinalComputeEnvName`
- Ops: `docs\deploy\actual_state\batch_ops_state.json` (batch_ops_setup.ps1 실행 후 생성)
- 감사로 전체 정합 한 번에 점검:  
  `.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2`
