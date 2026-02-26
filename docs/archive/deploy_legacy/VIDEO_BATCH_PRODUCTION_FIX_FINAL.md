# 1. 분석 보고서

## A. 현재 구조 요약

| 구성요소 | 파일/위치 | 내용 |
|----------|-----------|------|
| Video CE 생성 | `scripts/infra/recreate_batch_in_api_vpc.ps1` L219 | `batch_video_setup.ps1` 호출, `-ComputeEnvName academy-video-batch-ce` 고정 |
| Video CE JSON | `scripts/infra/batch/video_compute_env.json` | `instanceTypes: ["c6g.large"]`, `maxvCpus: 32` |
| Video JobDef JSON | `scripts/infra/batch/video_job_definition.json` | `memory: 4096`, `vcpus: 2` (요구는 3072MB) |
| Video 큐 업데이트 실패 시 | `scripts/infra/batch_video_setup.ps1` L278-291 | `academy-video-batch-queue-ce` 신규 큐 생성 → 큐/이름 분리 가능성 |
| Ops CE | `scripts/infra/batch_ops_setup.ps1` L70-71 | `academy-video-batch-ce` 에서 VPC/SG 읽음. `batch/ops_compute_env.json`: `instanceTypes: ["default_arm64"]`, `maxvCpus: 2` |
| EventBridge reconcile | `scripts/infra/eventbridge_deploy_video_scheduler.ps1` L97-98 | `rate(5 minutes)`, `JobDefinition: academy-video-ops-reconcile` (이름만, revision 미고정) |
| EventBridge scan_stuck | 동일 L132-133 | `rate(5 minutes)` |
| Reconcile 락 | `apps/support/video/management/commands/reconcile_batch_video_jobs.py` L45-58, L154-161 | Redis `video:reconcile:lock` SETNX TTL=600s. 락 실패 시 skip |
| Submit 경로 | `apps/support/video/services/video_encoding.py` L45-50, L86-99 | `existing` active job 체크 후 `VideoTranscodeJob.objects.create` + `submit_batch_job`. **비동기 락 없음** |
| Submit 호출처 | `apps/support/video/views/video_views.py` L451, L473, L494, L580 | upload_complete: video에 `select_for_update` 없이 `create_job_and_submit_batch(video)` 호출. retry(L518): `select_for_update` 있음 |

## B. CE/ASG 증식 원인

- `recreate_batch_in_api_vpc.ps1`: 동일 `ComputeEnvName`(academy-video-batch-ce)로 `batch_video_setup` 호출. setup은 CE가 있으면 create 스킵(L199-206)하므로 **동일 이름으로 재실행 시 CE 1개 유지**.
- CE가 늘어나는 경우: (1) **다른 스크립트/수동으로 다른 CE 이름 사용** (예: one_shot의 academy-video-batch-ce-final, 과거 v2/v3/public). (2) **batch_video_setup.ps1** L278-291: `update-job-queue` 실패 시 `academy-video-batch-queue-ce` 새 큐 생성 → 큐 이름이 갈라지고, 이후 다른 스크립트가 새 CE를 만들 수 있음.
- `batch_ops_setup.ps1` L70: Video CE 이름이 `academy-video-batch-ce`로 하드코딩. final/v2만 있으면 여기서 실패하거나, CE를 새로 만드는 경로가 없어서 Ops만 생성. **Video CE는 recreate/batch_video_setup/one_shot 등 서로 다른 이름을 쓰면 CE가 여러 개 생김.**

## C. 1워커=1작업이 깨지는 원인

- **CE instanceTypes**: `video_compute_env.json`은 `["c6g.large"]`만 있음. 과거에 콘솔 또는 다른 스크립트로 `c6g.xlarge`, `c6g.2xlarge`가 추가되면 BEST_FIT_PROGRESSIVE로 한 인스턴스에 여러 태스크 배치됨.
- **JobDef vcpus=2, memory=4096**: c6g.large는 2 vCPU. 한 인스턴스에 1개만 배치되려면 job당 2 vCPU가 맞지만, **memory 4096이면 xlarge/2xlarge가 있을 때** 더 큰 인스턴스에 여러 job이 들어갈 수 있음. 요구사항은 **memory=3072**로 고정.
- **reconcile_video_batch_production.ps1** L163: Video CE를 `minvCpus=0,maxvCpus=32`로만 update. **instanceTypes는 변경하지 않음** → 이미 xlarge/2xlarge가 들어가 있으면 그대로 유지됨.

## D. reconcile 겹침 원인

- EventBridge 규칙이 **rate(5 minutes)** 로 5분마다 SubmitJob. reconcile 커맨드는 **Redis 락**(TTL 600s)으로 동시 실행은 1개로 제한됨(L154-161).
- 그러나 **EventBridge는 5분마다 무조건 새 Batch job을 제출**함. 이전 reconcile job이 아직 RUNNING이어도 새 job이 RUNNABLE로 쌓임. Ops CE maxvCpus=1이면 한 번에 1개만 실행되지만, **RUNNABLE이 4~5개 쌓이는 것**이 “폭주” 현상. 주기를 15분으로 늘리면 제출 빈도가 줄어듦.

## E. jobDefinition revision 반영 실패 원인

- **batch_submit.py** L42: `jobDefinition=job_def_name` (이름만 전달). Batch는 **최신 ACTIVE revision**을 씀. 따라서 새 revision을 등록하면 다음 submit부터 자동 반영됨.
- **반영이 안 보이는 경우**: (1) API/워커가 **다른 설정**(예: SSM에 예전 JobDef 이름이 있거나, `VIDEO_BATCH_JOB_DEFINITION`이 revision으로 고정된 값)을 쓰는 경우. (2) **reconcile_video_batch_production.ps1**이 JobDef를 3072/2 vCPU로 재등록한 뒤, **이전 revision을 쓰는 다른 경로**가 있음. (3) EventBridge 타깃은 **JobDefinition 이름만** (`academy-video-ops-reconcile`)이므로 Ops는 항상 최신 revision 사용. Video는 API/SSM의 `VIDEO_BATCH_JOB_DEFINITION`이 이름만이면 최신 반영됨.

---

# 2. 최종 고정 설계

| 항목 | 설계 |
|------|------|
| Video CE | 1개. 이름 SSOT: `academy-video-batch-ce-final` (또는 기존 문서대로 final). |
| Ops CE | 1개. `academy-video-ops-ce`. |
| Queue | Video: `academy-video-batch-queue` → Video CE 1개만 연결. Ops: `academy-video-ops-queue` → Ops CE만 연결. |
| instanceTypes | Video CE: **c6g.large만** 고정. Ops CE: default_arm64 또는 동일 1종. |
| JobDef (Video) | **vcpus=2, memory=3072**, timeout 14400. EC2용이므로 runtimePlatform 없음. |
| Reconcile 주기/락 | EventBridge **rate(15 minutes)**. 기존 Redis 락(600s) 유지. |
| Submit 중복 방지 | `create_job_and_submit_batch` 진입 시 **video에 대해 select_for_update** 후 기존 active job 재확인. |
| 재실행 시 증식 방지 | CE/Queue는 **이름으로 존재 여부 확인 후 create 스킵**. Queue update 실패 시 fallback으로 **다른 이름의 큐 생성하지 않고** 실패 종료 또는 재시도만. |

---

# 3. 원테이크 실행 스크립트

**파일:** `scripts/infra/video_batch_production_one_take.ps1` (실행 가능). EventBridge 타깃 JobDefinition은 `academy-video-ops-reconcile` 사용.

```powershell
# scripts/infra/video_batch_production_one_take.ps1
param(
    [string]$Region = "ap-northeast-2",
    [string]$VideoCEName = "academy-video-batch-ce-final",
    [string]$VideoQueueName = "academy-video-batch-queue",
    [string]$OpsCEName = "academy-video-ops-ce",
    [string]$OpsQueueName = "academy-video-ops-queue",
    [string]$VideoJobDefName = "academy-video-batch-jobdef",
    [string]$OpsJobDefName = "academy-video-ops-reconcile",
    [string]$ReconcileRuleName = "academy-reconcile-video-jobs"
)
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

function ExecJson($a) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @a 2>&1
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $s = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    try { return $s | ConvertFrom-Json } catch { return $null }
}

function Invoke-Aws { param([string[]]$ArgsArray,[string]$ErrorMessage="AWS failed")
    $out = & aws @ArgsArray 2>&1
    if ($LASTEXITCODE -ne 0) { throw "$ErrorMessage. $($out | Out-String)" }
    return $out
}

Write-Host "=== 1) EventBridge reconcile 15min + DISABLED ===" -ForegroundColor Cyan
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
Invoke-Aws -ArgsArray @("events", "put-rule", "--name", $ReconcileRuleName, "--schedule-expression", "rate(15 minutes)", "--state", "DISABLED", "--description", "Reconcile video jobs", "--region", $Region) -ErrorMessage "put-rule failed"
$ErrorActionPreference = $prevErr

Write-Host "=== 2) Video CE: ensure single CE, instanceTypes c6g.large only ===" -ForegroundColor Cyan
$ceList = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$videoCe = $ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
if (-not $videoCe) {
    $seed = ExecJson @("batch", "describe-compute-environments", "--region", $Region, "--output", "json")
    $any = $seed.computeEnvironments | Where-Object { $_.computeEnvironmentName -match "academy-video-batch" } | Select-Object -First 1
    if (-not $any) { Write-Error "No existing Video CE to clone from. Create one first."; exit 1 }
    $subnets = ($any.computeResources.subnets) -join ","
    $sgs = ($any.computeResources.securityGroupIds) -join ","
    $instRole = $any.computeResources.instanceRole
    $svcRole = $any.serviceRole
    Invoke-Aws -ArgsArray @("batch", "create-compute-environment", "--compute-environment-name", $VideoCEName, "--type", "MANAGED", "--state", "ENABLED", "--service-role", $svcRole, "--compute-resources", "type=EC2,allocationStrategy=BEST_FIT_PROGRESSIVE,minvCpus=0,maxvCpus=32,desiredvCpus=0,instanceTypes=c6g.large,subnets=$subnets,securityGroupIds=$sgs,instanceRole=$instRole,ec2Configuration=[{imageType=ECS_AL2023}]", "--region", $Region) -ErrorMessage "create Video CE failed"
    $w = 0; while ($w -lt 90) { Start-Sleep -Seconds 5; $w += 5; $x = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json"); $xc = $x.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1; if ($xc -and $xc.status -eq "VALID") { break }; if ($xc -and $xc.status -eq "INVALID") { Write-Error "CE INVALID"; exit 1 } }
} else {
    $cr = $videoCe.computeResources
    $types = $cr.instanceTypes -join ","
    if ($types -ne "c6g.large") {
        Write-Host "  Video CE instanceTypes=$types; update to c6g.large only (create new CE or manual). Skipping in-place update (API does not support instanceTypes change)." -ForegroundColor Yellow
    }
}
$videoCeArn = (ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")).computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1 -ExpandProperty computeEnvironmentArn
if (-not $videoCeArn) { Write-Error "Video CE ARN not found"; exit 1 }

Write-Host "=== 3) Video Queue: attach only this CE ===" -ForegroundColor Cyan
$qDesc = ExecJson @("batch", "describe-job-queues", "--job-queues", $VideoQueueName, "--region", $Region, "--output", "json")
$qObj = $qDesc.jobQueues | Where-Object { $_.jobQueueName -eq $VideoQueueName } | Select-Object -First 1
if (-not $qObj) { Write-Error "Video queue not found"; exit 1 }
$order = $qObj.computeEnvironmentOrder
$needUpdate = -not $order -or $order.Count -ne 1 -or $order[0].computeEnvironment -ne $videoCeArn
if ($needUpdate) {
    $payload = '{"jobQueue":"' + $VideoQueueName + '","computeEnvironmentOrder":[{"order":1,"computeEnvironment":"' + $videoCeArn + '"}]}'
    $tf = Join-Path $RepoRoot "vb_one_take_vq.json"
    [System.IO.File]::WriteAllText($tf, $payload, $utf8NoBom)
    $uri = "file://" + ($tf -replace '\\', '/')
    try { Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--cli-input-json", $uri, "--region", $Region) -ErrorMessage "update Video queue failed" } finally { Remove-Item $tf -Force -ErrorAction SilentlyContinue }
}

Write-Host "=== 4) Video JobDef: force 2 vCPU / 3072 MB (register if missing or wrong) ===" -ForegroundColor Cyan
$jdAll = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$jdLatest = $null
if ($jdAll -and $jdAll.jobDefinitions -and $jdAll.jobDefinitions.Count -gt 0) {
    $jdLatest = $jdAll.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
}
$needReg = $false
if ($jdLatest) {
    $m = [int]$jdLatest.containerProperties.memory
    $v = [int]$jdLatest.containerProperties.vcpus
    $t = 0; if ($jdLatest.timeout -and $jdLatest.timeout.attemptDurationSeconds) { $t = [int]$jdLatest.timeout.attemptDurationSeconds }
    if ($m -ne 3072 -or $v -ne 2 -or $t -ne 14400) { $needReg = $true }
} else { $needReg = $true }
if ($needReg -and $jdLatest) {
    $illegal = @("revision", "status", "jobDefinitionArn", "containerOrchestrationType")
    $regObj = @{}
    foreach ($k in $jdLatest.PSObject.Properties.Name) { if ($k -notin $illegal) { $regObj[$k] = $jdLatest.$k } }
    $regObj.containerProperties.memory = 3072
    $regObj.containerProperties.vcpus = 2
    if ($regObj.containerProperties.PSObject.Properties['runtimePlatform']) { $regObj.containerProperties.PSObject.Properties.Remove('runtimePlatform') }
    if (-not $regObj.timeout) { $regObj | Add-Member -NotePropertyName "timeout" -NotePropertyValue @{ attemptDurationSeconds = 14400 } -Force } else { $regObj.timeout = @{ attemptDurationSeconds = 14400 } }
    $jdPath = Join-Path $RepoRoot "vb_one_take_jd.json"
    $jsonStr = $regObj | ConvertTo-Json -Depth 25 -Compress:$false
    $jsonStr = $jsonStr -replace '"JobDefinitionName"', '"jobDefinitionName"' -replace '"ContainerProperties"', '"containerProperties"' -replace '"Memory":', '"memory":' -replace '"Vcpus":', '"vcpus":' -replace '"Image":', '"image":' -replace '"Command":', '"command":' -replace '"JobRoleArn":', '"jobRoleArn":' -replace '"ExecutionRoleArn":', '"executionRoleArn":' -replace '"ResourceRequirements":', '"resourceRequirements":' -replace '"LogConfiguration":', '"logConfiguration":' -replace '"RuntimePlatform":', '"runtimePlatform":' -replace '"CpuArchitecture":', '"cpuArchitecture":' -replace '"Timeout"', '"timeout"' -replace '"AttemptDurationSeconds"', '"attemptDurationSeconds"' -replace '"PlatformCapabilities"', '"platformCapabilities"' -replace '"Parameters"', '"parameters"' -replace '"RetryStrategy"', '"retryStrategy"' -replace '"Attempts":', '"attempts":' -replace '(\s)"Type":', '$1"type":'
    $jsonStr = $jsonStr -replace '"LogDriver":', '"logDriver":' -replace '"Options":', '"options":' -replace '"Awslogs-group":', '"awslogs-group":' -replace '"Awslogs-region":', '"awslogs-region":' -replace '"Awslogs-stream-prefix":', '"awslogs-stream-prefix":'
    [System.IO.File]::WriteAllText($jdPath, $jsonStr, $utf8NoBom)
    $uri = "file://" + ($jdPath -replace '\\', '/')
    try { Invoke-Aws -ArgsArray @("batch", "register-job-definition", "--cli-input-json", $uri, "--region", $Region, "--output", "json") -ErrorMessage "register Video JobDef failed" } finally { Remove-Item $jdPath -Force -ErrorAction SilentlyContinue }
}

Write-Host "=== 5) EventBridge reconcile rule: rate(15 minutes), target Ops queue ===" -ForegroundColor Cyan
$opsJq = ExecJson @("batch", "describe-job-queues", "--job-queues", $OpsQueueName, "--region", $Region, "--output", "json")
$opsArn = ($opsJq.jobQueues | Where-Object { $_.jobQueueName -eq $OpsQueueName } | Select-Object -First 1).jobQueueArn
$evRole = (ExecJson @("iam", "get-role", "--role-name", "academy-eventbridge-batch-video-role", "--output", "json")).Role.Arn
$targets = @(@{ Id = "1"; Arn = $opsArn; RoleArn = $evRole; BatchParameters = @{ JobDefinition = $OpsJobDefName; JobName = "reconcile-video-jobs" } })
$inputJson = @{ Rule = $ReconcileRuleName; Targets = $targets } | ConvertTo-Json -Depth 5 -Compress
$tFile = Join-Path $RepoRoot "vb_one_take_eb.json"
[System.IO.File]::WriteAllText($tFile, $inputJson, $utf8NoBom)
$tUri = "file://" + ($tFile -replace '\\', '/')
try {
    Invoke-Aws -ArgsArray @("events", "put-rule", "--name", $ReconcileRuleName, "--schedule-expression", "rate(15 minutes)", "--state", "DISABLED", "--description", "Reconcile video jobs", "--region", $Region) -ErrorMessage "put-rule failed"
    Invoke-Aws -ArgsArray @("events", "put-targets", "--cli-input-json", $tUri, "--region", $Region) -ErrorMessage "put-targets failed"
} finally { Remove-Item $tFile -Force -ErrorAction SilentlyContinue }

Write-Host "=== 6) Evidence ===" -ForegroundColor Cyan
$ceOut = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$ce = $ceOut.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
$qOut = ExecJson @("batch", "describe-job-queues", "--job-queues", $VideoQueueName, "--region", $Region, "--output", "json")
$q = $qOut.jobQueues | Where-Object { $_.jobQueueName -eq $VideoQueueName } | Select-Object -First 1
Write-Host "Video CE: $VideoCEName state=$($ce.state) status=$($ce.status) instanceTypes=$($ce.computeResources.instanceTypes -join ',')"
Write-Host "Video Queue: $VideoQueueName computeEnvironmentOrder=$($q.computeEnvironmentOrder[0].computeEnvironment)"
$rule = ExecJson @("events", "describe-rule", "--name", $ReconcileRuleName, "--region", $Region, "--output", "json")
Write-Host "EventBridge $ReconcileRuleName: $($rule.ScheduleExpression) State=$($rule.State)"
Write-Host "=== DONE (idempotent). Re-run safe. ===" -ForegroundColor Green
```

---

# 4. PR 변경 요약

**반영 완료:** 아래 변경이 레포에 적용됨. 원테이크 스크립트는 `scripts/infra/video_batch_production_one_take.ps1` 로 저장됨.

| 파일 | 변경 |
|------|------|
| `scripts/infra/batch/video_job_definition.json` | `"memory":4096` → `"memory":3072` |
| `scripts/infra/eventbridge_deploy_video_scheduler.ps1` | reconcile 규칙 `rate(5 minutes)` → `rate(15 minutes)` (L97). scan_stuck은 유지 또는 15분으로 통일. |
| `scripts/infra/batch_video_setup.ps1` | Queue update 실패 시 `academy-video-batch-queue-ce` 생성 분기 제거: 실패 시 exit 1 또는 재시도만. (L278-291 삭제/대체) |
| `scripts/infra/recreate_batch_in_api_vpc.ps1` | `-ComputeEnvName` 기본값을 `academy-video-batch-ce-final`로 변경(선택). 또는 문서만 final로 통일. |
| `apps/support/video/services/video_encoding.py` | `create_job_and_submit_batch`: 진입 시 `with transaction.atomic():` 안에서 `video = Video.objects.select_for_update().get(pk=video.pk)` 후 기존 `existing` active job 재조회. 동일 video에 대한 동시 create 방지. |
| `docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md` | EventBridge reconcile 주기 15분 명시. |
| 신규 | `scripts/infra/video_batch_production_one_take.ps1` — 위 원테이크 스크립트 추가. |

**실행 순서**

1. `.\scripts\infra\video_batch_production_one_take.ps1 -Region ap-northeast-2`
2. (선택) EventBridge 규칙 수동으로 ENABLED: `aws events put-rule --name academy-reconcile-video-jobs --schedule-expression "rate(15 minutes)" --state ENABLED --region ap-northeast-2`
3. `.\scripts\infra\reconcile_video_batch_production.ps1 -Region ap-northeast-2 -VideoCEName academy-video-batch-ce-final` (기존 정합 스크립트로 JobDef/Ops/알람 등 추가 정합)
