[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^(sha-[0-9a-f]{40}-run-[0-9]+-[0-9]+|manual-sha256-[0-9a-f]{64})$')]
    [string]$ReleaseId,
    [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9_]{2,62}$')][string]$ProductionDatabase,
    [Parameter(Mandatory)][string]$ManifestPath,
    [Parameter(Mandatory)][string]$LockOwner,
    [switch]$CleanupOnly,
    [switch]$EnrollmentNotices,
    [switch]$ClinicNotices,
    [ValidateSet("student", "parent")][string]$EnrollmentRecipient
)
$ErrorActionPreference = "Stop"
if ($EnrollmentNotices -and $ClinicNotices) { throw "Select one notification scenario." }
$messagingScenario = $EnrollmentNotices -or $ClinicNotices
if ($EnrollmentRecipient -and (-not $EnrollmentNotices -or $CleanupOnly)) {
    throw "Select a recipient only for a new enrollment notification scenario."
}
if ($EnrollmentRecipient) { $EnrollmentRecipient = $EnrollmentRecipient.ToLowerInvariant() }
if ($EnrollmentNotices -and -not $CleanupOnly -and -not $EnrollmentRecipient) {
    # Each invocation rechecks the release lock, owns its fixture and reports its own terminal command.
    foreach ($role in @("student", "parent")) {
        & $PSCommandPath @PSBoundParameters -EnrollmentRecipient $role
    }
    return
}
$script:PlanMode = $false
. (Join-Path $PSScriptRoot "core/env.ps1")
. (Join-Path $PSScriptRoot "core/ssot.ps1")
. (Join-Path $PSScriptRoot "core/aws.ps1")
Assert-AwsMutationIdentity | Out-Null
Load-SSOT -Env prod | Out-Null
if (-not $script:ApiDevelopmentEnabled -or $script:ApiDevelopmentAccessMode -ne "ssm-only") {
    throw "Wrong-note QA needs the SSM-only development runtime."
}

function Assert-SmokeLock {
    python (Join-Path $PSScriptRoot "deployment_lock.py") assert-owned --owner $LockOwner
    if ($LASTEXITCODE -ne 0) { throw "Missing shared development release lock." }
}
function Get-SoleActiveDevelopment {
    $inventory = Invoke-AwsJson @(
        "ec2", "describe-instances", "--filters",
        "Name=tag:Name,Values=$($script:ApiDevelopmentInstanceName)",
        "Name=tag:ManagedBy,Values=$($script:ApiDevelopmentManagedByTag)",
        "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down",
        "--region", $script:Region, "--output", "json"
    )
    $hosts = @($inventory.Reservations.Instances | Where-Object { $_.InstanceId })
    if ($hosts.Count -ne 1 -or $hosts[0].State.Name -ne "running") {
        throw "Expected one running development host after prior instance termination."
    }
    $tags = @{}
    foreach ($tag in $hosts[0].Tags) { $tags[$tag.Key] = $tag.Value }
    if ($tags["Environment"] -ne "development" -or $tags["Lifecycle"] -ne "active" -or
        $tags["ReleaseId"] -ne $ReleaseId -or $tags["VerifiedReleaseId"] -ne $ReleaseId) {
        throw "Active development release differs from the candidate."
    }
    return [string]$hosts[0].InstanceId
}

Assert-SmokeLock
$instanceId = Get-SoleActiveDevelopment
$manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
if ($messagingScenario -and ($manifest.schemaVersion -ne 1 -or $manifest.status -cne "candidate" -or
    $manifest.releaseImageTag -cne $ReleaseId -or $manifest.gitSha -cnotmatch '^[0-9a-f]{40}$' -or
    -not $ReleaseId.StartsWith("sha-$($manifest.gitSha)-run-"))) {
    throw "Messaging QA requires the exact workflow candidate manifest."
}
$registry = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com"
$command = @'
set -euo pipefail
verify_runtime() {
  for pair in __CANDIDATE_CONTAINERS__; do
    container=${pair%%|*}
    image=${pair#*|}
    test "$(docker inspect -f '{{.State.Status}} {{.HostConfig.NetworkMode}} {{.Config.Image}}' "$container")" = "running host $image"
    docker exec "$container" python -c 'import os; assert os.environ["ACADEMY_DEVELOPMENT_RELEASE_ID"] == "__RELEASE__"; assert os.environ["ACADEMY_RUNTIME_ENV"] == "development"'
    if [ "__NOTICE_MODE__" = "1" ]; then
      docker exec "$container" python -c 'import os; e=os.environ; assert e["SOLAPI_MOCK"].lower()=="true" and e["SOLAPI_KAKAO_PF_ID"]=="development-mock-pfid" and not e.get("MESSAGING_DRY_RUN_TRIGGERS","").strip() and e["MESSAGING_SQS_QUEUE_NAME"]=="academy-v1-development-messaging-queue" and e["DB_NAME"]=="academy_api_development" and e["DB_USER"]=="academy_api_development_app"'
    fi
  done
  python3 - <<'PY'
import re, subprocess
api = set(subprocess.check_output(["docker","top","academy-api","-eo","pid"], text=True).split()[1:])
listeners = subprocess.check_output(["ss","-H","-lntp","sport = :8000"], text=True)
pids = set(re.findall(r"pid=(\d+)", listeners))
assert pids and pids <= api, "Unexpected development HTTP listener"
PY
  curl -fsS --max-time 8 http://127.0.0.1:8000/healthz >/dev/null
}
verify_runtime
docker exec academy-api python manage.py verify_development_wrong_notes --release '__RELEASE__' --production-database '__PROD__' __OPTION__
verify_runtime
'@
$images = @(@("api", "academy-api"), @("ai", "academy-ai-worker-cpu"), @("tools", "academy-tools-worker"))
$containers = '"academy-api|__api__" "academy-ai-development|__ai__" "academy-tools-development|__tools__"'
if ($messagingScenario) {
    $images = @(@("api", "academy-api"), @("msg", "academy-messaging-worker"))
    $containers = '"academy-api|__api__" "academy-messaging-development|__msg__"'
}
$command = $command.Replace("__CANDIDATE_CONTAINERS__", $containers)
$command = $command.Replace("__NOTICE_MODE__", [string][int][bool]$messagingScenario)
foreach ($image in $images) {
    $repo = $image[1]
    $digest = [string]$manifest.images.$repo.digest
    if ($digest -cnotmatch '^sha256:[0-9a-f]{64}$') { throw "Missing candidate digest for $repo." }
    $command = $command.Replace("__$($image[0])__", "$registry/$repo@$digest")
}
$option = if ($CleanupOnly) { "--cleanup-only" } else { "" }
if ($EnrollmentNotices) { $option = "--enrollment-notices $option" }
if ($ClinicNotices) { $option = "--clinic-notices $option" }
if ($EnrollmentRecipient) { $option = "$option --enrollment-recipient $EnrollmentRecipient" }
$command = $command.Replace("__RELEASE__", $ReleaseId).Replace("__PROD__", $ProductionDatabase).Replace("__OPTION__", $option).Replace("`r", "")
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($command))
$paramsRef = Convert-JsonArgToFileRef (@{
    commands = @("printf '%s' '$b64' | base64 -d | bash")
    executionTimeout = @("1600")
} | ConvertTo-Json -Compress)
try {
    $description = if ($ClinicNotices) { "Exact development clinic booking notification scenario" }
        elseif ($EnrollmentNotices) { "Exact development enrollment notification scenario" }
        else { "Exact development AI and Tools wrong-note print scenario" }
    $sent = Invoke-AwsJson @("ssm","send-command","--instance-ids",$instanceId,"--document-name","AWS-RunShellScript",
        "--parameters",$paramsRef,"--timeout-seconds","1600","--region",$script:Region,"--output","json",
        "--comment",$description)
} finally {
    Remove-TempFiles @(($paramsRef -replace '^file://', ''))
}
$id = [string]$sent.Command.CommandId
if (-not $id) { throw "No SSM command ID returned." }
Write-Host "Development QA instance=$instanceId release=$ReleaseId command=$id"
for ($i = 0; $i -lt 162; $i++) {
    Start-Sleep -Seconds 10
    $read = Invoke-AwsJson @("ssm","get-command-invocation","--command-id",$id,"--instance-id",$instanceId,
        "--region",$script:Region,"--output","json")
    if ($read.Status -notin @("Pending","InProgress","Delayed","Cancelling")) { break }
}
if ($read.Status -ne "Success") { throw "Development QA requires recovery: instance=$instanceId command=$id status=$($read.Status)." }
$expected = if ($CleanupOnly) { "WRONG_NOTE_CLEANUP_PASS" } else { "WRONG_NOTE_REAL_USE_PASS" }
if ($EnrollmentNotices) {
    $expected = if ($CleanupOnly) { "ENROLLMENT_NOTICE_CLEANUP_PASS" } else { "ENROLLMENT_NOTICE_REAL_USE_PASS" }
}
if ($ClinicNotices) {
    $expected = if ($CleanupOnly) { "CLINIC_NOTICE_CLEANUP_PASS" } else { "CLINIC_NOTICE_REAL_USE_PASS" }
}
$lines = @(([string]$read.StandardOutputContent) -split '\r?\n' | Where-Object { $_ -match "^\{.*$expected.*\}$" })
if ($lines.Count -ne 1) { throw "Missing exact development smoke receipt." }
$receipt = $lines[0] | ConvertFrom-Json
if ($receipt.status -ne $expected -or $null -eq $receipt.remaining) { throw "Invalid smoke receipt." }
foreach ($field in @("tenants","users","r2_objects","ai_jobs","ai_results","ai_redis_keys")) {
    if ($null -eq $receipt.remaining.$field -or $receipt.remaining.$field -ne 0) { throw "Wrong-note residue: $field." }
}
if ($EnrollmentNotices) {
    foreach ($field in @("registration_batches","notice_recipients","notice_outboxes","owner_notice_logs","worker_evidence","messaging_redis")) {
        if ($null -eq $receipt.remaining.$field -or $receipt.remaining.$field -ne 0) { throw "Enrollment residue: $field." }
    }
    if (-not $CleanupOnly) {
        $excluded = if ($EnrollmentRecipient -eq "student") { "parent" } else { "student" }
        if ($receipt.selected_role -cne $EnrollmentRecipient -or $receipt.excluded_role -cne $excluded -or
            $receipt.skipped -ne 2 -or $receipt.scheduled_students -ne 2 -or $receipt.acknowledged -ne 2 -or
            $receipt.redelivered -ne 1 -or $receipt.mock_calls -ne 2 -or @($receipt.deliveries).Count -ne 2 -or
            @($receipt.deliveries | Where-Object { $_.role -cne $EnrollmentRecipient }).Count -ne 0) {
            throw "Incomplete enrollment worker receipt for $EnrollmentRecipient."
        }
    }
} elseif ($ClinicNotices) {
    foreach ($field in @("clinic_sessions","clinic_participants","notice_outboxes","owner_notice_logs","worker_evidence","messaging_redis")) {
        if ($null -eq $receipt.remaining.$field -or $receipt.remaining.$field -ne 0) { throw "Clinic residue: $field." }
    }
    if (-not $CleanupOnly) {
        $recipients = @($receipt.deliveries | ForEach-Object { "$($_.phase)/$($_.role)" } | Sort-Object)
        $deletedClinicReceipts = $receipt.general_cleanup.clinic_mock_receipts
        if (($deletedClinicReceipts -isnot [long] -and $deletedClinicReceipts -isnot [int]) -or $deletedClinicReceipts -ne 5) {
            throw "General clinic cleanup did not report five receipts."
        }
        $expectedRecipients = @("approval/parent","approval/student","reservation_pending/clinic_teacher","reservation_pending/parent","reservation_pending/student")
        foreach ($field in @("business_key","message_id","outbox_id","log_id","mock_event_id","ack_id")) {
            $values = @($receipt.deliveries | ForEach-Object { $_.$field } | Where-Object { $_ } | Sort-Object -Unique)
            if ($values.Count -ne 5) { throw "Missing or duplicate clinic delivery field: $field." }
        }
        foreach ($row in $receipt.deliveries) {
            $retry = $row.phase -ceq "reservation_pending" -and $row.role -ceq "clinic_teacher"
            if ($row.deduplicated -isnot [bool] -or $row.deduplicated -ne $retry -or
                $row.receive_count -ne (1 + [int]$retry) -or [bool]$row.redelivery_event_id -ne $retry) {
                throw "Unexpected clinic redelivery receipt."
            }
        }
        if ($receipt.acknowledged -ne 5 -or $receipt.redelivered -ne 1 -or $receipt.mock_calls -ne 5 -or
            $recipients.Count -ne 5 -or @(Compare-Object $expectedRecipients $recipients -CaseSensitive).Count -ne 0 -or
            $receipt.participant_id -lt 1 -or $receipt.source_tenant_id -lt 1 -or $receipt.owner_tenant_id -lt 1 -or
            $receipt.source_tenant_id -eq $receipt.owner_tenant_id) {
            throw "Incomplete clinic booking worker receipt."
        }
    }
} elseif (-not $CleanupOnly -and ($receipt.exam_questions -ne 25 -or $receipt.homework_questions -ne 5 -or
    $receipt.approved_answers -ne 30 -or $receipt.pdf_members -ne 2 -or
    @($receipt.acknowledged_segmentation_jobs | Sort-Object -Unique).Count -ne 2 -or
    @($receipt.acknowledged_print_jobs | Sort-Object -Unique).Count -ne 2)) { throw "Incomplete document or queue readback." }
Assert-SmokeLock
if ((Get-SoleActiveDevelopment) -ne $instanceId) { throw "Development host changed during the scenario." }
if ($messagingScenario) {
    $receipt | Add-Member -NotePropertyMembers ([ordered]@{
        release_id = $ReleaseId
        api_digest = $manifest.images.'academy-api'.digest
        messaging_digest = $manifest.images.'academy-messaging-worker'.digest
        instance_id = $instanceId
        command_id = $id
        command_status = $read.Status
        ssm_sessions_opened = 0
    })
    Write-Host ($receipt | ConvertTo-Json -Depth 6 -Compress)
} else {
    Write-Host $lines[0]
}
