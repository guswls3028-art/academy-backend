# CloudWatch: 로그 그룹 생성 및 retention (SSOT: observability.logRetentionDays)
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Ensure-VideoBatchLogRetention {
    $R = $script:Region
    $retentionDays = if ($script:ObservabilityLogRetentionDays -gt 0) { $script:ObservabilityLogRetentionDays } elseif ($script:VideoBatchLogRetentionDays -gt 0) { $script:VideoBatchLogRetentionDays } else { 30 }
    $logGroups = @(
        $script:VideoLogGroup,
        $script:OpsLogGroup
    )
    if ($script:RdsProxyName -and $script:RdsProxyName.Trim() -ne "") {
        $logGroups += "/aws/rds/proxy/$($script:RdsProxyName.Trim())"
    }
    foreach ($name in $logGroups) {
        if (-not $name -or $name.Trim() -eq "") { continue }
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

function Ensure-RdsCloudWatchAlarms {
    $R = $script:Region
    $dbId = $script:RdsDbIdentifier
    if (-not $dbId -or $dbId.Trim() -eq "") {
        Write-Host "  [CloudWatch] RDS alarms skipped: RdsDbIdentifier empty" -ForegroundColor Yellow
        return
    }

    $period = if ($script:ObservabilityAlarmPeriodSeconds -gt 0) { $script:ObservabilityAlarmPeriodSeconds } else { 300 }
    $evalPeriods = if ($script:ObservabilityAlarmEvaluationPeriods -gt 0) { $script:ObservabilityAlarmEvaluationPeriods } else { 2 }
    $cpuThreshold = if ($script:ObservabilityRdsCpuThresholdPercent -gt 0) { $script:ObservabilityRdsCpuThresholdPercent } else { 80 }
    $freeStorageGb = if ($script:ObservabilityRdsFreeStorageGbThreshold -gt 0) { $script:ObservabilityRdsFreeStorageGbThreshold } else { 5 }
    $freeStorageBytes = [int64]$freeStorageGb * 1073741824
    # Existing ops healthcheck treats >320 connections as an issue; keep the alarm aligned.
    $connectionThreshold = if ($script:ObservabilityRdsConnectionsThreshold -gt 100) { $script:ObservabilityRdsConnectionsThreshold } else { 320 }

    $alarmActionArgs = @()
    $opsTopicArn = "arn:aws:sns:${R}:$($script:AccountId):academy-ops-alerts"
    try {
        Invoke-Aws @("sns", "get-topic-attributes", "--topic-arn", $opsTopicArn, "--region", $R) -ErrorMessage "sns-get-ops-alerts" | Out-Null
        $alarmActionArgs = @("--alarm-actions", $opsTopicArn)
    } catch {
        Write-Host "  [CloudWatch] SNS topic not found, creating RDS alarms without actions: academy-ops-alerts" -ForegroundColor Yellow
    }

    $dimension = "Name=DBInstanceIdentifier,Value=$dbId"
    $alarms = @(
        @{
            Name = "academy-rds-CPUHigh"
            Description = "RDS CPUUtilization high for academy-db"
            Metric = "CPUUtilization"
            Statistic = "Average"
            Threshold = $cpuThreshold
            Operator = "GreaterThanThreshold"
            Missing = "notBreaching"
        },
        @{
            Name = "academy-rds-FreeStorageLow"
            Description = "RDS FreeStorageSpace low for academy-db"
            Metric = "FreeStorageSpace"
            Statistic = "Average"
            Threshold = $freeStorageBytes
            Operator = "LessThanThreshold"
            Missing = "breaching"
        },
        @{
            Name = "academy-rds-DatabaseConnectionsHigh"
            Description = "RDS DatabaseConnections high for academy-db"
            Metric = "DatabaseConnections"
            Statistic = "Average"
            Threshold = $connectionThreshold
            Operator = "GreaterThanThreshold"
            Missing = "notBreaching"
        }
    )

    foreach ($alarm in $alarms) {
        $args = @(
            "cloudwatch", "put-metric-alarm",
            "--alarm-name", $alarm.Name,
            "--alarm-description", $alarm.Description,
            "--namespace", "AWS/RDS",
            "--metric-name", $alarm.Metric,
            "--dimensions", $dimension,
            "--statistic", $alarm.Statistic,
            "--period", $period.ToString(),
            "--evaluation-periods", $evalPeriods.ToString(),
            "--threshold", $alarm.Threshold.ToString(),
            "--comparison-operator", $alarm.Operator,
            "--treat-missing-data", $alarm.Missing,
            "--region", $R
        )
        $args += $alarmActionArgs
        Invoke-Aws $args -ErrorMessage "put-metric-alarm $($alarm.Name)" | Out-Null
        Write-Host "  [CloudWatch] RDS alarm ensured: $($alarm.Name)" -ForegroundColor Gray
    }
}
