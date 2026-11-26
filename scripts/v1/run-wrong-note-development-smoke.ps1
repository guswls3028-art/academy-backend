[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^(sha-[0-9a-f]{40}-run-[0-9]+-[0-9]+|manual-sha256-[0-9a-f]{64})$')]
    [string]$ReleaseId,
    [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9_]{2,62}$')][string]$ProductionDatabase,
    [Parameter(Mandatory)][string]$ManifestPath,
    [Parameter(Mandatory)][string]$LockOwner,
    [switch]$CleanupOnly
)
$ErrorActionPreference = "Stop"
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
$registry = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com"
$command = @'
set -euo pipefail
verify_runtime() {
  for pair in "academy-api|__api__" "academy-ai-development|__ai__" "academy-tools-development|__tools__"; do
    container=${pair%%|*}
    image=${pair#*|}
    test "$(docker inspect -f '{{.State.Status}} {{.HostConfig.NetworkMode}} {{.Config.Image}}' "$container")" = "running host $image"
    docker exec "$container" python -c 'import os; assert os.environ["ACADEMY_DEVELOPMENT_RELEASE_ID"] == "__RELEASE__"; assert os.environ["ACADEMY_RUNTIME_ENV"] == "development"'
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
foreach ($image in @(@("api", "academy-api"), @("ai", "academy-ai-worker-cpu"), @("tools", "academy-tools-worker"))) {
    $repo = $image[1]
    $digest = [string]$manifest.images.$repo.digest
    if ($digest -cnotmatch '^sha256:[0-9a-f]{64}$') { throw "Missing candidate digest for $repo." }
    $command = $command.Replace("__$($image[0])__", "$registry/$repo@$digest")
}
$option = if ($CleanupOnly) { "--cleanup-only" } else { "" }
$command = $command.Replace("__RELEASE__", $ReleaseId).Replace("__PROD__", $ProductionDatabase).Replace("__OPTION__", $option).Replace("`r", "")
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($command))
$paramsRef = Convert-JsonArgToFileRef (@{
    commands = @("printf '%s' '$b64' | base64 -d | bash")
    executionTimeout = @("1600")
} | ConvertTo-Json -Compress)
try {
    $sent = Invoke-AwsJson @("ssm","send-command","--instance-ids",$instanceId,"--document-name","AWS-RunShellScript",
        "--parameters",$paramsRef,"--timeout-seconds","1600","--region",$script:Region,"--output","json",
        "--comment","Exact development AI and Tools wrong-note print scenario")
} finally {
    Remove-TempFiles @(($paramsRef -replace '^file://', ''))
}
$id = [string]$sent.Command.CommandId
if (-not $id) { throw "No SSM command ID returned." }
Write-Host "Wrong-note QA instance=$instanceId release=$ReleaseId command=$id"
for ($i = 0; $i -lt 162; $i++) {
    Start-Sleep -Seconds 10
    $read = Invoke-AwsJson @("ssm","get-command-invocation","--command-id",$id,"--instance-id",$instanceId,
        "--region",$script:Region,"--output","json")
    if ($read.Status -notin @("Pending","InProgress","Delayed","Cancelling")) { break }
}
if ($read.Status -ne "Success") { throw "Wrong-note QA requires recovery: instance=$instanceId command=$id status=$($read.Status)." }
$expected = if ($CleanupOnly) { "WRONG_NOTE_CLEANUP_PASS" } else { "WRONG_NOTE_REAL_USE_PASS" }
$lines = @(([string]$read.StandardOutputContent) -split '\r?\n' | Where-Object { $_ -match "^\{.*$expected.*\}$" })
if ($lines.Count -ne 1) { throw "Missing exact development smoke receipt." }
$receipt = $lines[0] | ConvertFrom-Json
if ($receipt.status -ne $expected -or $null -eq $receipt.remaining) { throw "Invalid smoke receipt." }
foreach ($field in @("tenants","users","r2_objects","ai_jobs","ai_results","ai_redis_keys")) {
    if ($null -eq $receipt.remaining.$field -or $receipt.remaining.$field -ne 0) { throw "Wrong-note residue: $field." }
}
if (-not $CleanupOnly -and ($receipt.exam_questions -ne 25 -or $receipt.homework_questions -ne 5 -or
    $receipt.approved_answers -ne 30 -or $receipt.pdf_members -ne 2 -or
    @($receipt.acknowledged_segmentation_jobs | Sort-Object -Unique).Count -ne 2 -or
    @($receipt.acknowledged_print_jobs | Sort-Object -Unique).Count -ne 2)) { throw "Incomplete document or queue readback." }
Assert-SmokeLock
if ((Get-SoleActiveDevelopment) -ne $instanceId) { throw "Development host changed during the scenario." }
Write-Host $lines[0]
