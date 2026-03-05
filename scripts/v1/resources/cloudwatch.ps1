# CloudWatch: Video Batch 로그 그룹 생성 및 retention (SSOT: videoBatch.observability.logRetentionDays)
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Ensure-VideoBatchLogRetention {
    $R = $script:Region
    $retentionDays = if ($script:VideoBatchLogRetentionDays -gt 0) { $script:VideoBatchLogRetentionDays } else { 30 }
    $logGroups = @(
        "/aws/batch/academy-video-worker",
        "/aws/batch/academy-video-ops"
    )
    foreach ($name in $logGroups) {
        try {
            $exists = Invoke-AwsJson @("logs", "describe-log-groups", "--log-group-name-prefix", $name, "--region", $R, "--output", "json")
            if (-not $exists -or -not $exists.logGroups -or $exists.logGroups.Count -eq 0) {
                Invoke-AwsJson @("logs", "create-log-group", "--log-group-name", $name, "--region", $R) | Out-Null
                Write-Host "  [CloudWatch] Created log group: $name" -ForegroundColor Green
            }
            Invoke-AwsJson @("logs", "put-retention-policy", "--log-group-name", $name, "--retention-in-days", $retentionDays, "--region", $R) | Out-Null
            Write-Host "  [CloudWatch] Retention ${retentionDays}d set: $name" -ForegroundColor Gray
        } catch {
            Write-Host "  [CloudWatch] $name : $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}
