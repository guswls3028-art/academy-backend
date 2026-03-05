# SSM: Validate parameters exist. No overwrite in v1 (manual/separate sync).
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
function Confirm-SSMEnv {
    Write-Step "Validate SSM env"
    if ($script:PlanMode) { Write-Ok "SSM check skipped (Plan)"; return }
    $w = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmWorkersEnv, "--region", $script:Region, "--output", "json")
    $api = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmApiEnv, "--region", $script:Region, "--output", "json")
    if ($w -and $w.Parameter -and $w.Parameter.Name) { Write-Ok $script:SsmWorkersEnv } else { Write-Warn "SSM $($script:SsmWorkersEnv) missing or no access" }
    if ($api -and $api.Parameter -and $api.Parameter.Name) { Write-Ok $script:SsmApiEnv } else { Write-Warn "SSM $($script:SsmApiEnv) missing or no access" }
}
