# SSM: Validate parameters exist. No overwrite in v4 (manual/separate sync).
function Confirm-SSMEnv {
    Write-Step "Validate SSM env"
    if ($script:PlanMode) { Write-Ok "SSM check skipped (Plan)"; return }
    $w = aws ssm get-parameter --name $script:SsmWorkersEnv --region $script:Region --query "Parameter.Name" --output text 2>&1
    $api = aws ssm get-parameter --name $script:SsmApiEnv --region $script:Region --query "Parameter.Name" --output text 2>&1
    if ($LASTEXITCODE -eq 0 -and $w) { Write-Ok $script:SsmWorkersEnv } else { Write-Warn "SSM $($script:SsmWorkersEnv) missing or no access" }
    if ($LASTEXITCODE -eq 0 -and $api) { Write-Ok $script:SsmApiEnv } else { Write-Warn "SSM $($script:SsmApiEnv) missing or no access" }
}
