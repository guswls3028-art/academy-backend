# ==============================================================================
# CloudWatch alarms for video Batch pipeline.
# Usage: .\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue [-SnsTopicArn "arn:aws:sns:..."]
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$JobQueueName = "academy-video-batch-queue",
    [string]$SnsTopicArn = ""
)
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$AlarmsPath = Join-Path $ScriptRoot "cloudwatch"

function PutAlarm($name, $ns, $metric, $threshold, $period, $evalPeriods, $dimensionsJson) {
    $cmd = "aws cloudwatch put-metric-alarm --alarm-name $name --namespace $ns --metric-name $metric --statistic Sum --period $period --evaluation-periods $evalPeriods --threshold $threshold --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching --region $Region"
    if ($dimensionsJson) { $cmd += " --dimensions $dimensionsJson" }
    if ($SnsTopicArn) { $cmd += " --alarm-actions $SnsTopicArn" }
    Invoke-Expression $cmd
}

# Job queue ARN for AWS/Batch alarms
$QueueArn = (aws batch describe-job-queues --job-queues $JobQueueName --region $Region --query "jobQueues[0].jobQueueArn" --output text 2>&1)
$QueueArn = ($QueueArn -replace "[\r\n]","").Trim()
if (-not $QueueArn -or $QueueArn -eq "None") {
    Write-Host "Job queue $JobQueueName not found; AWS/Batch alarms will be skipped." -ForegroundColor Yellow
}

Write-Host "[1] Academy/Video DeadJobs (DeadJobs > 0)" -ForegroundColor Cyan
$j = Get-Content (Join-Path $AlarmsPath "alarm_video_dead_jobs.json") -Raw | ConvertFrom-Json
$action = if ($SnsTopicArn) { " --alarm-actions $SnsTopicArn" } else { "" }
aws cloudwatch put-metric-alarm --alarm-name $j.AlarmName --alarm-description $j.AlarmDescription --metric-name $j.MetricName --namespace $j.Namespace --statistic $j.Statistic --period $j.Period --evaluation-periods $j.EvaluationPeriods --threshold $j.Threshold --comparison-operator $j.ComparisonOperator --treat-missing-data $j.TreatMissingData --region $Region $action

Write-Host "[2] Academy/Video UploadFailures" -ForegroundColor Cyan
$j = Get-Content (Join-Path $AlarmsPath "alarm_video_upload_failures.json") -Raw | ConvertFrom-Json
aws cloudwatch put-metric-alarm --alarm-name $j.AlarmName --alarm-description $j.AlarmDescription --metric-name $j.MetricName --namespace $j.Namespace --statistic $j.Statistic --period $j.Period --evaluation-periods $j.EvaluationPeriods --threshold $j.Threshold --comparison-operator $j.ComparisonOperator --treat-missing-data $j.TreatMissingData --region $Region $action

Write-Host "[3] Academy/Video FailedJobs" -ForegroundColor Cyan
$j = Get-Content (Join-Path $AlarmsPath "alarm_video_failed_jobs.json") -Raw | ConvertFrom-Json
aws cloudwatch put-metric-alarm --alarm-name $j.AlarmName --alarm-description $j.AlarmDescription --metric-name $j.MetricName --namespace $j.Namespace --statistic $j.Statistic --period $j.Period --evaluation-periods $j.EvaluationPeriods --threshold $j.Threshold --comparison-operator $j.ComparisonOperator --treat-missing-data $j.TreatMissingData --region $Region $action

if ($QueueArn) {
    Write-Host "[4] AWS/Batch Failed (job queue)" -ForegroundColor Cyan
    $dim = "[{`"Name`":`"JobQueue`",`"Value`":`"$QueueArn`"}]"
    $prevCw = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    aws cloudwatch put-metric-alarm --alarm-name "academy-video-BatchJobFailures" --alarm-description "AWS Batch failed jobs in video queue" --namespace AWS/Batch --metric-name Failed --dimensions $dim --statistic Sum --period 300 --evaluation-periods 2 --threshold 5 --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching --region $Region $action 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "WARN: put-metric-alarm BatchJobFailures failed (exit $LASTEXITCODE)." -ForegroundColor Yellow }
    Write-Host "[5] AWS/Batch RUNNABLE (queue depth)" -ForegroundColor Cyan
    aws cloudwatch put-metric-alarm --alarm-name "academy-video-QueueRunnable" --alarm-description "AWS Batch RUNNABLE jobs above threshold" --namespace AWS/Batch --metric-name RUNNABLE --dimensions $dim --statistic Average --period 300 --evaluation-periods 2 --threshold 50 --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching --region $Region $action 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "WARN: put-metric-alarm QueueRunnable failed (exit $LASTEXITCODE)." -ForegroundColor Yellow }
    $ErrorActionPreference = $prevCw
}

Write-Host "Done. Video Batch CloudWatch alarms deployed." -ForegroundColor Green
