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

function Ensure-VideoCloudWatchAlarms {
    $R = $script:Region
    $period = if ($script:ObservabilityAlarmPeriodSeconds -gt 0) { $script:ObservabilityAlarmPeriodSeconds } else { 300 }
    $evalPeriods = if ($script:ObservabilityAlarmEvaluationPeriods -gt 0) { $script:ObservabilityAlarmEvaluationPeriods } else { 2 }
    $queueDepthThreshold = if ($script:VideoQueueDepthAlarmThreshold -gt 0) { $script:VideoQueueDepthAlarmThreshold } else { 50 }
    $failedJobsThreshold = if ($script:VideoFailedJobsAlarmThreshold -gt 0) { $script:VideoFailedJobsAlarmThreshold } else { 5 }

    $alarmActionArgs = @()
    $opsTopicArn = "arn:aws:sns:${R}:$($script:AccountId):academy-ops-alerts"
    try {
        Invoke-Aws @("sns", "get-topic-attributes", "--topic-arn", $opsTopicArn, "--region", $R) -ErrorMessage "sns-get-video-ops-alerts" | Out-Null
        $alarmActionArgs = @("--alarm-actions", $opsTopicArn)
    } catch {
        Write-Host "  [CloudWatch] SNS topic not found, creating video alarms without actions: academy-ops-alerts" -ForegroundColor Yellow
    }

    $batchQueueArn = ""
    if ($script:VideoQueueName) {
        try {
            $queue = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $R, "--output", "json")
            if ($queue -and $queue.jobQueues -and $queue.jobQueues.Count -gt 0) {
                $batchQueueArn = [string]$queue.jobQueues[0].jobQueueArn
            }
        } catch {
            Write-Host "  [CloudWatch] Video Batch queue ARN lookup failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    $alarms = @(
        @{
            Name = "academy-video-DeadJobs"
            Description = "Video encoding: at least one DeadJobs event (Academy/Video)"
            Namespace = "Academy/Video"
            Metric = "DeadJobs"
            Statistic = "Sum"
            Threshold = 0
            EvalPeriods = 1
            Operator = "GreaterThanThreshold"
            Missing = "notBreaching"
            Dimensions = @()
        },
        @{
            Name = "academy-video-UploadFailures"
            Description = "Video encoding: UploadFailures (R2 integrity) above threshold"
            Namespace = "Academy/Video"
            Metric = "UploadFailures"
            Statistic = "Sum"
            Threshold = $failedJobsThreshold
            EvalPeriods = $evalPeriods
            Operator = "GreaterThanThreshold"
            Missing = "notBreaching"
            Dimensions = @()
        },
        @{
            Name = "academy-video-FailedJobs"
            Description = "Video encoding: FailedJobs (BATCH_DESYNC) above threshold"
            Namespace = "Academy/Video"
            Metric = "FailedJobs"
            Statistic = "Sum"
            Threshold = $failedJobsThreshold
            EvalPeriods = $evalPeriods
            Operator = "GreaterThanThreshold"
            Missing = "notBreaching"
            Dimensions = @()
        }
    )

    if ($batchQueueArn) {
        $batchDimension = "Name=JobQueue,Value=$batchQueueArn"
        $alarms += @(
            @{
                Name = "academy-video-BatchJobFailures"
                Description = "AWS Batch: failed jobs in video queue (AWS/Batch metric)"
                Namespace = "AWS/Batch"
                Metric = "Failed"
                Statistic = "Sum"
                Threshold = $failedJobsThreshold
                EvalPeriods = $evalPeriods
                Operator = "GreaterThanThreshold"
                Missing = "notBreaching"
                Dimensions = @($batchDimension)
            },
            @{
                Name = "academy-video-QueueRunnable"
                Description = "AWS Batch: RUNNABLE job count above threshold (backlog)"
                Namespace = "AWS/Batch"
                Metric = "RUNNABLE"
                Statistic = "Average"
                Threshold = $queueDepthThreshold
                EvalPeriods = $evalPeriods
                Operator = "GreaterThanThreshold"
                Missing = "notBreaching"
                Dimensions = @($batchDimension)
            }
        )
    } else {
        Write-Host "  [CloudWatch] Video Batch metric alarms skipped: queue ARN unavailable" -ForegroundColor Yellow
    }

    foreach ($alarm in $alarms) {
        $args = @(
            "cloudwatch", "put-metric-alarm",
            "--alarm-name", $alarm.Name,
            "--alarm-description", $alarm.Description,
            "--namespace", $alarm.Namespace,
            "--metric-name", $alarm.Metric,
            "--statistic", $alarm.Statistic,
            "--period", $period.ToString(),
            "--evaluation-periods", $alarm.EvalPeriods.ToString(),
            "--threshold", $alarm.Threshold.ToString(),
            "--comparison-operator", $alarm.Operator,
            "--treat-missing-data", $alarm.Missing,
            "--region", $R
        )
        if ($alarm.Dimensions -and $alarm.Dimensions.Count -gt 0) {
            $args += @("--dimensions") + @($alarm.Dimensions)
        }
        $args += $alarmActionArgs
        Invoke-Aws $args -ErrorMessage "put-metric-alarm $($alarm.Name)" | Out-Null
        Write-Host "  [CloudWatch] Video alarm ensured: $($alarm.Name)" -ForegroundColor Gray
    }
}

function Ensure-ApiCloudWatchAlarms {
    $R = $script:Region
    if (-not $script:ApiAlbName -or -not $script:ApiTargetGroupName) {
        throw "API ALB/target group names are required for user-impact alarms."
    }

    $alb = Invoke-AwsJson @(
        "elbv2", "describe-load-balancers",
        "--names", $script:ApiAlbName,
        "--region", $R,
        "--output", "json"
    )
    $targetGroup = Invoke-AwsJson @(
        "elbv2", "describe-target-groups",
        "--names", $script:ApiTargetGroupName,
        "--region", $R,
        "--output", "json"
    )
    $albArn = [string]$alb.LoadBalancers[0].LoadBalancerArn
    $targetGroupArn = [string]$targetGroup.TargetGroups[0].TargetGroupArn
    if (-not $albArn.Contains(":loadbalancer/") -or -not $targetGroupArn.Contains(":targetgroup/")) {
        throw "Could not resolve API ALB/target group CloudWatch dimensions."
    }
    $albDimension = ($albArn -split ":loadbalancer/", 2)[1]
    $targetGroupDimension = "targetgroup/" + ($targetGroupArn -split ":targetgroup/", 2)[1]
    $dimensions = @(
        "Name=LoadBalancer,Value=$albDimension",
        "Name=TargetGroup,Value=$targetGroupDimension"
    )

    $period = if ($script:ObservabilityAlarmPeriodSeconds -gt 0) {
        $script:ObservabilityAlarmPeriodSeconds
    } else {
        300
    }
    $evalPeriods = if ($script:ObservabilityAlarmEvaluationPeriods -gt 0) {
        $script:ObservabilityAlarmEvaluationPeriods
    } else {
        2
    }
    $fiveXxThreshold = if ($script:ObservabilityApiAlb5xxThreshold -gt 0) {
        $script:ObservabilityApiAlb5xxThreshold
    } else {
        10
    }
    $minimumHealthyHosts = 1

    $fiveXxArgs = @(
        "cloudwatch", "put-metric-alarm",
        "--alarm-name", "academy-api-Target5XX",
        "--alarm-description", "User-impacting API target 5XX burst; polled by Dev Alerts Cron.",
        "--namespace", "AWS/ApplicationELB",
        "--metric-name", "HTTPCode_Target_5XX_Count",
        "--dimensions"
    ) + $dimensions + @(
        "--statistic", "Sum",
        "--period", $period.ToString(),
        "--evaluation-periods", $evalPeriods.ToString(),
        "--threshold", $fiveXxThreshold.ToString(),
        "--comparison-operator", "GreaterThanOrEqualToThreshold",
        "--treat-missing-data", "notBreaching",
        "--region", $R
    )
    Invoke-Aws $fiveXxArgs -ErrorMessage "put-metric-alarm academy-api-Target5XX" | Out-Null

    $unhealthyArgs = @(
        "cloudwatch", "put-metric-alarm",
        "--alarm-name", "academy-api-UnHealthyHosts",
        "--alarm-description", "API has no healthy target; polled by Dev Alerts Cron.",
        "--namespace", "AWS/ApplicationELB",
        "--metric-name", "HealthyHostCount",
        "--dimensions"
    ) + $dimensions + @(
        "--statistic", "Minimum",
        "--period", $period.ToString(),
        "--evaluation-periods", "1",
        "--threshold", $minimumHealthyHosts.ToString(),
        "--comparison-operator", "LessThanThreshold",
        "--treat-missing-data", "breaching",
        "--region", $R
    )
    Invoke-Aws $unhealthyArgs -ErrorMessage "put-metric-alarm academy-api-UnHealthyHosts" | Out-Null

    $compositeArgs = @(
        "cloudwatch", "put-composite-alarm",
        "--alarm-name", "academy-api-UserImpact",
        "--alarm-description", "API 5XX burst or unhealthy target; Dev Alerts Cron sends fixed-recipient SMS.",
        "--alarm-rule", 'ALARM("academy-api-Target5XX") OR ALARM("academy-api-UnHealthyHosts")',
        "--region", $R
    )
    Invoke-Aws $compositeArgs -ErrorMessage "put-composite-alarm academy-api-UserImpact" | Out-Null
    Write-Host "  [CloudWatch] API user-impact alarms ensured." -ForegroundColor Gray
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
