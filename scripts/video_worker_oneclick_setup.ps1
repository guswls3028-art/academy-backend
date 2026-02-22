# ==============================================================================
# Video Worker 원큐 셋업: 초기/적용/검증/롤백을 한 번에
# 스케일링 = SQS 기반만. DLQ/redrive 보강. 비용 폭주 방지.
# ==============================================================================
# 사용:
#   .\scripts\video_worker_oneclick_setup.ps1
#   .\scripts\video_worker_oneclick_setup.ps1 -Region ap-northeast-2 -DryRun
#   .\scripts\video_worker_oneclick_setup.ps1 -Rollback
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$Profile = "",
    [switch]$DryRun = $false,
    [switch]$Rollback = $false,
    [int]$MaxSize = 20,
    [int]$TargetMessagesPerInstance = 1,
    [int]$MaxReceiveCount = 3
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

# 리소스명: 레포 SSOT/docs/VIDEO_WORKER_SCALING_SSOT.md 및 기존 스크립트와 동일
$AsgName = "academy-video-worker-asg"
$QueueName = "academy-video-jobs"
$DlqName = "academy-video-jobs-dlq"
$LambdaName = "academy-worker-queue-depth-metric"
$PolicyName = "video-backlogcount-tt"
$LtName = "academy-video-worker-lt"

$AwsBase = @("--region", $Region)
if ($Profile) { $AwsBase = @("--profile", $Profile) + $AwsBase }
function Invoke-AwsCli { param([parameter(ValueFromRemainingArguments)]$Rest) $a = @($Rest) + $AwsBase; $exe = (Get-Command aws.exe -CommandType Application -ErrorAction SilentlyContinue).Source; if (-not $exe) { $exe = "aws" }; & $exe @a }

$BackupRoot = Join-Path $RepoRoot "backups\video_worker"
$Log = @()

function Log-Step { param([string]$Msg) $script:Log += "[$(Get-Date -Format 'HH:mm:ss')] $Msg"; Write-Host $Msg -ForegroundColor Cyan }
function Log-Warn { param([string]$Msg) $script:Log += "[WARN] $Msg"; Write-Host $Msg -ForegroundColor Yellow }
function Log-Fail { param([string]$Msg) $script:Log += "[FAIL] $Msg"; Write-Host $Msg -ForegroundColor Red }

# ------------------------------------------------------------------------------
# 0) 사전 점검
# ------------------------------------------------------------------------------
function Test-Prechecks {
    Log-Step "0) Pre-check"
    $missing = @()

    $id = Invoke-AwsCli sts get-caller-identity --output json 2>$null
    if (-not $id) {
        Log-Fail "AWS login/perms failed. Run aws sts get-caller-identity"
        return $false
    }
    Log-Step "  sts get-caller-identity OK"

    $asg = Invoke-AwsCli autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --output json 2>$null | ConvertFrom-Json
    if (-not $asg -or -not $asg.AutoScalingGroups -or $asg.AutoScalingGroups.Count -eq 0) {
        $missing += "ASG($AsgName)"
    } else { Log-Step "  ASG $AsgName exists" }

    $qurl = Invoke-AwsCli sqs get-queue-url --queue-name $QueueName --query "QueueUrl" --output text 2>$null
    if (-not $qurl) { $missing += "SQS($QueueName)" } else { Log-Step "  SQS $QueueName exists" }

    $lt = Invoke-AwsCli ec2 describe-launch-templates --launch-template-names $LtName --output json 2>$null | ConvertFrom-Json
    if (-not $lt -or -not $lt.LaunchTemplates -or $lt.LaunchTemplates.Count -eq 0) {
        $missing += "LaunchTemplate($LtName)"
    } else { Log-Step "  LT $LtName exists" }

    $fn = Invoke-AwsCli lambda get-function --function-name $LambdaName --output json 2>$null
    if (-not $fn) { $missing += "Lambda($LambdaName)" } else { Log-Step "  Lambda $LambdaName exists" }

    $alarms = Invoke-AwsCli cloudwatch describe-alarms --output json 2>$null | ConvertFrom-Json
    $videoAlarms = @()
    if ($alarms -and $alarms.MetricAlarms) {
        $videoAlarms = $alarms.MetricAlarms | Where-Object { $_.Namespace -eq "Academy/VideoProcessing" -or $_.MetricName -like "*Backlog*" -or $_.MetricName -like "*VideoQueue*" }
    }
    Log-Step "  CloudWatch Alarms (video): $($videoAlarms.Count)"

    if ($missing.Count -gt 0) {
        Log-Fail "Missing resources: $($missing -join ', '). Create them and retry."
        return $false
    }
    return $true
}

# ------------------------------------------------------------------------------
# 1) 백업
# ------------------------------------------------------------------------------
function Backup-State {
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $dir = Join-Path $BackupRoot $ts
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    Log-Step "1) Backup -> $dir"

    $asgJson = Invoke-AwsCli autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --output json 2>$null
    if ($asgJson) { [System.IO.File]::WriteAllText((Join-Path $dir "asg.json"), $asgJson, $utf8NoBom) }

    $polJson = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null
    if ($polJson) {
        [System.IO.File]::WriteAllText((Join-Path $dir "scaling_policies.json"), $polJson, $utf8NoBom)
        $polObj = $polJson | ConvertFrom-Json
        $vp = $polObj.ScalingPolicies | Where-Object { $_.PolicyName -eq $PolicyName } | Select-Object -First 1
        if ($vp -and $vp.TargetTrackingConfiguration) {
            $tt = $vp.TargetTrackingConfiguration | ConvertTo-Json -Depth 10 -Compress
            [System.IO.File]::WriteAllText((Join-Path $dir "video_tt_config.json"), $tt, $utf8NoBom)
        }
    }

    $alarmsJson = Invoke-AwsCli cloudwatch describe-alarms --output json 2>$null
    if ($alarmsJson) { [System.IO.File]::WriteAllText((Join-Path $dir "alarms.json"), $alarmsJson, $utf8NoBom) }

    $ltJson = Invoke-AwsCli ec2 describe-launch-templates --launch-template-names $LtName --output json 2>$null
    if ($ltJson) { [System.IO.File]::WriteAllText((Join-Path $dir "launch_template.json"), $ltJson, $utf8NoBom) }

    $lambdaJson = Invoke-AwsCli lambda get-function-configuration --function-name $LambdaName --output json 2>$null
    if ($lambdaJson) { [System.IO.File]::WriteAllText((Join-Path $dir "lambda_config.json"), $lambdaJson, $utf8NoBom) }

    $qurl = Invoke-AwsCli sqs get-queue-url --queue-name $QueueName --query "QueueUrl" --output text 2>$null
    if ($qurl) {
        $sqsJson = Invoke-AwsCli sqs get-queue-attributes --queue-url $qurl --attribute-names All --output json 2>$null
        if ($sqsJson) { [System.IO.File]::WriteAllText((Join-Path $dir "sqs_attributes.json"), $sqsJson, $utf8NoBom) }
    }

    Log-Step "  Backup done: $dir"
    return $dir
}

# ------------------------------------------------------------------------------
# Diff 리스트 출력 (적용 전 변경 예정 항목)
# ------------------------------------------------------------------------------
function Show-DiffList {
    Log-Step "Diff list (changes to apply)"
    $pol = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json
    $curMetric = "unknown"
    if ($pol -and $pol.ScalingPolicies) {
        $vp = $pol.ScalingPolicies | Where-Object { $_.PolicyName -eq $PolicyName } | Select-Object -First 1
        if ($vp -and $vp.TargetTrackingConfiguration.CustomizedMetricSpecification) {
            $curMetric = $vp.TargetTrackingConfiguration.CustomizedMetricSpecification.MetricName
        }
    }
    Write-Host "  - ASG Scaling Policy: metric $curMetric -> VideoQueueDepthTotal (SQS)" -ForegroundColor Gray
    Write-Host "  - Lambda: deploy code (VideoQueueDepthTotal, remove Backlog API)" -ForegroundColor Gray
    Write-Host "  - ASG: MaxSize=$MaxSize, TargetMessagesPerInstance=$TargetMessagesPerInstance" -ForegroundColor Gray
    Write-Host "  - SQS: create/set DLQ RedrivePolicy if missing (maxReceiveCount=$MaxReceiveCount)" -ForegroundColor Gray
}

# ------------------------------------------------------------------------------
# 2) 스케일링 소스 정석 교체 (SQS 기반)
# ------------------------------------------------------------------------------
function Set-SqsBasedScaling {
    Log-Step "2) Scaling source -> SQS only"

    $lambdaPath = Join-Path $RepoRoot "infra\worker_asg\queue_depth_lambda\lambda_function.py"
    if (-not (Test-Path $lambdaPath)) { Log-Fail "Lambda source not found: $lambdaPath"; return $false }
    $zipPath = Join-Path $RepoRoot "worker_queue_depth_lambda.zip"
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path $lambdaPath -DestinationPath $zipPath -Force
    $zipUri = "fileb://$($zipPath -replace '\\','/')"
    Invoke-AwsCli lambda update-function-code --function-name $LambdaName --zip-file $zipUri 2>$null
    if ($LASTEXITCODE -ne 0) { Log-Fail "Lambda update-function-code failed"; Remove-Item $zipPath -Force -ErrorAction SilentlyContinue; return $false }
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    $waited = 0
    do {
        Start-Sleep -Seconds 2
        $waited += 2
        $status = Invoke-AwsCli lambda get-function-configuration --function-name $LambdaName --query "LastUpdateStatus" --output text
        if ($status -eq "Successful") { break }
        if ($waited -ge 30) { Log-Warn "Lambda update wait timeout"; break }
    } while ($true)
    Log-Step "  Lambda deploy done (VideoQueueDepthTotal)"

    $videoTtJson = '{"TargetValue":' + [string]$TargetMessagesPerInstance + ',"CustomizedMetricSpecification":{"MetricName":"VideoQueueDepthTotal","Namespace":"Academy/VideoProcessing","Dimensions":[{"Name":"WorkerType","Value":"Video"},{"Name":"AutoScalingGroupName","Value":"' + $AsgName + '"}],"Statistic":"Average","Unit":"Count"}}'
    $tmpFile = Join-Path $RepoRoot "asg_video_tt_ec2.json"
    [System.IO.File]::WriteAllText($tmpFile, $videoTtJson, $utf8NoBom)
    $pathUri = "file://$($tmpFile -replace '\\','/' -replace ' ', '%20')"
    Invoke-AwsCli autoscaling put-scaling-policy --auto-scaling-group-name $AsgName --policy-name $PolicyName --policy-type TargetTrackingScaling --target-tracking-configuration $pathUri
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { Log-Fail "put-scaling-policy failed"; return $false }
    Log-Step "  ASG TargetTracking -> VideoQueueDepthTotal applied"
    return $true
}

# ------------------------------------------------------------------------------
# 3) DLQ / redrive 보강
# ------------------------------------------------------------------------------
function Set-DlqRedrive {
    Log-Step "3) DLQ / redrive"
    $qurl = Invoke-AwsCli sqs get-queue-url --queue-name $QueueName --query "QueueUrl" --output text 2>$null
    if (-not $qurl) { Log-Warn "Main queue not found, skip DLQ"; return $true }

    $attrs = Invoke-AwsCli sqs get-queue-attributes --queue-url $qurl --attribute-names RedrivePolicy --output json 2>$null | ConvertFrom-Json
    if ($attrs.Attributes.RedrivePolicy) {
        Log-Step "  RedrivePolicy already set"
        return $true
    }

    $dlqUrl = Invoke-AwsCli sqs get-queue-url --queue-name $DlqName --query "QueueUrl" --output text 2>$null
    if (-not $dlqUrl) {
        Invoke-AwsCli sqs create-queue --queue-name $DlqName --attributes "MessageRetentionPeriod=1209600" 2>$null
        if ($LASTEXITCODE -ne 0) { Log-Warn "DLQ create failed"; return $true }
        $dlqUrl = Invoke-AwsCli sqs get-queue-url --queue-name $DlqName --query "QueueUrl" --output text
    }
    $dlqArn = Invoke-AwsCli sqs get-queue-attributes --queue-url $dlqUrl --attribute-names QueueArn --query "Attributes.QueueArn" --output text 2>$null
    if (-not $dlqArn) { Log-Warn "DLQ ARN fetch failed"; return $true }

    $redriveVal = "{`"deadLetterTargetArn`":`"$dlqArn`",`"maxReceiveCount`":$MaxReceiveCount}"
    Invoke-AwsCli sqs set-queue-attributes --queue-url $qurl --attributes "RedrivePolicy=$redriveVal"
    if ($LASTEXITCODE -ne 0) { Log-Warn "RedrivePolicy set failed" } else { Log-Step "  DLQ and RedrivePolicy set (maxReceiveCount=$MaxReceiveCount)" }
    return $true
}

# ------------------------------------------------------------------------------
# 4) 비용 폭주 방지 (ASG Max/Desired, cooldown은 이미 2에서 적용)
# ------------------------------------------------------------------------------
function Set-CostGuards {
    Log-Step "4) Cost guards"
    Invoke-AwsCli autoscaling update-auto-scaling-group --auto-scaling-group-name $AsgName --max-size $MaxSize
    if ($LASTEXITCODE -ne 0) { Log-Warn "ASG max-size update failed" } else { Log-Step "  ASG MaxSize=$MaxSize applied" }
    return $true
}

# ------------------------------------------------------------------------------
# 5) 적용 후 자동 검증
# ------------------------------------------------------------------------------
function Invoke-PostValidate {
    Log-Step "5) Post-validate"
    $mEnd = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $mStart = (Get-Date).AddMinutes(-10).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $cw = Invoke-AwsCli cloudwatch get-metric-statistics --namespace "Academy/VideoProcessing" --metric-name "VideoQueueDepthTotal" --dimensions "Name=WorkerType,Value=Video" "Name=AutoScalingGroupName,Value=$AsgName" --start-time $mStart --end-time $mEnd --period 60 --statistics Average --output json 2>$null | ConvertFrom-Json
    $hasMetric = $cw.Datapoints -and $cw.Datapoints.Count -gt 0
    if ($hasMetric) { Log-Step "  CloudWatch VideoQueueDepthTotal data in last 10min" } else { Log-Warn "  VideoQueueDepthTotal no data yet (Lambda 1min period)" }

    $pol = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json
    $usesSqs = $false
    if ($pol -and $pol.ScalingPolicies) {
        $vp = $pol.ScalingPolicies | Where-Object { $_.PolicyName -eq $PolicyName } | Select-Object -First 1
        if ($vp -and $vp.TargetTrackingConfiguration.CustomizedMetricSpecification.MetricName -eq "VideoQueueDepthTotal") { $usesSqs = $true }
    }
    if ($usesSqs) { Log-Step "  Scaling policy uses VideoQueueDepthTotal" } else { Log-Fail "  Policy does not use VideoQueueDepthTotal" }

    $act = Invoke-AwsCli autoscaling describe-scaling-activities --auto-scaling-group-name $AsgName --max-items 5 --output json 2>$null | ConvertFrom-Json
    if ($act -and $act.Activities) {
        Log-Step "  ASG recent activities: $($act.Activities.Count)"
        $act.Activities | Select-Object -First 3 | ForEach-Object { Write-Host "    $($_.StatusCode) $($_.Description)" -ForegroundColor Gray }
    }
    return $true
}

# ------------------------------------------------------------------------------
# 6) Rollback
# ------------------------------------------------------------------------------
function Restore-Backup {
    Log-Step "6) Rollback"
    if (-not (Test-Path $BackupRoot)) {
        Log-Fail "Backup folder not found: $BackupRoot"
        return $false
    }
    $latest = Get-ChildItem -Path $BackupRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { Log-Fail "No backup dir"; return $false }
    $dir = $latest.FullName
    Log-Step "  Restore from: $dir"

    $ttPath = Join-Path $dir "video_tt_config.json"
    if (Test-Path $ttPath) {
        $ttContent = Get-Content $ttPath -Raw
        $tmpFile = Join-Path $RepoRoot "asg_video_tt_rollback.json"
        [System.IO.File]::WriteAllText($tmpFile, $ttContent, $utf8NoBom)
        $pathUri = "file://$($tmpFile -replace '\\','/' -replace ' ', '%20')"
        Invoke-AwsCli autoscaling put-scaling-policy --auto-scaling-group-name $AsgName --policy-name $PolicyName --policy-type TargetTrackingScaling --target-tracking-configuration $pathUri
        Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
        Log-Step "  Scaling policy restored"
    } else {
        Log-Warn "  video_tt_config.json missing, cannot restore policy"
    }

    Write-Host "  Rollback scope: ASG Scaling Policy only. Lambda/SQS/Alarm: manual." -ForegroundColor Yellow
    return $true
}

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
Log-Step "Video Worker One-Click Setup | Region=$Region DryRun=$DryRun Rollback=$Rollback"

if (-not (Test-Prechecks)) {
    $Log | Out-File (Join-Path $RepoRoot "video_worker_setup_log.txt") -Encoding UTF8
    exit 1
}

if ($Rollback) {
    Restore-Backup
    $Log | Out-File (Join-Path $RepoRoot "video_worker_setup_log.txt") -Encoding UTF8
    exit 0
}

$backupDir = Backup-State
Show-DiffList

if ($DryRun) {
    Log-Step "DryRun done (no apply)"
    $Log | Out-File (Join-Path $RepoRoot "video_worker_setup_log.txt") -Encoding UTF8
    exit 0
}

if (-not (Set-SqsBasedScaling)) { $Log | Out-File (Join-Path $RepoRoot "video_worker_setup_log.txt") -Encoding UTF8; exit 1 }
Set-DlqRedrive | Out-Null
Set-CostGuards | Out-Null
Invoke-PostValidate | Out-Null

Log-Step "Setup done. Validate: .\scripts\video_worker_oneclick_validate.ps1"
$Log | Out-File (Join-Path $RepoRoot "video_worker_setup_log.txt") -Encoding UTF8
