# Video Batch 프로덕션 원테이크: 단일 CE, c6g.large, JobDef 2vCPU/3072MB, EventBridge 15분, 재실행 시 증식 없음.
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
