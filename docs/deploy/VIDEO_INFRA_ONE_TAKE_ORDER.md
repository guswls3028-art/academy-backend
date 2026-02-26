# Video Batch 인프라 원테이크 실행 순서

**리소스 이름·스펙·성공 조건 등 모든 기준은 [VIDEO_WORKER_INFRA_SSOT_V1.md](VIDEO_WORKER_INFRA_SSOT_V1.md) 를 따른다.**  
이 문서는 실행 순서와 스크립트만 정리한다.

---

## 역할 구분 (SSOT와 동일)

| 구분 | 큐 | CE |
|------|-----|-----|
| **Video 워커** | academy-video-batch-queue | academy-video-batch-ce-final |
| **Ops** | academy-video-ops-queue | academy-video-ops-ce |

- EventBridge(reconcile/scan_stuck) 타깃 = **Ops 큐**. CloudWatch 알람 = **Video 큐**. Netprobe = **Ops 큐**.

---

## Reconcile 설정 (SSOT §6)

- **rate(15 minutes)** 고정. Redis lock key: `video:reconcile:lock`, TTL 600초.
- 환경 변수/설정: `VIDEO_BATCH_JOB_QUEUE`, `RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES` 등 — `apps/api/config/settings/base.py`, SSM `/academy/workers/env`. reconcile 코드: `apps/support/video/management/commands/reconcile_batch_video_jobs.py`.

---

## A) 처음부터 전부 세팅 (원테이크 권장)

**권장:** 한 번에 구축·Netprobe·Audit까지 수행하는 스크립트 사용.

```powershell
.\scripts\infra\video_worker_infra_one_take.ps1 -Region ap-northeast-2
```

수동 단계가 필요할 때만 아래 순서 사용. **리소스 이름·스펙은 SSOT v1.1 준수.**

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$Region = "ap-northeast-2"

.\scripts\infra\discover_api_network.ps1 -Region $Region
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region $Region -EnvFile .env -Overwrite -UsePrivateApiIp
# (선택) .\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly -Region $Region
$acctId = (aws sts get-caller-identity --query Account --output text)
$ecrUri = "${acctId}.dkr.ecr.${Region}.amazonaws.com/academy-video-worker:latest"
.\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region $Region -EcrRepoUri $ecrUri -ComputeEnvName academy-video-batch-ce-final -JobQueueName academy-video-batch-queue
.\scripts\infra\batch_ops_setup.ps1 -Region $Region
.\scripts\infra\iam_attach_batch_describe_jobs.ps1 -Region $Region
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region $Region -OpsJobQueueName academy-video-ops-queue
$q = (Get-Content docs\deploy\actual_state\batch_final_state.json -Raw | ConvertFrom-Json).FinalJobQueueName
.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region $Region -JobQueueName $q
.\scripts\infra\run_netprobe_job.ps1 -Region $Region -JobQueueName academy-video-ops-queue
.\scripts\infra\production_done_check.ps1 -Region $Region
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region -FixMode
```

- EventBridge/Netprobe는 반드시 **OpsJobQueueName / academy-video-ops-queue**. Video 큐(`$q`) 넣지 말 것.

---

## B) Ops + EventBridge + 감사만 (Video는 이미 있음)

```powershell
$Region = "ap-northeast-2"
.\scripts\infra\batch_ops_setup.ps1 -Region $Region
.\scripts\infra\iam_attach_batch_describe_jobs.ps1 -Region $Region
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region $Region -OpsJobQueueName academy-video-ops-queue
.\scripts\infra\infra_one_take_full_audit.ps1 -Region $Region -FixMode
```

---

## 현재 인프라 상태 확인

- Video: `docs\deploy\actual_state\batch_final_state.json`
- Ops: `docs\deploy\actual_state\batch_ops_state.json`
- 감사: `.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2`
- SSOT 검증: `.\scripts\infra\verify_video_batch_ssot.ps1 -Region ap-northeast-2`

---

## One-shot / 프로덕션 정합

- **One-shot (CE 정리):** `scripts/infra/one_shot_video_ce_final.ps1` — 단일 Video CE `academy-video-batch-ce-final` 고정용. SSOT 이름 준수.
- **프로덕션 정합 (멱등):** `.\scripts\infra\reconcile_video_batch_production.ps1 -Region ap-northeast-2 -VideoCEName academy-video-batch-ce-final`
- **프로덕션 원테이크:** `scripts/infra/video_worker_infra_one_take.ps1` — SSOT v1.1 강제, Evidence·Netprobe·Audit 포함.

---

**모든 리소스 이름·스펙·성공 조건:** [VIDEO_WORKER_INFRA_SSOT_V1.md](VIDEO_WORKER_INFRA_SSOT_V1.md)
