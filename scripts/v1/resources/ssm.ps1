# SSM: Validate parameters exist. No overwrite in v1 (manual/separate sync).
function Confirm-SSMEnv {
    Write-Step "Validate SSM env"
    if ($script:PlanMode) { Write-Ok "SSM check skipped (Plan)"; return }
    $w = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmWorkersEnv, "--region", $script:Region, "--query", "Parameter.Name", "--output", "json")
    $api = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmApiEnv, "--region", $script:Region, "--query", "Parameter.Name", "--output", "json")
    if ($w -and $w.Parameter) { Write-Ok $script:SsmWorkersEnv } else { Write-Warn "SSM $($script:SsmWorkersEnv) missing or no access" }
    if ($api -and $api.Parameter) { Write-Ok $script:SsmApiEnv } else { Write-Warn "SSM $($script:SsmApiEnv) missing or no access" }
}
