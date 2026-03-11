# EventBridge: rule + targets Ensure. Uses v1/templates/eventbridge and iam.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"
$V4Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EventBridgePath = Join-Path $V4Root "templates\eventbridge"
$IamPath = Join-Path $V4Root "templates\iam"

function Ensure-EventBridgeRules {
    if ($script:PlanMode) { return }
    Write-Step "Ensure EventBridge rules"
    $jq = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
    if (-not $jq -or -not $jq.jobQueues -or $jq.jobQueues.Count -eq 0) {
        throw "Ops Queue $($script:OpsQueueName) not found."
    }
    $JobQueueArn = $jq.jobQueues[0].jobQueueArn
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $script:EventBridgeRoleName, "--output", "json")
    if (-not $role -or -not $role.Role) {
        Write-Host "  Creating EventBridge role $($script:EventBridgeRoleName)" -ForegroundColor Yellow
        $script:ChangesMade = $true
        $trustPath = Join-Path $IamPath "trust_events.json"
        $policyPath = Join-Path $IamPath "policy_eventbridge_batch_submit.json"
        if (-not (Test-Path $trustPath)) { throw "IAM trust_events.json not found." }
        Invoke-Aws @("iam", "create-role", "--role-name", $script:EventBridgeRoleName, "--assume-role-policy-document", "file://$($trustPath -replace '\\','/')") -ErrorMessage "create EventBridge role" | Out-Null
        if (Test-Path $policyPath) {
            Invoke-Aws @("iam", "put-role-policy", "--role-name", $script:EventBridgeRoleName, "--policy-name", "academy-eventbridge-batch-inline", "--policy-document", "file://$($policyPath -replace '\\','/')") | Out-Null
        }
        $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $script:EventBridgeRoleName, "--output", "json")
    }
    $EventsRoleArn = $role.Role.Arn
    $reconcileTargetPath = Join-Path $EventBridgePath "reconcile_to_batch_target.json"
    $scanStuckTargetPath = Join-Path $EventBridgePath "scan_stuck_to_batch_target.json"
    if (-not (Test-Path $reconcileTargetPath) -or -not (Test-Path $scanStuckTargetPath)) { throw "EventBridge target JSON not found." }
    $reconcileJson = (Get-Content $reconcileTargetPath -Raw) -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn
    $scanStuckJson = (Get-Content $scanStuckTargetPath -Raw) -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn
    $ruleExists = $false
    try { $rule = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeReconcileRule, "--region", $script:Region, "--output", "json"); $ruleExists = ($null -ne $rule) } catch { }
    if (-not $ruleExists) {
        Write-Host "  Creating rule $($script:EventBridgeReconcileRule)" -ForegroundColor Yellow
        $script:ChangesMade = $true
        $state = if ($script:EventBridgeReconcileState -eq "DISABLED") { "DISABLED" } else { "ENABLED" }
        Invoke-Aws @("events", "put-rule", "--name", $script:EventBridgeReconcileRule, "--schedule-expression", $script:EventBridgeReconcileSchedule, "--state", $state, "--region", $script:Region) | Out-Null
    } else {
        $desiredState = if ($script:EventBridgeReconcileState -eq "DISABLED") { "DISABLED" } else { "ENABLED" }
        $scheduleDrift = ($rule.ScheduleExpression -ne $script:EventBridgeReconcileSchedule)
        if ($rule.State -ne $desiredState -or $scheduleDrift) {
            if ($scheduleDrift) { Write-Host "  Reconcile rule schedule drift: $($rule.ScheduleExpression) -> $($script:EventBridgeReconcileSchedule)" -ForegroundColor Yellow }
            if ($rule.State -ne $desiredState) { Write-Host "  Setting rule $($script:EventBridgeReconcileRule) to $desiredState (was $($rule.State))" -ForegroundColor Yellow }
            $script:ChangesMade = $true
            Invoke-Aws @("events", "put-rule", "--name", $script:EventBridgeReconcileRule, "--schedule-expression", $script:EventBridgeReconcileSchedule, "--state", $desiredState, "--region", $script:Region) | Out-Null
        }
    }
    $targetsObj = $reconcileJson | ConvertFrom-Json
    $targetsInput = @{ Rule = $script:EventBridgeReconcileRule; Targets = @($targetsObj) } | ConvertTo-Json -Depth 15 -Compress
    $tmpFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmpFile, $targetsInput, [System.Text.UTF8Encoding]::new($false))
        Invoke-Aws @("events", "put-targets", "--cli-input-json", "file://$($tmpFile -replace '\\','/')", "--region", $script:Region) -ErrorMessage "put-targets reconcile"
    } finally { Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue }
    Write-Ok "EventBridge $($script:EventBridgeReconcileRule) targets updated"
    $rule2Exists = $false
    try { $rule2 = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeScanStuckRule, "--region", $script:Region, "--output", "json"); $rule2Exists = ($null -ne $rule2) } catch { }
    if (-not $rule2Exists) {
        Write-Host "  Creating rule $($script:EventBridgeScanStuckRule)" -ForegroundColor Yellow
        $script:ChangesMade = $true
        $state2 = if ($script:EventBridgeScanStuckState -eq "DISABLED") { "DISABLED" } else { "ENABLED" }
        Invoke-Aws @("events", "put-rule", "--name", $script:EventBridgeScanStuckRule, "--schedule-expression", $script:EventBridgeScanStuckSchedule, "--state", $state2, "--region", $script:Region) | Out-Null
    } else {
        $desiredState2 = if ($script:EventBridgeScanStuckState -eq "DISABLED") { "DISABLED" } else { "ENABLED" }
        $scheduleDrift2 = ($rule2.ScheduleExpression -ne $script:EventBridgeScanStuckSchedule)
        if ($rule2.State -ne $desiredState2 -or $scheduleDrift2) {
            if ($scheduleDrift2) { Write-Host "  Scan-stuck rule schedule drift: $($rule2.ScheduleExpression) -> $($script:EventBridgeScanStuckSchedule)" -ForegroundColor Yellow }
            if ($rule2.State -ne $desiredState2) { Write-Host "  Setting rule $($script:EventBridgeScanStuckRule) to $desiredState2 (was $($rule2.State))" -ForegroundColor Yellow }
            $script:ChangesMade = $true
            Invoke-Aws @("events", "put-rule", "--name", $script:EventBridgeScanStuckRule, "--schedule-expression", $script:EventBridgeScanStuckSchedule, "--state", $desiredState2, "--region", $script:Region) | Out-Null
        }
    }
    $targetsObj2 = $scanStuckJson | ConvertFrom-Json
    $targetsInput2 = @{ Rule = $script:EventBridgeScanStuckRule; Targets = @($targetsObj2) } | ConvertTo-Json -Depth 15 -Compress
    $tmpFile2 = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmpFile2, $targetsInput2, [System.Text.UTF8Encoding]::new($false))
        Invoke-Aws @("events", "put-targets", "--cli-input-json", "file://$($tmpFile2 -replace '\\','/')", "--region", $script:Region) -ErrorMessage "put-targets scan_stuck"
    } finally { Remove-Item $tmpFile2 -Force -ErrorAction SilentlyContinue }
    Write-Ok "EventBridge $($script:EventBridgeScanStuckRule) targets updated"

    # --- enqueue-uploaded-videos rule ---
    $enqueueUploadedTargetPath = Join-Path $EventBridgePath "enqueue_uploaded_to_batch_target.json"
    if (Test-Path $enqueueUploadedTargetPath) {
        $enqueueUploadedJson = (Get-Content $enqueueUploadedTargetPath -Raw) -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn
        $rule3Exists = $false
        try { $rule3 = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeEnqueueUploadedRule, "--region", $script:Region, "--output", "json"); $rule3Exists = ($null -ne $rule3) } catch { }
        if (-not $rule3Exists) {
            Write-Host "  Creating rule $($script:EventBridgeEnqueueUploadedRule)" -ForegroundColor Yellow
            $script:ChangesMade = $true
            $state3 = if ($script:EventBridgeEnqueueUploadedState -eq "DISABLED") { "DISABLED" } else { "ENABLED" }
            Invoke-Aws @("events", "put-rule", "--name", $script:EventBridgeEnqueueUploadedRule, "--schedule-expression", $script:EventBridgeEnqueueUploadedSchedule, "--state", $state3, "--region", $script:Region) | Out-Null
        } else {
            $desiredState3 = if ($script:EventBridgeEnqueueUploadedState -eq "DISABLED") { "DISABLED" } else { "ENABLED" }
            $scheduleDrift3 = ($rule3.ScheduleExpression -ne $script:EventBridgeEnqueueUploadedSchedule)
            if ($rule3.State -ne $desiredState3 -or $scheduleDrift3) {
                if ($scheduleDrift3) { Write-Host "  Enqueue-uploaded rule schedule drift: $($rule3.ScheduleExpression) -> $($script:EventBridgeEnqueueUploadedSchedule)" -ForegroundColor Yellow }
                if ($rule3.State -ne $desiredState3) { Write-Host "  Setting rule $($script:EventBridgeEnqueueUploadedRule) to $desiredState3 (was $($rule3.State))" -ForegroundColor Yellow }
                $script:ChangesMade = $true
                Invoke-Aws @("events", "put-rule", "--name", $script:EventBridgeEnqueueUploadedRule, "--schedule-expression", $script:EventBridgeEnqueueUploadedSchedule, "--state", $desiredState3, "--region", $script:Region) | Out-Null
            }
        }
        $targetsObj3 = $enqueueUploadedJson | ConvertFrom-Json
        $targetsInput3 = @{ Rule = $script:EventBridgeEnqueueUploadedRule; Targets = @($targetsObj3) } | ConvertTo-Json -Depth 15 -Compress
        $tmpFile3 = [System.IO.Path]::GetTempFileName()
        try {
            [System.IO.File]::WriteAllText($tmpFile3, $targetsInput3, [System.Text.UTF8Encoding]::new($false))
            Invoke-Aws @("events", "put-targets", "--cli-input-json", "file://$($tmpFile3 -replace '\\','/')", "--region", $script:Region) -ErrorMessage "put-targets enqueue_uploaded"
        } finally { Remove-Item $tmpFile3 -Force -ErrorAction SilentlyContinue }
        Write-Ok "EventBridge $($script:EventBridgeEnqueueUploadedRule) targets updated"
    }
}
