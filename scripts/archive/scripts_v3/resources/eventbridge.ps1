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
        $script:ChangesMade = $true
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

    $ruleExists = $false
    try {
        $rule = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeReconcileRule, "--region", $script:Region, "--output", "json")
        $ruleExists = ($null -ne $rule)
    } catch {
        $ruleExists = $false
    }
    if (-not $ruleExists) {
        Write-Host "  Creating rule $($script:EventBridgeReconcileRule)" -ForegroundColor Yellow
        $script:ChangesMade = $true
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

    $rule2Exists = $false
    try {
        $rule2 = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeScanStuckRule, "--region", $script:Region, "--output", "json")
        $rule2Exists = ($null -ne $rule2)
    } catch {
        $rule2Exists = $false
    }
    if (-not $rule2Exists) {
        Write-Host "  Creating rule $($script:EventBridgeScanStuckRule)" -ForegroundColor Yellow
        $script:ChangesMade = $true
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
