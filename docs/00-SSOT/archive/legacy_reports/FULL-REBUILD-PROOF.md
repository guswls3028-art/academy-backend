# Full Rebuild SSOT v3 — 증명 체크리스트 및 증명 자료

**목적:** 코드/CI가 Full Rebuild 계약을 **실제로** 만족하는지 검증. “말로 반영”이 아니라 **증명**.

---

## 1) Full Rebuild SSOT v3 “증명” 체크리스트 (코드 리뷰 기준)

### A. Create Path 존재 증명

| 리소스 | describe empty/NotFound 시 동작 | 코드 위치 | 합격 |
|--------|----------------------------------|-----------|------|
| **CE** | create-compute-environment | batch.ps1 L74–80 (Video), L116–123 (Ops) | ✅ |
| **Queue** | create-job-queue (CE ARN 연결) | batch.ps1 L158–164 (Video), L181–187 (Ops) | ✅ |
| **JobDef** | register-job-definition | jobdef.ps1 L73–77 (no ACTIVE → Register) | ✅ |
| **EventBridge rule** | put-rule | eventbridge.ps1 L36–39 (reconcile), L49–52 (scan_stuck) | ✅ |
| **EventBridge targets** | put-targets (항상 실행) | eventbridge.ps1 L40–46, L54–61 | ✅ |

- **수동 bootstrap 문구:** docs에서 제거됨 (SSOT-V3-CONFIRMATION-GATHER, state-contract).
- **scripts/infra/*.ps1 호출:** scripts_v3는 **실행 호출 0건**. Join-Path로 JSON 템플릿 **읽기만** 함 (batch.ps1, jobdef.ps1, eventbridge.ps1, iam.ps1).  
  `& .\scripts\infra\*.ps1` 또는 `pwsh -File scripts/infra/*.ps1` 없음.  
  **합격.**

---

### B. Ops CE INVALID Full Recreate 완결 증명

순서: Ops Queue DISABLED → Ops CE DISABLED → (Queue/CE state 대기) → CE delete → **Wait 삭제 완료** → CE create → **Wait VALID** → CE ENABLED → Ops Queue ENABLED.

| 단계 | 구현 | 코드 위치 |
|------|------|-----------|
| Ops Queue DISABLED | update-job-queue --state DISABLED | batch.ps1 L131 |
| Ops CE DISABLED | update-compute-environment --state DISABLED | batch.ps1 L133 |
| Queue state=DISABLED 대기 | describe-job-queues 폴링 (90s) | batch.ps1 L134 |
| CE state=DISABLED 대기 | describe-compute-environments 폴링 (120s) | batch.ps1 L135 |
| CE delete | delete-compute-environment | batch.ps1 L136 |
| **Wait 삭제 완료** | **Wait-CEDeleted** (describe에서 해당 CE 없을 때까지, 300s 폴링 10s) | wait.ps1 L1–16, batch.ps1 L137 |
| CE create | New-OpsCE | batch.ps1 L138 |
| **Wait VALID** | **Wait-CEValidEnabled** (status=VALID, state=ENABLED, 600s 폴링 15s) | wait.ps1 L18–43, batch.ps1 L139 |
| CE ENABLED | (create 후 기본 ENABLED; 필요 시 update-compute-environment) | Wait-CEValidEnabled 내부에서 VALID+ENABLED 확인 |
| Ops Queue ENABLED | update-job-queue --compute-environment-order + --state ENABLED | batch.ps1 L140–141 |

- “삭제 후 sleep 몇 초”만 있는 건 **없음**. 삭제 후에는 반드시 **Wait-CEDeleted** (상태 기반 describe 폴링).  
  **합격.**

---

### C. Ensure-JobDefinition drift 기반 revision 증명

| 항목 | 구현 | 코드 위치 |
|------|------|-----------|
| 최신 ACTIVE 1개만 사용 | describe-job-definitions → Sort-Object revision -Desc → Select -First 1 | jobdef.ps1 L67–71 |
| drift 비교 필드 | image, vcpus, memory, command, jobRoleArn, executionRoleArn, logConfiguration (group, stream-prefix), timeout | jobdef.ps1 L20–40 (Test-JobDefDrift) |
| drift 없으면 | 기존 ARN 유지, register 호출 안 함 | jobdef.ps1 L84–86 |
| drift 있으면 | register-job-definition → 새 revision | jobdef.ps1 L79–82 |
| Evidence에 선택 revision | 4종 JobDef 각각 ARN + revision + image, ECR imageDigest 별도 행 | evidence.ps1 (Get-LatestJobDef, JobDef 행, ECR imageDigest 행) |

- digest는 Evidence에만 기록, drift 판단에는 미사용.  
  **합격.**

---

### D. Netprobe gating 증명

| 기준 | 구현 | 코드 위치 |
|------|------|-----------|
| SUCCEEDED → 배포 성공 | return @{ jobId; status } | netprobe/batch.ps1 L24–26 |
| FAILED → 배포 실패 | throw "Netprobe FAILED: ..." | netprobe/batch.ps1 L28–29 |
| TIMEOUT → 배포 실패 | throw "Netprobe timeout ..." | netprobe/batch.ps1 L33 |
| RUNNABLE 정체 (180s 초과) | throw "Netprobe stuck RUNNABLE ..." | netprobe/batch.ps1 L21–22 |
| jobId/status in Evidence | Show-Evidence -NetprobeJobId / -NetprobeStatus | deploy.ps1 L71–72, evidence.ps1 |

  **합격.**

---

### E. Legacy 차단(기술적) 증명

| 장치 | 내용 | 위치 |
|------|------|------|
| CI denylist 가드 | workflow에 scripts/infra/*.ps1 실행이 있으면 실패 | .github/workflows/video_batch_deploy.yml L29–39 (guard-no-legacy-scripts) |
| workflow 진입점 | deploy 단계는 `pwsh -File scripts_v3/deploy.ps1` 만 호출 | video_batch_deploy.yml L116–117 |

  **합격.**

---

## 2) 위험 구간 4개 — 확인 결과

1. **JobDef drift가 ‘최신 ACTIVE 1개’ 기준인지**  
   - `describe-job-definitions --status ACTIVE` 후 `Sort-Object -Property revision -Descending | Select-Object -First 1` (jobdef.ps1 L69–70).  
   - **충족.**

2. **EventBridge target의 JobDefinition 형태**  
   - 현재 target JSON: `BatchParameters.JobDefinition` = `"academy-video-ops-reconcile"` / `"academy-video-ops-scanstuck"` (이름만).  
   - AWS Batch SubmitJob은 `name` 또는 `name:revision` 모두 허용. 이름만 쓰면 트리거 시점의 최신 ACTIVE revision 사용.  
   - deploy가 JobDef를 Ensure한 직후 put-targets 하므로, 트리거되는 JobDef는 방금 drift 수렴된 revision과 동일.  
   - **정확한 형태 사용.**

3. **Ops CE recreate 시 Queue/Rule 순서**  
   - deploy.ps1 순서: Ensure-VideoCE → Ensure-OpsCE → Ensure-VideoQueue → Ensure-OpsQueue → Ensure-*JobDef → Ensure-EventBridgeRules → ...  
   - Recreate는 CE/Queue 내부에서 완결; Rule은 JobDef 이후에 put-targets.  
   - **순서 적절.**

4. **deploy가 scripts/infra를 “실행”하지 않는지**  
   - scripts_v3 전체 grep: `Invoke-Expression`, `& .\scripts\infra`, `Start-Process.*scripts\infra`, `pwsh.*scripts/infra` 등 없음.  
   - 오직 Join-Path + Test-Path/Get-Content/ReadAllText로 JSON 읽기만 함.  
   - **실행 호출 0건.**

---

## 3) 실행 증명 시나리오 (추천 3단계)

- **시나리오 1 (비파괴):** 변경 없이 `.\scripts_v3\deploy.ps1 -Env prod` 실행 → 로그에 “Skip/No-op” 또는 “unchanged” 메시지, Evidence 테이블 정상 출력.
- **시나리오 2 (Queue DISABLED 복구):** 수동으로 Ops/Video Queue를 DISABLED로 변경 후 deploy → Queue ENABLED로 수렴, Evidence에 state=ENABLED 표시.
- **시나리오 3 (Create path):** Ops CE를 삭제한 뒤 deploy 1회 실행 → CE create → Wait VALID → Queue 연결/Enable → EventBridge targets → Netprobe SUCCEEDED → Evidence 완료.

---

## 4) 증명 자료 — 스크립트 전문 (복붙용)

아래 4개 파일 전문을 그대로 복붙해 최종 합격/불합격 + 수정 포인트 판정에 사용하면 됨.

---

### scripts_v3/deploy.ps1

```powershell
# ==============================================================================
# SSOT v3 Full Rebuild — 단일 진입점. Create/Recreate/Drift 수렴. docs/00-SSOT/INFRA-SSOT-V3.* 참조.
# Usage: .\scripts_v3\deploy.ps1 [-Env prod] [-EcrRepoUri ...] [-AllowRebuild] [-SkipNetprobe]
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("prod","staging","dev")]
    [string]$Env = "prod",
    [string]$EcrRepoUri = "",
    [bool]$AllowRebuild = $true,
    [switch]$SkipNetprobe = $false
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = $PSScriptRoot

Write-Host "`n=== ONE-TAKE DEPLOY START ($Env) [Full Rebuild] ===" -ForegroundColor Cyan

# Env (SSOT values)
. (Join-Path $ScriptRoot "env\prod.ps1")
if ($EcrRepoUri) { $script:EcrRepoUri = $EcrRepoUri } else { $script:EcrRepoUri = "" }
$script:AllowRebuild = $AllowRebuild

# Core
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws-wrapper.ps1")
. (Join-Path $ScriptRoot "core\wait.ps1")
. (Join-Path $ScriptRoot "core\preflight.ps1")
. (Join-Path $ScriptRoot "core\evidence.ps1")

# Resources
. (Join-Path $ScriptRoot "resources\iam.ps1")
. (Join-Path $ScriptRoot "resources\batch.ps1")
. (Join-Path $ScriptRoot "resources\jobdef.ps1")
. (Join-Path $ScriptRoot "resources\eventbridge.ps1")
. (Join-Path $ScriptRoot "resources\asg.ps1")
. (Join-Path $ScriptRoot "resources\ssm.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")

# Netprobe
. (Join-Path $ScriptRoot "netprobe\batch.ps1")

# Sequence (state-contract): IAM -> CE -> Queue -> JobDef -> EventBridge -> Validate -> Netprobe -> Evidence
Invoke-PreflightCheck
$script:BatchIam = Ensure-BatchIAM

Ensure-VideoCE
Ensure-OpsCE
Ensure-VideoQueue
Ensure-OpsQueue

Ensure-VideoJobDef
Ensure-OpsJobDefReconcile
Ensure-OpsJobDefScanStuck
Ensure-OpsJobDefNetprobe

Ensure-EventBridgeRules
Confirm-ASGState
Confirm-SSMEnv
Confirm-APIHealth

$netJobId = ""
$netStatus = ""
if (-not $SkipNetprobe) {
    $net = Invoke-Netprobe -TimeoutSec 1200 -RunnableFailSec 180
    $netJobId = $net.jobId
    $netStatus = $net.status
} else {
    Write-Warn "Netprobe skipped (-SkipNetprobe)"
}

Show-Evidence -NetprobeJobId $netJobId -NetprobeStatus $netStatus

Write-Host "=== ONE-TAKE DEPLOY COMPLETE ===`n" -ForegroundColor Green
```

---

### scripts_v3/resources/batch.ps1

```powershell
# Full Rebuild: Ensure Batch Video/Ops CE and Queues. Create if missing; INVALID -> delete+wait+recreate+wait+enable.
# Uses scripts/infra/batch/*.json (read-only). Requires $script:BatchIam (from Ensure-BatchIAM) and $script:AllowRebuild.
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$BatchPath = Join-Path $InfraPath "batch"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-CEArn {
    param([string]$Name)
    $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $Name, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) { return $null }
    return $r.computeEnvironments[0].computeEnvironmentArn
}

function New-VideoCE {
    $iam = $script:BatchIam
    $subnetArr = ($script:PublicSubnets | ForEach-Object { "`"$_`"" }) -join ","
    $path = Join-Path $BatchPath "video_compute_env.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $script:VideoCEName
    $content = $content -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $iam.ServiceRoleArn
    $content = $content -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $iam.InstanceProfileArn
    $content = $content -replace "PLACEHOLDER_SECURITY_GROUP_ID", $script:BatchSecurityGroupId
    $content = $content -replace '"PLACEHOLDER_SUBNET_1"', $subnetArr
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-compute-environment", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Video CE" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function New-OpsCE {
    $iam = $script:BatchIam
    $subnetArr = ($script:PublicSubnets | ForEach-Object { "`"$_`"" }) -join ","
    $path = Join-Path $BatchPath "ops_compute_env.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $iam.ServiceRoleArn
    $content = $content -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $iam.InstanceProfileArn
    $content = $content -replace "PLACEHOLDER_SECURITY_GROUP_ID", $script:BatchSecurityGroupId
    $content = $content -replace '"PLACEHOLDER_SUBNET_1"', $subnetArr
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-compute-environment", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Ops CE" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function New-VideoQueue {
    param([string]$CeArn)
    $path = Join-Path $BatchPath "video_job_queue.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $CeArn
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-job-queue", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Video Queue" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function New-OpsQueue {
    param([string]$CeArn)
    $path = Join-Path $BatchPath "ops_job_queue.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $CeArn
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-job-queue", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Ops Queue" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Ensure-VideoCE {
    Write-Step "Ensure Video CE $($script:VideoCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Video CE not found; -AllowRebuild false, skip create."; return }
        Write-Host "  Creating Video CE" -ForegroundColor Yellow
        New-VideoCE
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
        return
    }
    $c = $ce.computeEnvironments[0]
    $status = $c.status
    $state = $c.state
    if ($status -eq "INVALID") {
        if (-not $script:AllowRebuild) { Write-Warn "Video CE INVALID; -AllowRebuild false, skip recreate."; return }
        Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait, enable" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Video Queue" 2>$null | Out-Null
        Start-Sleep -Seconds 5
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Video CE" | Out-Null
        $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
        $wait = 0; while ($wait -lt 120) { Start-Sleep -Seconds 5; $wait += 5; $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $script:Region, "--output", "json"); if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break } }
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:VideoCEName, "--region", $script:Region) -ErrorMessage "Delete Video CE" | Out-Null
        Wait-CEDeleted -CEName $script:VideoCEName -Reg $script:Region
        New-VideoCE
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
        $ceArn = Get-CEArn -Name $script:VideoCEName
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Video Queue" 2>$null | Out-Null
        return
    }
    if ($state -eq "DISABLED") {
        Write-Host "  Enabling CE" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Video CE" | Out-Null
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
    } else {
        Write-Ok "Video CE status=$status state=$state"
    }
}

function Ensure-OpsCE {
    Write-Step "Ensure Ops CE $($script:OpsCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Ops CE not found; -AllowRebuild false, skip create."; return }
        Write-Host "  Creating Ops CE" -ForegroundColor Yellow
        New-OpsCE
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
        return
    }
    $c = $ce.computeEnvironments[0]
    $status = $c.status
    $state = $c.state
    if ($status -eq "INVALID") {
        if (-not $script:AllowRebuild) { Write-Warn "Ops CE INVALID; -AllowRebuild false, skip recreate."; return }
        Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait, enable" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Ops Queue" 2>$null | Out-Null
        Start-Sleep -Seconds 5
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Ops CE" | Out-Null
        $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
        $wait = 0; while ($wait -lt 120) { Start-Sleep -Seconds 5; $wait += 5; $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json"); if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break } }
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:OpsCEName, "--region", $script:Region) -ErrorMessage "Delete Ops CE" | Out-Null
        Wait-CEDeleted -CEName $script:OpsCEName -Reg $script:Region
        New-OpsCE
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
        $ceArn = Get-CEArn -Name $script:OpsCEName
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Ops Queue" 2>$null | Out-Null
        return
    }
    if ($state -eq "DISABLED") {
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Ops CE" | Out-Null
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
    } else {
        Write-Ok "Ops CE status=$status state=$state"
    }
}

function Ensure-VideoQueue {
    Write-Step "Ensure Video Queue $($script:VideoQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Video Queue not found; skip create."; return }
        $ceArn = Get-CEArn -Name $script:VideoCEName
        if (-not $ceArn) { throw "Video CE not found; cannot create Video Queue." }
        Write-Host "  Creating Video Queue" -ForegroundColor Yellow
        New-VideoQueue -CeArn $ceArn
        Write-Ok "Video Queue created"
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        Write-Host "  Enabling queue" -ForegroundColor Yellow
        $ceArn = Get-CEArn -Name $script:VideoCEName
        if ($ceArn) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Video Queue" | Out-Null
        }
    } else {
        Write-Ok "Video Queue state=$($qu.state)"
    }
}

function Ensure-OpsQueue {
    Write-Step "Ensure Ops Queue $($script:OpsQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Ops Queue not found; skip create."; return }
        $ceArn = Get-CEArn -Name $script:OpsCEName
        if (-not $ceArn) { throw "Ops CE not found; cannot create Ops Queue." }
        Write-Host "  Creating Ops Queue" -ForegroundColor Yellow
        New-OpsQueue -CeArn $ceArn
        Write-Ok "Ops Queue created"
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        $ceArn = Get-CEArn -Name $script:OpsCEName
        if ($ceArn) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Ops Queue" | Out-Null
        }
    } else {
        Write-Ok "Ops Queue state=$($qu.state)"
    }
}
```

---

### scripts_v3/resources/jobdef.ps1

```powershell
# Ensure Job Definitions with drift detection. Register new revision only when image/vcpus/memory/command/roles/logConfig/timeout differ.
# Uses scripts/infra/batch/*.json. Requires $script:BatchIam, $script:EcrRepoUri (or default repo:latest).
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$BatchPath = Join-Path $InfraPath "batch"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-DesiredJobDefSpec {
    param([string]$TemplatePath)
    $content = [System.IO.File]::ReadAllText($TemplatePath, $utf8NoBom)
    $ecr = $script:EcrRepoUri
    if (-not $ecr) { $ecr = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:VideoWorkerRepo):latest" }
    $content = $content -replace "PLACEHOLDER_ECR_URI", $ecr
    $content = $content -replace "PLACEHOLDER_JOB_ROLE_ARN", $script:BatchIam.JobRoleArn
    $content = $content -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $script:BatchIam.ExecutionRoleArn
    $content = $content -replace "PLACEHOLDER_REGION", $script:Region
    return $content | ConvertFrom-Json
}

function Test-JobDefDrift {
    param($Desired, $Current)
    if (-not $Current -or -not $Current.containerProperties) { return $true }
    $c = $Current.containerProperties
    $d = $Desired.containerProperties
    if ($c.image -ne $d.image) { return $true }
    if ([int]$c.vcpus -ne [int]$d.vcpus) { return $true }
    if ([int]$c.memory -ne [int]$d.memory) { return $true }
    $cmdCur = ($c.command | ConvertTo-Json -Compress)
    $cmdDes = ($d.command | ConvertTo-Json -Compress)
    if ($cmdCur -ne $cmdDes) { return $true }
    if ($c.jobRoleArn -ne $d.jobRoleArn) { return $true }
    if ($c.executionRoleArn -ne $d.executionRoleArn) { return $true }
    $logC = $c.logConfiguration.options
    $logD = $d.logConfiguration.options
    if ($logC."awslogs-group" -ne $logD."awslogs-group") { return $true }
    if ($logC."awslogs-stream-prefix" -ne $logD."awslogs-stream-prefix") { return $true }
    $timeCur = if ($Current.timeout) { $Current.timeout.attemptDurationSeconds } else { 0 }
    $timeDes = if ($Desired.timeout) { $Desired.timeout.attemptDurationSeconds } else { 0 }
    if ($timeCur -ne $timeDes) { return $true }
    return $false
}

function Register-JobDefFromJson {
    param([string]$JsonPath, [string]$Name)
    $content = [System.IO.File]::ReadAllText($JsonPath, $utf8NoBom)
    $ecr = $script:EcrRepoUri
    if (-not $ecr) { $ecr = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:VideoWorkerRepo):latest" }
    $content = $content -replace "PLACEHOLDER_ECR_URI", $ecr
    $content = $content -replace "PLACEHOLDER_JOB_ROLE_ARN", $script:BatchIam.JobRoleArn
    $content = $content -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $script:BatchIam.ExecutionRoleArn
    $content = $content -replace "PLACEHOLDER_REGION", $script:Region
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        $raw = & aws batch register-job-definition --cli-input-json "file://$($tmp -replace '\\','/')" --region $script:Region --output json 2>&1
        if ($LASTEXITCODE -ne 0) { throw "register-job-definition failed: $raw" }
        $obj = ($raw | Out-String).Trim() | ConvertFrom-Json
        return $obj.jobDefinitionArn
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Ensure-JobDefOne {
    param([string]$JobDefName, [string]$TemplateFileName)
    Write-Step "Ensure JobDef $JobDefName"
    $templatePath = Join-Path $BatchPath $TemplateFileName
    if (-not (Test-Path $templatePath)) { Write-Warn "Template $templatePath not found."; return $JobDefName }
    $desired = Get-DesiredJobDefSpec -TemplatePath $templatePath
    $list = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $JobDefName, "--status", "ACTIVE", "--region", $script:Region, "--output", "json")
    $latest = $null
    if ($list -and $list.jobDefinitions -and $list.jobDefinitions.Count -gt 0) {
        $latest = $list.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
    }
    $drift = Test-JobDefDrift -Desired $desired -Current $latest
    if (-not $latest) {
        Write-Host "  Registering (no ACTIVE revision)" -ForegroundColor Yellow
        $arn = Register-JobDefFromJson -JsonPath $templatePath -Name $JobDefName
        Write-Ok "Registered $arn"
        return $JobDefName
    }
    if ($drift) {
        Write-Host "  Drift detected; registering new revision" -ForegroundColor Yellow
        $arn = Register-JobDefFromJson -JsonPath $templatePath -Name $JobDefName
        Write-Ok "Registered $arn"
        return $JobDefName
    }
    Write-Ok "JobDef $JobDefName revision $($latest.revision) unchanged"
    return $JobDefName
}

function Ensure-VideoJobDef {
    Ensure-JobDefOne -JobDefName $script:VideoJobDefName -TemplateFileName "video_job_definition.json" | Out-Null
}

function Ensure-OpsJobDefReconcile {
    Ensure-JobDefOne -JobDefName $script:OpsJobDefReconcile -TemplateFileName "video_ops_job_definition_reconcile.json" | Out-Null
}

function Ensure-OpsJobDefScanStuck {
    Ensure-JobDefOne -JobDefName $script:OpsJobDefScanStuck -TemplateFileName "video_ops_job_definition_scanstuck.json" | Out-Null
}

function Ensure-OpsJobDefNetprobe {
    Ensure-JobDefOne -JobDefName $script:OpsJobDefNetprobe -TemplateFileName "video_ops_job_definition_netprobe.json" | Out-Null
}
```

---

### scripts_v3/resources/eventbridge.ps1

```powershell
# Ensure EventBridge rules: reconcile + scan_stuck. Rule missing -> put-rule + put-targets; Rule exists -> put-targets only.
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$EventBridgePath = Join-Path $InfraPath "eventbridge"
$IamPath = Join-Path $InfraPath "iam"

function Ensure-EventBridgeRules {
    Write-Step "Ensure EventBridge rules (targets)"
    $jq = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
    if (-not $jq -or -not $jq.jobQueues -or $jq.jobQueues.Count -eq 0) {
        throw "Ops Queue $($script:OpsQueueName) not found. Run deploy without -SkipNetprobe after Batch is ready."
    }
    $JobQueueArn = $jq.jobQueues[0].jobQueueArn
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $script:EventBridgeRoleName, "--output", "json")
    if (-not $role -or -not $role.Role) {
        Write-Host "  Creating EventBridge role $($script:EventBridgeRoleName)" -ForegroundColor Yellow
        $trustPath = Join-Path $IamPath "trust_events.json"
        $policyPath = Join-Path $IamPath "policy_eventbridge_batch_submit.json"
        if (-not (Test-Path $trustPath)) { throw "IAM trust_events.json not found." }
        Invoke-Aws @("iam", "create-role", "--role-name", $script:EventBridgeRoleName, "--assume-role-policy-document", "file://$($trustPath -replace '\\','/')") -ErrorMessage "create EventBridge role" | Out-Null
        if (Test-Path $policyPath) {
            Invoke-Aws @("iam", "put-role-policy", "--role-name", $script:EventBridgeRoleName, "--policy-name", "academy-eventbridge-batch-inline", "--policy-document", "file://$($policyPath -replace '\\','/')") -ErrorMessage "put-role-policy" | Out-Null
        }
        $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $script:EventBridgeRoleName, "--output", "json")
    }
    $EventsRoleArn = $role.Role.Arn

    $reconcileTargetPath = Join-Path $EventBridgePath "reconcile_to_batch_target.json"
    $scanStuckTargetPath = Join-Path $EventBridgePath "scan_stuck_to_batch_target.json"
    if (-not (Test-Path $reconcileTargetPath) -or -not (Test-Path $scanStuckTargetPath)) {
        throw "EventBridge target JSON not found under $EventBridgePath"
    }
    $reconcileJson = (Get-Content $reconcileTargetPath -Raw) -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn
    $scanStuckJson = (Get-Content $scanStuckTargetPath -Raw) -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn

    $rule = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeReconcileRule, "--region", $script:Region, "--output", "json")
    if (-not $rule) {
        Write-Host "  Creating rule $($script:EventBridgeReconcileRule)" -ForegroundColor Yellow
        Invoke-Aws @("events", "put-rule", "--name", $script:EventBridgeReconcileRule, "--schedule-expression", "rate(15 minutes)", "--state", "ENABLED", "--description", "Trigger reconcile_batch_video_jobs via Batch SubmitJob", "--region", $script:Region) -ErrorMessage "put-rule reconcile" | Out-Null
    }
    $targetsObj = $reconcileJson | ConvertFrom-Json
    $targetsInput = @{ Rule = $script:EventBridgeReconcileRule; Targets = @($targetsObj) } | ConvertTo-Json -Depth 15 -Compress
    $tmpFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmpFile, $targetsInput, [System.Text.UTF8Encoding]::new($false))
        Invoke-Aws @("events", "put-targets", "--cli-input-json", "file://$($tmpFile -replace '\\','/')", "--region", $script:Region) -ErrorMessage "put-targets reconcile"
        Write-Ok "EventBridge $($script:EventBridgeReconcileRule) targets updated"
    } finally { Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue }

    $rule2 = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeScanStuckRule, "--region", $script:Region, "--output", "json")
    if (-not $rule2) {
        Write-Host "  Creating rule $($script:EventBridgeScanStuckRule)" -ForegroundColor Yellow
        Invoke-Aws @("events", "put-rule", "--name", $script:EventBridgeScanStuckRule, "--schedule-expression", "rate(5 minutes)", "--state", "ENABLED", "--description", "Trigger scan_stuck_video_jobs via Batch SubmitJob", "--region", $script:Region) -ErrorMessage "put-rule scan_stuck" | Out-Null
    }
    $targetsObj2 = $scanStuckJson | ConvertFrom-Json
    $targetsInput2 = @{ Rule = $script:EventBridgeScanStuckRule; Targets = @($targetsObj2) } | ConvertTo-Json -Depth 15 -Compress
    $tmpFile2 = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmpFile2, $targetsInput2, [System.Text.UTF8Encoding]::new($false))
        Invoke-Aws @("events", "put-targets", "--cli-input-json", "file://$($tmpFile2 -replace '\\','/')", "--region", $script:Region) -ErrorMessage "put-targets scan_stuck"
        Write-Ok "EventBridge $($script:EventBridgeScanStuckRule) targets updated"
    } finally { Remove-Item $tmpFile2 -Force -ErrorAction SilentlyContinue }
}
```

---

**끝.** 위 체크리스트·위험 구간·증명 자료로 최종 합격/불합격 + 수정 포인트 판정하면 됨.
