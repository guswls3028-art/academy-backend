# ==============================================================================
# Video Worker Incident Data Collection + Report Generation
# FACT-ONLY: All conclusions from collected data only. No speculation.
# Usage: .\scripts\collect_video_worker_incident_data.ps1
#        .\scripts\collect_video_worker_incident_data.ps1 -Region ap-northeast-2
# Requires: AWS CLI configured, SSM permissions, autoscaling, ec2, sqs, cloudwatch
# Output: backups/video_worker/incident_YYYYMMDD_HHMMSS/ + docs_cursor/VIDEO_WORKER_INCIDENT_REPORT_YYYYMMDD.md
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$QueueName = "academy-video-jobs",
    [string]$LtName = "academy-video-worker-lt"
)

$ErrorActionPreference = "Continue"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DateOnly = Get-Date -Format "yyyyMMdd"
$OutDir = Join-Path $RepoRoot "backups\video_worker\incident_$Stamp"
$DocsCursor = Join-Path $RepoRoot "docs_cursor"
$QueueUrl = "https://sqs.$Region.amazonaws.com"

# Ensure AWS identity
$AcctRaw = aws sts get-caller-identity --query Account --output text 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: AWS identity check failed. Configure credentials." -ForegroundColor Red
    exit 1
}
$AccountId = $AcctRaw.Trim()
$QueueUrl = "https://sqs.$Region.amazonaws.com/$AccountId/$QueueName"

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $DocsCursor -Force | Out-Null

Write-Host "`n=== Video Worker Incident Data Collection ===" -ForegroundColor Cyan
Write-Host "Region=$Region  OutDir=$OutDir`n" -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 1) ASG state
# ------------------------------------------------------------------------------
Write-Host "[1] ASG state..." -ForegroundColor Yellow
$asgJson = aws autoscaling describe-auto-scaling-groups `
    --auto-scaling-group-names $AsgName `
    --region $Region `
    --query "AutoScalingGroups[0].{Desired:DesiredCapacity,Min:MinSize,Max:MaxSize,Instances:Instances[].{Id:InstanceId,Life:LifecycleState,Health:HealthStatus,AZ:AvailabilityZone,Launch:LaunchTemplate}}" `
    --output json 2>&1
[System.IO.File]::WriteAllText((Join-Path $OutDir "1_asg.json"), $asgJson, $Utf8NoBom)

$asgActivities = aws autoscaling describe-scaling-activities `
    --auto-scaling-group-name $AsgName `
    --region $Region `
    --max-items 20 `
    --output json 2>&1
[System.IO.File]::WriteAllText((Join-Path $OutDir "1_asg_activities.json"), $asgActivities, $Utf8NoBom)

# ------------------------------------------------------------------------------
# 2) Launch Template
# ------------------------------------------------------------------------------
Write-Host "[2] Launch Template..." -ForegroundColor Yellow
$ltSpec = aws autoscaling describe-auto-scaling-groups `
    --auto-scaling-group-names $AsgName `
    --region $Region `
    --query "AutoScalingGroups[0].MixedInstancesPolicy.LaunchTemplate.LaunchTemplateSpecification" `
    --output json 2>&1
[System.IO.File]::WriteAllText((Join-Path $OutDir "2_lt_spec.json"), $ltSpec, $Utf8NoBom)

$ltData = aws ec2 describe-launch-template-versions `
    --launch-template-name $LtName `
    --versions "`$Default" `
    --region $Region `
    --query "LaunchTemplateVersions[0].LaunchTemplateData" `
    --output json 2>&1
[System.IO.File]::WriteAllText((Join-Path $OutDir "2_lt_data.json"), $ltData, $Utf8NoBom)

# ------------------------------------------------------------------------------
# 3) SSM registration
# ------------------------------------------------------------------------------
Write-Host "[3] SSM registration..." -ForegroundColor Yellow
$asgIdsRaw = aws autoscaling describe-auto-scaling-groups `
    --auto-scaling-group-names $AsgName `
    --region $Region `
    --query "AutoScalingGroups[0].Instances[].InstanceId" `
    --output text 2>&1
$asgIds = ($asgIdsRaw -split "\s+") | Where-Object { $_.Trim() -ne "" }

$ssmIdsRaw = aws ssm describe-instance-information `
    --region $Region `
    --query "InstanceInformationList[].InstanceId" `
    --output text 2>&1
$ssmIds = ($ssmIdsRaw -split "\s+") | Where-Object { $_.Trim() -ne "" }

$asgNotSsm = @()
if ($asgIds) {
    foreach ($id in $asgIds) {
        if ($id -notin $ssmIds) { $asgNotSsm += $id }
    }
}

$ssmReport = @"
ASG=$($asgIds -join ",")
SSM=$($ssmIds -join ",")
ASG_NOT_SSM=$($asgNotSsm -join ",")
"@
[System.IO.File]::WriteAllText((Join-Path $OutDir "3_ssm_registration.txt"), $ssmReport, $Utf8NoBom)

# ------------------------------------------------------------------------------
# 3-2) Runtime investigation (investigate_video_worker_runtime.ps1)
# ------------------------------------------------------------------------------
Write-Host "[4] Runtime investigation (SSM Run Command)..." -ForegroundColor Yellow
$runtimeScript = Join-Path $ScriptRoot "investigate_video_worker_runtime.ps1"
$runtimeOut = Join-Path $OutDir "4_runtime_investigation.txt"
& $runtimeScript -Region $Region 2>&1 | Out-File -FilePath $runtimeOut -Encoding UTF8

# ------------------------------------------------------------------------------
# 4) SQS state
# ------------------------------------------------------------------------------
Write-Host "[5] SQS queue attributes..." -ForegroundColor Yellow
$sqsAttrs = aws sqs get-queue-attributes `
    --queue-url $QueueUrl `
    --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateAgeOfOldestMessage `
    --region $Region `
    --output json 2>&1
[System.IO.File]::WriteAllText((Join-Path $OutDir "5_sqs_attrs.json"), $sqsAttrs, $Utf8NoBom)

$end = (Get-Date).ToUniversalTime()
$start = $end.AddMinutes(-15)
$startStr = $start.ToString("yyyy-MM-ddTHH:mm:ssZ")
$endStr = $end.ToString("yyyy-MM-ddTHH:mm:ssZ")

$sqsVisibleMetric = aws cloudwatch get-metric-statistics `
    --namespace AWS/SQS `
    --metric-name ApproximateNumberOfMessagesVisible `
    --dimensions "Name=QueueName,Value=$QueueName" `
    --statistics Average `
    --period 60 `
    --start-time $startStr `
    --end-time $endStr `
    --region $Region `
    --output json 2>&1
[System.IO.File]::WriteAllText((Join-Path $OutDir "5_sqs_visible_metric.json"), $sqsVisibleMetric, $Utf8NoBom)

$sqsNotVisibleMetric = aws cloudwatch get-metric-statistics `
    --namespace AWS/SQS `
    --metric-name ApproximateNumberOfMessagesNotVisible `
    --dimensions "Name=QueueName,Value=$QueueName" `
    --statistics Average `
    --period 60 `
    --start-time $startStr `
    --end-time $endStr `
    --region $Region `
    --output json 2>&1
[System.IO.File]::WriteAllText((Join-Path $OutDir "5_sqs_notvisible_metric.json"), $sqsNotVisibleMetric, $Utf8NoBom)

# ------------------------------------------------------------------------------
# 5) Console output for ASG_NOT_SSM instances
# ------------------------------------------------------------------------------
Write-Host "[6] Console output for ASG_NOT_SSM instances..." -ForegroundColor Yellow
foreach ($instId in $asgNotSsm) {
    $consoleOut = aws ec2 get-console-output `
        --instance-id $instId `
        --region $Region `
        --latest `
        --output text 2>&1
    $safeId = $instId -replace '[^a-zA-Z0-9_-]', '_'
    [System.IO.File]::WriteAllText((Join-Path $OutDir "6_console_$safeId.txt"), $consoleOut, $Utf8NoBom)
}

# ------------------------------------------------------------------------------
# Generate report
# ------------------------------------------------------------------------------
Write-Host "`n[7] Generating report..." -ForegroundColor Cyan
$reportPath = Join-Path $DocsCursor "VIDEO_WORKER_INCIDENT_REPORT_$DateOnly.md"

function Read-SafeJson {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $raw = [System.IO.File]::ReadAllText($Path, $Utf8NoBom)
    if (-not $raw -or $raw -match 'error|Error|invalid|Invalid') { return $raw }
    try { return $raw | ConvertFrom-Json } catch { return $raw }
}

function Read-SafeText {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return "(file not found)" }
    return [System.IO.File]::ReadAllText($Path, $Utf8NoBom)
}

$kstNow = (Get-Date).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss KST")
$utcNow = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss UTC")
$f = '```'

$asgObj = Read-SafeJson (Join-Path $OutDir "1_asg.json")
$asgActObj = Read-SafeJson (Join-Path $OutDir "1_asg_activities.json")
$sqsObj = Read-SafeJson (Join-Path $OutDir "5_sqs_attrs.json")
$sqsVisObj = Read-SafeJson (Join-Path $OutDir "5_sqs_visible_metric.json")
$sqsNvObj = Read-SafeJson (Join-Path $OutDir "5_sqs_notvisible_metric.json")
$ltDataObj = Read-SafeJson (Join-Path $OutDir "2_lt_data.json")

$report = @"
# VIDEO WORKER PIPELINE INCIDENT REPORT (FACT-ONLY)

**Generated:** $kstNow ($utcNow)  
**Data source:** $OutDir

---

## 1. 현상 요약 (사용자 관찰 + 측정값)

| 항목 | 값 |
|------|-----|
| 사용자 관찰 | 워커가 떠 있는데 일을 안 하거나, 1대만 일하거나, 유령(inflight/NotVisible) 메시지가 남음 |
| 수집 시점 (KST) | $kstNow |
| 수집 시점 (UTC) | $utcNow |

---

## 2. 환경/구성 스냅샷

### 2.1 ASG
"@

if ($asgObj -and $asgObj -isnot [string]) {
    $report += @"

| 항목 | 값 |
|------|-----|
| DesiredCapacity | $($asgObj.Desired) |
| MinSize | $($asgObj.Min) |
| MaxSize | $($asgObj.Max) |
| 인스턴스 수 | $(if ($asgObj.Instances) { $asgObj.Instances.Count } else { 0 }) |
"@
    if ($asgObj.Instances) {
        $report += "`n| InstanceId | LifecycleState | HealthStatus | AZ |`n|------------|----------------|--------------|-----|`n"
        foreach ($i in $asgObj.Instances) {
            $report += "| $($i.Id) | $($i.Life) | $($i.Health) | $($i.AZ) |`n"
        }
    }
} else {
    $report += "`n$f" + "json`n$asgObj`n$f"
}

$report += @"

### 2.2 Launch Template
"@

$ltRaw = Read-SafeText (Join-Path $OutDir "2_lt_spec.json")
$report += "`n**LT Spec (ASG refer):**`n$f" + "json`n$ltRaw`n$f`n"
if ($ltDataObj -and $ltDataObj -isnot [string]) {
    $report += "**LT Data (UserData/IamInstanceProfile/SecurityGroup/Subnet):** AMI=$($ltDataObj.ImageId) IamProfile=$($ltDataObj.IamInstanceProfile.Name)"
}

$report += @"

### 2.3 SSM 등록
"@

$report += "`n$f`n$ssmReport`n$f"

$report += @"

### 2.4 SQS (academy-video-jobs)
"@

if ($sqsObj -and $sqsObj -isnot [string] -and $sqsObj.Attributes) {
    $a = $sqsObj.Attributes
    $report += @"

| Attribute | 값 |
|-----------|-----|
| ApproximateNumberOfMessages (Visible) | $($a.ApproximateNumberOfMessages) |
| ApproximateNumberOfMessagesNotVisible | $($a.ApproximateNumberOfMessagesNotVisible) |
| ApproximateAgeOfOldestMessage (초) | $($a.ApproximateAgeOfOldestMessage) |
"@
} else {
    $report += "`n$f`n$sqsObj`n$f"
}

$report += @"

---

## 3. 사실 기반 타임라인

| 시점 | 이벤트 | 근거 |
|------|--------|------|
| T0 | 업로드 완료 (upload_complete) | (DB/API 로그에서 확인 필요) |
| T1 | SQS 메시지 생성 (enqueue) | VIDEO_UPLOAD_ENQUEUE 로그 또는 SQS ApproximateNumberOfMessages |
| T2 | Worker가 메시지 수신 (claim) | Worker 로그 "job claim" / ffmpeg 시작 |
| T3 | 인코딩 완료 또는 정지 | Worker 로그 / delete_message |
| (수집 시점) | Visible / NotVisible | 아래 관측 데이터 |
"@

$report += @"

---

## 4. 관측 데이터

### 4.1 ASG Scaling Activities (최근 20건)
"@

if ($asgActObj -and $asgActObj -isnot [string] -and $asgActObj.Activities) {
    $report += "`n| StartTime | Activity | Description | StatusCode | StatusReason |`n|-----------|----------|-------------|------------|--------------|`n"
    foreach ($act in $asgActObj.Activities) {
        $report += "| $($act.StartTime) | $($act.Activity) | $($act.Description) | $($act.StatusCode) | $($act.StatusReason) |`n"
    }
} else {
    $report += "`n$f`n$asgActivities`n$f"
}

$report += @"

### 4.2 SQS CloudWatch Metric (최근 15분, 1분 period)
"@

if ($sqsVisObj -and $sqsVisObj -isnot [string] -and $sqsVisObj.Datapoints) {
    $report += "`n**Visible:** Datapoints count=$($sqsVisObj.Datapoints.Count)"
    foreach ($dp in ($sqsVisObj.Datapoints | Sort-Object -Property Timestamp)) {
        $report += " | $($dp.Timestamp) Avg=$($dp.Average)"
    }
}
if ($sqsNvObj -and $sqsNvObj -isnot [string] -and $sqsNvObj.Datapoints) {
    $report += "`n**NotVisible:** Datapoints count=$($sqsNvObj.Datapoints.Count)"
    foreach ($dp in ($sqsNvObj.Datapoints | Sort-Object -Property Timestamp)) {
        $report += " | $($dp.Timestamp) Avg=$($dp.Average)"
    }
}

$report += @"

### 4.3 Runtime Investigation (인스턴스별)
"@

$report += "`n$f`n"
$report += Read-SafeText (Join-Path $OutDir "4_runtime_investigation.txt")
$report += "`n$f"

$report += @"

### 4.4 ASG_NOT_SSM 콘솔 출력 (cloud-init/user-data)
"@

foreach ($instId in $asgNotSsm) {
    $safeId = $instId -replace '[^a-zA-Z0-9_-]', '_'
    $consolePath = Join-Path $OutDir "6_console_$safeId.txt"
    $report += "`n**$instId:**`n$f`n"
    $report += Read-SafeText $consolePath
    $report += "`n$f`n"
}
if ($asgNotSsm.Count -eq 0) {
    $report += "`n(ASG_NOT_SSM 없음 - 모든 ASG 인스턴스가 SSM 등록됨)"
}

$report += @"

---

## 5. 원인 (근거 기반 분류)

각 원인은 아래 A~E 중 해당하는 항목만 포함. **추측 금지.**

| 코드 | 원인 | 근거 | 해석 | 검증 기준 |
|------|------|------|------|-----------|
| A | ASG Desired를 올렸지만 인스턴스가 InService 되지 못함 | 1_asg.json, 1_asg_activities.json | Scaling Activity에서 Successful/InProgress/Failed 확인 | 모든 Activity가 Successful이고 Instances가 LifecycleState=InService |
| B | 인스턴스 InService지만 SSM 미등록 | 3_ssm_registration.txt ASG_NOT_SSM | 부팅/네트워크/권한 문제 | 6_console_*.txt에서 ECR login / SSM param / docker pull 실패 라인 |
| C | Worker 로그에 job claim/ffmpeg 오류 | 4_runtime_investigation.txt | NO_FFMPEG 또는 worker 에러 로그 | ffmpegProcessCount > 0, worker 로그에 claim/encode 성공 |
| D | 메시지 delete 안 됨 → NotVisible 유령 | 5_sqs_attrs.json, 5_sqs_notvisible_metric.json | NotVisible > 0인데 worker는 idle | NotVisible이 visibility timeout 내에 0으로 감소 |
| E | 메시지 enqueue 누락/중복 | API/DB 로그, SQS Visible | upload_complete 호출 실패 또는 중복 | VIDEO_UPLOAD_ENQUEUE 로그, SQS Visible 일치 |

**본 수집 데이터에서 확인된 항목:**  
(위 관측 데이터 4.1~4.4를 바탕으로 A~E 중 해당되는 것만 기입)

- A: ASG Activities에 Failed 또는 InProgress 대기 있음? 
- B: ASG_NOT_SSM 비어있지 않음? → 6_console_*.txt에서 cloud-init 실패 라인 인용
- C: 4_runtime_investigation에서 NO_FFMPEG 또는 worker 에러?
- D: NotVisible이 지속적으로 > 0?
- E: (API 로그 수집 범위 외)

---

## 6. 해결책

### 6.1 즉시 조치

| 조치 | 적용 방법 | 성공 판정 기준 |
|------|-----------|----------------|
| ASG_NOT_SSM 인스턴스 강제 교체 | Instance refresh 또는 해당 인스턴스 Terminate | ASG_NOT_SSM 빈 집합, 새 인스턴스 SSM 등록 |
| NotVisible 유령 메시지 | visibility timeout 대기 또는 Redrive (Dead Letter) | NotVisible 0으로 수렴 |
| Worker job claim 실패 | Worker 컨테이너 재시작 또는 이미지 재배포 | 4_runtime에서 ffmpeg 프로세스 확인 |

### 6.2 근본 조치

| 조치 | 적용 방법 | 성공 판정 기준 |
|------|-----------|----------------|
| UserData retry/exitcode | video_worker_user_data.sh에 set +e 제거, 실패 시 exit 1 | 6_console에서 "cloud-init" 실패 라인 없음 |
| SSM 등록 보장 | SSM Agent 설치/시작 타이밍, IAM Role 확인 | 3_ssm_registration에서 ASG=SSM |
| Worker 실패 시 delete_message | Worker 코드에서 예외 시에도 delete_message 호출 | NotVisible이 visibility timeout 내 감소 |
| Enqueue 보장 | upload_complete 패치 (VIDEO_UPLOAD_ENQUEUE 로그) | SQS Visible = 기대 메시지 수 |

---

## 7. 재발 방지 체크리스트

### 7.1 자동 검증 스크립트
"@
$report += "`n$f" + "powershell`n"
$report += @"
# 데이터 수집 + 보고서 생성 (AWS 자격증명 필요)
.\scripts\collect_video_worker_incident_data.ps1

# SSM 등록만 빠르게 확인
.\scripts\verify_video_worker_ssm.ps1

# 전체 진단 (Lambda, SQS, ASG, CloudWatch)
.\scripts\diagnose_video_worker_full.ps1

# Runtime (인스턴스별 docker/ffmpeg/worker 로그)
.\scripts\investigate_video_worker_runtime.ps1
"@
$report += "`n$f`n"
$report += @"

### 7.2 수동 체크

- [ ] ASG Desired vs 실제 InService 인스턴스 수
- [ ] SSM describe-instance-information에 모든 ASG 인스턴스 포함
- [ ] SQS ApproximateNumberOfMessagesNotVisible이 visibility timeout(기본 30초) 후 0 수렴
- [ ] Worker 로그에 "job claim" / ffmpeg 시작 / delete_message 확인
- [ ] Cloud-init / user-data 로그에서 ECR login, SSM param, docker run 성공 확인

---

**End of Report**
"@

[System.IO.File]::WriteAllText($reportPath, $report, $Utf8NoBom)
Write-Host "Report saved: $reportPath" -ForegroundColor Green
Write-Host "Data saved: $OutDir`n" -ForegroundColor Gray
