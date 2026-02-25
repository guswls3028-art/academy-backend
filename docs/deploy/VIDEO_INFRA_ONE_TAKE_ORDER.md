# Video Batch 인프라 원테이크 실행 순서 (SSOT)

이 문서는 **현재 레포 기준** 인프라 설정 순서와 파라미터를 하나로 고정한 것이다.  
방향이 흔들리지 않도록 단일 순서만 적어 둠.

---

## 역할 구분 (혼동 금지)

| 구분 | 큐 | CE | 용도 |
|------|-----|-----|------|
| **Video 워커** | `academy-video-batch-queue` | `academy-video-batch-ce-final` (단일 CE SSOT; 레거시: v2, academy-video-batch-ce) | 영상 인코딩 |
| **Ops** | `academy-video-ops-queue` | `academy-video-ops-ce` | reconcile, scan_stuck, netprobe |

- EventBridge(reconcile/scan_stuck) 타깃 = **Ops 큐** (`academy-video-ops-queue`). Video 큐 아님.
- CloudWatch 알람 = **Video 큐** (`academy-video-batch-queue` 또는 batch_final_state.json의 FinalJobQueueName).
- Netprobe 제출 = **Ops 큐** (`academy-video-ops-queue`).

---

## Reconcile 관련 설정

reconcile은 EventBridge로 **15분마다** **Ops 큐**에서 `reconcile_batch_video_jobs` 가 돌며, **Video 큐**의 job과 DB 정합을 맞춘다. 아래는 그 동작을 바꾸는 설정이다.

### 환경 변수 / Django 설정 (`apps/api/config/settings/base.py`)

| 설정 | 환경 변수 | 기본값 | 설명 |
|------|-----------|--------|------|
| Video 큐 이름 | `VIDEO_BATCH_JOB_QUEUE` | `academy-video-batch-queue` | reconcile가 orphan을 찾는 큐. API/워커와 동일해야 함. |
| orphan 최소 대기 시간(분) | `RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES` | `15` | RUNNABLE orphan을 terminate하기 전에 “RUNNABLE인 지속 시간”이 이 값 이상이고, CE `desiredvCpus > 0`일 때만 terminate. 스케일 대기 중인 job 보호용. |
| orphan terminate 끄기 | `RECONCILE_ORPHAN_DISABLED` | (비설정) | `1` / `true` / `yes` 이면 orphan terminate 블록 전체 스킵. 긴급 시 스위치로 사용. |

- 적용 위치: API 서버는 `.env` / OS 환경변수. **Batch 컨테이너(reconcile job)** 는 SSM `/academy/workers/env` JSON에 넣어두면 entrypoint가 읽음. SSM은 `ssm_bootstrap_video_worker.ps1` 로만 갱신.
- reconcile 코드: `apps/support/video/management/commands/reconcile_batch_video_jobs.py`

### EventBridge 규칙 (스케줄 on/off)

| 규칙 이름 | 역할 | 끄기 | 다시 켜기 |
|-----------|------|------|------------|
| `academy-reconcile-video-jobs` | 15분마다 reconcile job 제출 (Ops 큐) | `aws events put-rule --name academy-reconcile-video-jobs --state DISABLED --schedule-expression "rate(15 minutes)" --description "..." --region ap-northeast-2` | `--state ENABLED` 로 동일 호출 |

- 재배포 후 reconcile 코드/설정 반영이 끝나면 ENABLED로 다시 켜면 됨.
- **현재 규칙 상태·Ops 백로그 정리·향후 삭제/업로드 인프라 검토:** `docs/deploy/EVENTBRIDGE_RULES_STATE_AND_FUTURE.md` 참고.

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

- **SSOT 검증(원테이크):**  
  `.\scripts\infra\verify_video_batch_ssot.ps1 -Region ap-northeast-2`  
  Video/Ops 정렬, 1동영상=1워커, CE 스케일 경로, 네트워크, IAM, EventBridge, reconcile 설정까지 한 번에 PASS/FAIL + evidence 출력. root 자격증명 사용 시 exit 3.

---

## One-shot: 꼬인 Video CE 정리 후 단일 CE로 고정

여러 Video CE(v2/v3/public 등)가 섞여 있을 때 **한 번만** 실행해, 기존 CE는 정지하고 **단일 Video CE(`academy-video-batch-ce-final`)만** 쓰도록 큐를 고정하는 스크립트.

| 항목 | 내용 |
|------|------|
| **스크립트** | `scripts/infra/one_shot_video_ce_final.ps1` |
| **실행** | `.\scripts\infra\one_shot_video_ce_final.ps1` (상단 `$Region`, `$OldVideoCEs`, `$FinalVideoCE` 필요 시 수정) |
| **동작** | (0) 스케줄러 비활성화(주석 처리됨) (1) 기존 CE **DISABLED만** (desiredvCpus=0은 큐 연결 시 API 불가) (2) VideoQ/OpsQ job cancel·terminate (3) 기존 CE에서 VPC/Subnets/SG/역할 읽기 (4) Public subnet 강화(IGW, 라우트, MapPublicIpOnLaunch, SG egress) (5) IAM 역할 부착 (6) `academy-video-batch-ce-final` 생성(없을 때만) (7) Video 큐를 해당 CE만 쓰도록 update (8) evidence 출력 |
| **주의** | One-shot 용도. 평소 정합은 `reconcile_video_batch_production.ps1` 사용. one_shot 이후 reconcile을 돌릴 때는 `-VideoCEName academy-video-batch-ce-final` 로 CE 이름을 맞출 것. SSOT 이름은 본문의 큐/CE 표와 맞출 것. |

---

## 프로덕션 정합 (기존 리소스만 수정, 멱등)

CE/큐/JobDef/EventBridge/알람을 **기존 이름 기준**으로만 맞추는 스크립트. 새 CE/큐 생성 없음.

| 항목 | 내용 |
|------|------|
| **스크립트** | `scripts/infra/reconcile_video_batch_production.ps1` |
| **실행** | `.\scripts\infra\reconcile_video_batch_production.ps1 -Region ap-northeast-2` |
| **Video CE가 final인 경우** | `-VideoCEName academy-video-batch-ce-final` 추가 (기본값은 v2). |
| **기본 파라미터** | Video 큐 `academy-video-batch-queue`, Video JobDef `academy-video-batch-jobdef`, Ops 큐/CE/JobDef 이름은 스크립트 상단 param 참고. |
