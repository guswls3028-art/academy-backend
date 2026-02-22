# full_redeploy.ps1 — Rerun 400 Errors & Idempotency Report

**Scope:** `scripts/full_redeploy.ps1` and scripts it invokes (`_config_instance_keys.ps1`, `check_api_batch_runtime.ps1`).  
**Note:** full_redeploy does **not** call any Batch setup (no job queue, compute environment, or job definition register).

---

## 1. AWS CLI calls in execution order

| # | Call | Type | Idempotent on re-run? | Common 400/ClientException causes |
|---|------|------|------------------------|-----------------------------------|
| 1 | `aws sts get-caller-identity` (×2) | Read | Yes | Invalid credentials (usually 403); wrong region rarely 400. |
| 2 | `aws ec2 describe-instances` (Get-Ec2PublicIps) | Read | Yes | Invalid filter syntax, invalid region. |
| 3 | `aws ec2 describe-instances` (Start-StoppedAcademyInstances) | Read | Yes | Same. |
| 4 | `aws ec2 start-instances` (academy instances) | Update (state) | Yes (no-op if already running) | InvalidInstanceID.NotFound, InvalidState if already running (AWS returns success). |
| 5 | `aws ec2 wait instance-running` | Wait | Yes | N/A. |
| 6 | `aws ec2 describe-instances` (find academy-build-arm64) | Read | Yes | Invalid filter/region. |
| 7 | `aws ec2 start-instances` (build instance) | Update | Yes | Same as 4. |
| 8 | `aws ec2 wait instance-running` | Wait | Yes | N/A. |
| 9 | `aws ec2 describe-images` (AMI lookup) | Read | Yes | Invalid --owners/--filters; no matching AMI → empty result. |
| 10 | `aws ec2 run-instances` | **Create** | **Guarded** (only when no existing build instance) | InvalidAMIID.NotFound, InvalidSubnet.NotFound, InvalidGroup.NotFound, InvalidParameterValue (e.g. IAM profile name), InsufficientCapacity. Empty $AmiId if describe-images returned nothing → 400. |
| 11 | `aws ec2 wait instance-running` | Wait | Yes | N/A. |
| 12 | `aws ssm describe-instance-information` | Read | Yes | Invalid filter. |
| 13 | `aws ssm send-command` | **Create** (invocation) | N/A (new command each run) | InvalidInstanceId, InvalidDocument, **InvalidParameters** (e.g. commands JSON too large, Unicode escapes like `\u0026` from PowerShell ConvertTo-Json breaking parser), parameter size limit. |
| 14 | `aws ssm get-command-invocation` (poll) | Read | Yes | InvalidCommandId. |
| 15 | `aws ssm get-command-invocation` (on failure) | Read | Yes | Same. |
| 16 | (Remote) `aws ecr get-login-password` / docker push (inside SSM script) | Read / ECR push | Yes / N/A | Wrong region/permissions on build instance. |
| 17 | `aws ec2 describe-instances` (Get-Ec2PublicIps again) | Read | Yes | Same as 2. |
| 18 | `aws autoscaling describe-auto-scaling-groups` | Read | Yes | Invalid ASG name. |
| 19 | `aws autoscaling describe-instance-refreshes` | Read | Yes | Invalid ASG name. |
| 20 | `aws autoscaling start-instance-refresh` | Update (triggers refresh) | **Guarded** (skips if InProgress) | InstanceRefreshInProgress or similar if refresh already running and guard missed; invalid ASG name. |

**Subscripts:** `_config_instance_keys.ps1` has no AWS calls. `check_api_batch_runtime.ps1` only uses SSH + `docker exec` (no AWS CLI from PowerShell).

---

## 2. Region / profile / credentials

| Source | Where | Notes |
|--------|--------|------|
| **Region** | Param `$Region = "ap-northeast-2"` | Hardcoded default. Not from `$env:AWS_REGION` or `aws configure get region`. |
| **Account** | `aws sts get-caller-identity --query Account` | From default credential chain (env, profile, instance role). |
| **Profile** | Not set | Script does not set or clear `AWS_PROFILE`. Uses default credential chain. |
| **Env credentials** | Not cleared | Script does **not** call `Remove-Item Env:AWS_*`. No step clears credentials then runs AWS. |

**Risk:** If you run in another region without passing `-Region`, all EC2/SSM/ASG calls use `ap-northeast-2`; SubnetId/SecurityGroupId are also hardcoded and may belong to that region → wrong-region 400s or “not found” if resources are elsewhere.

---

## 3. Resource creation and “exists → update” guards

| Resource | Create call | Guard present? | Issue |
|----------|-------------|----------------|--------|
| Build EC2 instance | `aws ec2 run-instances` | Yes: only when `!$existingId` (no academy-build-arm64 found) | If `describe-instances` returns multiple lines (e.g. 2 instances with same tag), script parses **first line only** → one instance reused, others orphaned. No duplicate from single run. |
| SSM command | `aws ssm send-command` | N/A (each run = new command) | No guard needed; 400 from invalid/malformed parameters. |
| Instance refresh | `aws autoscaling start-instance-refresh` | Yes: checks `InstanceRefreshes[?Status=='InProgress']` and skips if in progress | Good. |

**Missing guard:** After `aws ec2 describe-images`, `$AmiId` is not checked. If no AMI matches, `$AmiId` is empty and `run-instances --image-id $AmiId` → 400. Patch below adds a check.

---

## 4. Batch-related parts (in full_redeploy only)

- **Job queue / compute environment / job definition:** full_redeploy does **not** call any Batch API (no create-job-queue, create-compute-environment, register-job-definition). Video worker image is built and pushed as `academy-video-worker:latest`; Batch Job Definition that references `:latest` will use the new image on **next job submit**. No revision update in this script.
- **Ref::job_id:** Not applicable in this script (only in Batch submit from API/CLI with `--parameters job_id=...`).
- **Log group:** full_redeploy does not create or reference CloudWatch log groups.

---

## 5. Quoting / PowerShell JSON and SSM

- **SSM send-command:** Commands are built as a PowerShell array, then serialized with `ConvertTo-Json -Compress`. In Windows PowerShell 5, `ConvertTo-Json` emits **Unicode escapes** (e.g. `&` → `\u0026`). The AWS CLI/SSM parameter parser can choke on or misinterpret these, leading to **InvalidParameters** or malformed commands on the instance.
- **Risk:** Build script content includes `$Region`, `$ECR`, `$GitRepoUrl`. If `$GitRepoUrl` contains `&` or `"`, the JSON string may be invalid or too large (SSM parameter size limit).
- **Mitigation:** Build the `commands` JSON array manually (escape only `\` and `"`), like `build_and_push_ecr_remote.ps1`, instead of `ConvertTo-Json` for the commands array.

---

## 6. A) Root causes of 400 on rerun (ranked)

1. **SSM send-command InvalidParameters** — `ConvertTo-Json` for the commands array produces Unicode escapes or too-large payload; or embedded `$GitRepoUrl`/`$ECR` contain characters that break JSON.
2. **run-instances with empty or wrong AMI** — `describe-images` returns no match (e.g. filter/region) → empty `$AmiId` → 400.
3. **run-instances with wrong SubnetId/SecurityGroupId** — Hardcoded defaults belong to one VPC/region; running in another account or region without overriding params → InvalidSubnet.NotFound / InvalidGroup.NotFound.
4. **start-instance-refresh while refresh already in progress** — If the in-progress check fails (e.g. query error or timing), second start-instance-refresh can return InstanceRefreshInProgress / 400-like error.
5. **Wrong region** — Script uses `$Region` param default; if resources are in another region, describe/start calls can return “not found” or 400 depending on service.
6. **Multiple academy-build-arm64 instances** — Script picks first line of describe-instances; if several exist, only one is used; run-instances is not called again, but state is ambiguous (which instance was used for build).
7. **IAM profile name** — `-RoleName` default `academy-ec2-role`; if role does not exist in the account → InvalidParameterValue on run-instances.
8. **ECR login/push on build instance** — Runs inside SSM script; if build instance role has no ECR permissions, remote aws ecr get-login-password or push fails (not a 400 from full_redeploy.ps1 itself).
9. **describe-instances filter** — Typo in tag name (e.g. academy-build-arm64) or wrong region → no instance → run-instances on every run → duplicate instances if AMI is valid.
10. **ASG name** — If ASG was renamed or deleted, describe-auto-scaling-groups / start-instance-refresh can fail.

---

## 7. B) Minimal safe rerun procedure

**Safe to run repeatedly:**

- `.\scripts\full_redeploy.ps1 -SkipBuild -WorkersViaASG`  
  (No build, no run-instances, no send-command; only describe-instances, start-instances for stopped academy instances, API deploy via SSH, ASG instance refresh with in-progress check.)
- `.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget api`  
  (Same, but only API server deploy; no worker ASG refresh.)

**Avoid or use with care:**

- **Full build without -SkipBuild:** Each run can hit SSM parameter/JSON issues; if no build instance exists, run-instances runs once per “no existing instance” (e.g. wrong tag/region). Prefer building once or using `build_and_push_ecr_remote.ps1`, then `-SkipBuild` redeploy.
- **Do not** run full build (no -SkipBuild) repeatedly in quick succession without confirming the first SSM command completed and build instance still has tag `academy-build-arm64`.
- **Do not** change region without passing `-Region` and without ensuring SubnetId, SecurityGroupId, and (if creating build instance) AMI exist in that region.

**What NOT to run for “only roll out already-built image”:**

- Do not run with build step if image is already in ECR. Use `-SkipBuild` and appropriate `-DeployTarget` / `-WorkersViaASG`.

---

## 8. C) Code patches for safety and determinism

### Patch 1: Guard AMI before run-instances

After the `describe-images` call (around line 145), ensure `$AmiId` is set and non-empty:

```powershell
        $AmiId = (aws ec2 describe-images --region $Region --owners amazon `
            --filters "Name=name,Values=al2023-ami-*-kernel-6.1-arm64" "Name=state,Values=available" `
            --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)
        if ([string]::IsNullOrWhiteSpace($AmiId) -or $AmiId -eq "None") {
            Write-Host "ERROR: No suitable AMI found (al2023-ami arm64). Check region $Region and filters." -ForegroundColor Red
            exit 1
        }
```

### Patch 2: Use explicit first instance when multiple academy-build-arm64 exist

When parsing `$existing`, take the first instance id and state in a stable way (e.g. first line), and optionally warn if more than one:

```powershell
    $existing = aws ec2 describe-instances --region $Region `
        --filters "Name=tag:Name,Values=academy-build-arm64" "Name=instance-state-name,Values=running,stopped" `
        --query "Reservations[].Instances[].[InstanceId,State.Name]" --output text 2>&1
    $existingId = $null
    $existingState = $null
    if ($existing -match "i-\S+\s+(running|stopped)") {
        $lines = $existing.Trim() -split "`n" | Where-Object { $_ -match "i-\S+\s+(running|stopped)" }
        if ($lines.Count -gt 1) {
            Write-Host "WARN: Multiple academy-build-arm64 instances found; using first." -ForegroundColor Yellow
        }
        $parts = $lines[0].Trim() -split "\s+", 2
        $existingId = $parts[0]
        $existingState = $parts[1]
    }
```

### Patch 3: SSM commands JSON without Unicode escapes (manual array)

Replace the block that builds `$commandsArray` and `$commandsJson` (around lines 226–236) with manual JSON array construction so SSM does not receive `\u0026`-style escapes:

```powershell
    $scriptLines = $buildScript -split "`n" | Where-Object { $_.Trim() -ne "" }
    $commandsArray = @()
    foreach ($line in $scriptLines) {
        $trimmed = $line.Trim()
        if ($trimmed) {
            $commandsArray += $trimmed
        }
    }
    # Build JSON array by hand to avoid ConvertTo-Json Unicode escapes (\u0026 etc.) that can break SSM
    $escaped = $commandsArray | ForEach-Object {
        $s = $_ -replace '\\', '\\\\' -replace '"', '\"'
        "`"$s`""
    }
    $commandsJson = "[" + ($escaped -join ",") + "]"
    $cmdResult = aws ssm send-command --region $Region `
```

This keeps the same `--parameters "commands=$commandsJson"` usage but avoids PowerShell’s default Unicode escaping.

### Patch 4 (optional): Region from env with fallback

At the top of the script (after param block), allow region from environment when not passed:

```powershell
if (-not $Region -or $Region -eq "ap-northeast-2") {
    $envRegion = $env:AWS_REGION
    if (-not $envRegion) { $envRegion = $env:AWS_DEFAULT_REGION }
    if ($envRegion) { $Region = $envRegion }
}
```

Use only if you want env to override the default; otherwise document that `-Region` must be passed when not using ap-northeast-2.

---

## 9. Summary

- **400s on rerun** are most likely from **SSM send-command** (malformed/large commands JSON) and **run-instances** (empty AMI or wrong SubnetId/SecurityGroupId/region). **start-instance-refresh** is already guarded.
- **Idempotency:** Build instance creation is guarded (reuse if exists); instance refresh is guarded (skip if InProgress). Remaining risks are duplicate build instances if describe fails or tag is wrong, and SSM/AMI/region issues above.
- **Minimal safe rerun:** Prefer `-SkipBuild` and only deploy (API and/or workers via ASG). For build, run once or use a dedicated build script, then redeploy with `-SkipBuild`.
- **Batch:** full_redeploy does not register or update Batch resources; it only pushes the video worker image. Ensure Batch Job Definition uses `academy-video-worker:latest` so new jobs pick the new image; no change needed in this script for that.
