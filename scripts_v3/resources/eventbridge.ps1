# Ensure EventBridge rules: reconcile + scan_stuck. Rule exists -> put-targets only.
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$EventBridgePath = Join-Path $InfraPath "eventbridge"

function Ensure-EventBridgeRules {
    Write-Step "Ensure EventBridge rules (targets)"
    $jq = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
    if (-not $jq -or -not $jq.jobQueues -or $jq.jobQueues.Count -eq 0) {
        Write-Fail "Ops Queue $($script:OpsQueueName) not found; run Batch Ops setup first."
        return
    }
    $JobQueueArn = $jq.jobQueues[0].jobQueueArn
    $role = Invoke-AwsJson @("iam", "get-role", "--role-name", $script:EventBridgeRoleName, "--output", "json")
    if (-not $role -or -not $role.Role) {
        Write-Warn "EventBridge role $($script:EventBridgeRoleName) not found; create via eventbridge_deploy_video_scheduler.ps1 once."
        return
    }
    $EventsRoleArn = $role.Role.Arn

    $reconcileTargetPath = Join-Path $EventBridgePath "reconcile_to_batch_target.json"
    $scanStuckTargetPath = Join-Path $EventBridgePath "scan_stuck_to_batch_target.json"
    if (-not (Test-Path $reconcileTargetPath) -or -not (Test-Path $scanStuckTargetPath)) {
        Write-Warn "EventBridge target JSON not found under $EventBridgePath"
        return
    }
    $reconcileJson = (Get-Content $reconcileTargetPath -Raw) -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn
    $scanStuckJson = (Get-Content $scanStuckTargetPath -Raw) -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn

    $rule = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeReconcileRule, "--region", $script:Region, "--output", "json")
    if ($rule) {
        $targetsInput = @{ Rule = $script:EventBridgeReconcileRule; Targets = ($reconcileJson | ConvertFrom-Json) } | ConvertTo-Json -Depth 10 -Compress
        $tmpFile = [System.IO.Path]::GetTempFileName()
        try {
            [System.IO.File]::WriteAllText($tmpFile, $targetsInput, [System.Text.UTF8Encoding]::new($false))
            Invoke-Aws @("events", "put-targets", "--cli-input-json", "file://$($tmpFile -replace '\\','/')", "--region", $script:Region) -ErrorMessage "put-targets reconcile"
            Write-Ok "EventBridge $($script:EventBridgeReconcileRule) targets updated"
        } finally { Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue }
    } else {
        Write-Warn "Rule $($script:EventBridgeReconcileRule) not found; create via eventbridge_deploy_video_scheduler.ps1 once."
    }

    $rule2 = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeScanStuckRule, "--region", $script:Region, "--output", "json")
    if ($rule2) {
        $targetsInput2 = @{ Rule = $script:EventBridgeScanStuckRule; Targets = ($scanStuckJson | ConvertFrom-Json) } | ConvertTo-Json -Depth 10 -Compress
        $tmpFile2 = [System.IO.Path]::GetTempFileName()
        try {
            [System.IO.File]::WriteAllText($tmpFile2, $targetsInput2, [System.Text.UTF8Encoding]::new($false))
            Invoke-Aws @("events", "put-targets", "--cli-input-json", "file://$($tmpFile2 -replace '\\','/')", "--region", $script:Region) -ErrorMessage "put-targets scan_stuck"
            Write-Ok "EventBridge $($script:EventBridgeScanStuckRule) targets updated"
        } finally { Remove-Item $tmpFile2 -Force -ErrorAction SilentlyContinue }
    } else {
        Write-Warn "Rule $($script:EventBridgeScanStuckRule) not found."
    }
}
