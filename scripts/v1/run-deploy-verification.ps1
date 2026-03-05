# V1 배포 검증 자동화 — 인프라/기능/Evidence 수집 후 최종 보고서 생성.
# 리소스 변경 없음. 검증만 수행. 결과: docs/00-SSOT/v1/reports/deploy-verification-latest.md, audit.latest.md, drift.latest.md 갱신.
# 사용: pwsh -File scripts/v1/run-deploy-verification.ps1 [-AwsProfile default] (run-with-env 권장)
$ErrorActionPreference = "Stop"
param([string]$AwsProfile = "")
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path

. (Join-Path $ScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
    Write-Host "Using AWS_PROFILE: $env:AWS_PROFILE" -ForegroundColor Gray
}

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\diff.ps1")
. (Join-Path $ScriptRoot "core\evidence.ps1")
. (Join-Path $ScriptRoot "core\reports.ps1")

$null = Load-SSOT -Env prod
$script:PlanMode = $true
$R = $script:Region

$verificationTime = Get-Date -Format "o"
$findings = [System.Collections.ArrayList]::new()
$finalStatus = "PASS"

function Add-Finding { param([string]$Severity, [string]$Area, [string]$Message)
    [void]$findings.Add([PSCustomObject]@{ Severity = $Severity; Area = $Area; Message = $Message })
    if ($Severity -eq "FAIL") { $script:finalStatus = "FAIL" }
    elseif ($Severity -eq "WARNING" -and $script:finalStatus -eq "PASS") { $script:finalStatus = "WARNING" }
}

Write-Host "`n=== V1 배포 검증 (read-only) ===" -ForegroundColor Cyan

# --- 0. ALB DNS (Evidence /health 체크용) ---
if ($script:ApiAlbName) {
    try {
        $alb0 = Invoke-AwsJson @("elbv2", "describe-load-balancers", "--names", $script:ApiAlbName, "--region", $R, "--output", "json")
        if ($alb0 -and $alb0.LoadBalancers -and $alb0.LoadBalancers.Count -gt 0) {
            $script:ApiBaseUrl = "http://$($alb0.LoadBalancers[0].DNSName)"
        }
    } catch { }
}

# --- 1. Drift / Evidence (audit, drift 갱신) ---
Write-Host "`n[1] Drift / Evidence 수집..." -ForegroundColor Cyan
$driftRows = Get-StructuralDrift
Save-DriftReport -Rows $driftRows
$ev = Get-EvidenceSnapshot -NetprobeJobId "" -NetprobeStatus "skipped"
$auditMd = Convert-EvidenceToMarkdown -Ev $ev
Save-EvidenceReport -MarkdownContent $auditMd

$driftFail = $driftRows | Where-Object { $_.Action -ne "NoOp" }
if ($driftFail -and $driftFail.Count -gt 0) {
    Add-Finding -Severity "WARNING" -Area "Drift" -Message "SSOT와 불일치 $($driftFail.Count)건: $($driftFail | ForEach-Object { "$($_.ResourceType)/$($_.Name)" } | Join-String -Separator ', ')"
}

# --- 2. ALB DNS 및 /health (상세) ---
$apiHealthStatus = "unreachable"
$apiHealthResponseTime = ""
$albDns = ""
$targetHealthyCount = 0
$targetTotalCount = 0

if ($script:ApiAlbName) {
    try {
        $alb = Invoke-AwsJson @("elbv2", "describe-load-balancers", "--names", $script:ApiAlbName, "--region", $R, "--output", "json")
        if ($alb -and $alb.LoadBalancers -and $alb.LoadBalancers.Count -gt 0) {
            $albDns = $alb.LoadBalancers[0].DNSName
            $healthUrl = "http://${albDns}/$($script:ApiHealthPath.TrimStart('/'))"
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            try {
                $hr = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
                $sw.Stop()
                $apiHealthStatus = if ($hr.StatusCode -eq 200) { "OK" } else { "HTTP $($hr.StatusCode)" }
                $apiHealthResponseTime = "$($sw.ElapsedMilliseconds)ms"
                if ($hr.StatusCode -ne 200) { Add-Finding -Severity "WARNING" -Area "API" -Message "/health returned $($hr.StatusCode)" }
                if ($sw.ElapsedMilliseconds -gt 2000) { Add-Finding -Severity "WARNING" -Area "API" -Message "/health 응답 > 2s ($apiHealthResponseTime)" }
            } catch {
                $apiHealthStatus = "unreachable"
                Add-Finding -Severity "FAIL" -Area "API" -Message "/health unreachable: $($_.Exception.Message)"
            }
        }
        $tg = Invoke-AwsJson @("elbv2", "describe-target-groups", "--names", $script:ApiTargetGroupName, "--region", $R, "--output", "json")
        if ($tg -and $tg.TargetGroups -and $tg.TargetGroups.Count -gt 0) {
            $tgArn = $tg.TargetGroups[0].TargetGroupArn
            $th = Invoke-AwsJson @("elbv2", "describe-target-health", "--target-group-arn", $tgArn, "--region", $R, "--output", "json")
            if ($th -and $th.TargetHealthDescriptions) {
                $targetTotalCount = $th.TargetHealthDescriptions.Count
                $targetHealthyCount = @($th.TargetHealthDescriptions | Where-Object { $_.TargetHealth.State -eq "healthy" }).Count
                if ($targetHealthyCount -eq 0 -and $targetTotalCount -gt 0) { Add-Finding -Severity "FAIL" -Area "API" -Message "ALB target healthy 0 / $targetTotalCount" }
            }
        }
    } catch { Add-Finding -Severity "WARNING" -Area "API" -Message "ALB/Target 조회 실패: $($_.Exception.Message)" }
}

# --- 3. RDS / Redis 상태 ---
$rdsStatus = "not checked"
$redisStatus = "not checked"
if ($script:RdsDbIdentifier) {
    try {
        $rds = Invoke-AwsJson @("rds", "describe-db-instances", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $R, "--output", "json")
        if ($rds -and $rds.DBInstances -and $rds.DBInstances.Count -gt 0) {
            $rdsStatus = $rds.DBInstances[0].DBInstanceStatus
            if ($rdsStatus -ne "available") { Add-Finding -Severity "WARNING" -Area "DB" -Message "RDS status: $rdsStatus" }
        } else { $rdsStatus = "not found"; Add-Finding -Severity "WARNING" -Area "DB" -Message "RDS not found" }
    } catch { $rdsStatus = "error"; Add-Finding -Severity "WARNING" -Area "DB" -Message "RDS describe failed: $($_.Exception.Message)" }
}
if ($script:RedisReplicationGroupId) {
    try {
        $redis = Invoke-AwsJson @("elasticache", "describe-replication-groups", "--replication-group-id", $script:RedisReplicationGroupId, "--region", $R, "--output", "json")
        if ($redis -and $redis.ReplicationGroups -and $redis.ReplicationGroups.Count -gt 0) {
            $redisStatus = $redis.ReplicationGroups[0].Status
            if ($redisStatus -ne "available") { Add-Finding -Severity "WARNING" -Area "Cache" -Message "Redis status: $redisStatus" }
        } else { $redisStatus = "not found"; Add-Finding -Severity "WARNING" -Area "Cache" -Message "Redis not found" }
    } catch { $redisStatus = "error"; Add-Finding -Severity "WARNING" -Area "Cache" -Message "Redis describe failed: $($_.Exception.Message)" }
}

# --- 4. SQS 큐 깊이 / DLQ ---
$msgQueueDepth = ""
$msgDlqDepth = ""
$aiQueueDepth = ""
$aiDlqDepth = ""
function Get-SqsDepth {
    param([string]$QueueUrl)
    if (-not $QueueUrl) { return "n/a" }
    try {
        $a = Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $QueueUrl, "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "--region", $R, "--output", "json")
        $vis = [int]($a.Attributes.ApproximateNumberOfMessages ?? 0)
        $inFlight = [int]($a.Attributes.ApproximateNumberOfMessagesNotVisible ?? 0)
        return "$vis (in-flight $inFlight)"
    } catch { return "error" }
}
if ($script:MessagingSqsQueueUrl) { $msgQueueDepth = Get-SqsDepth -QueueUrl $script:MessagingSqsQueueUrl }
if ($script:AiSqsQueueUrl) { $aiQueueDepth = Get-SqsDepth -QueueUrl $script:AiSqsQueueUrl }
$msgDlqUrl = $script:MessagingSqsQueueUrl -replace '/academy-v1-messaging-queue$', '/academy-v1-messaging-queue-dlq'
$aiDlqUrl = $script:AiSqsQueueUrl -replace '/academy-v1-ai-queue$', '/academy-v1-ai-queue-dlq'
try {
    $dlqA = Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $msgDlqUrl, "--attribute-names", "ApproximateNumberOfMessages", "--region", $R, "--output", "json")
    $msgDlqDepth = $dlqA.Attributes.ApproximateNumberOfMessages
} catch { $msgDlqDepth = "n/a" }
try {
    $dlqB = Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $aiDlqUrl, "--attribute-names", "ApproximateNumberOfMessages", "--region", $R, "--output", "json")
    $aiDlqDepth = $dlqB.Attributes.ApproximateNumberOfMessages
} catch { $aiDlqDepth = "n/a" }
if ([int]$msgDlqDepth -gt 0) { Add-Finding -Severity "WARNING" -Area "SQS" -Message "Messaging DLQ messages: $msgDlqDepth" }
if ([int]$aiDlqDepth -gt 0) { Add-Finding -Severity "WARNING" -Area "SQS" -Message "AI DLQ messages: $aiDlqDepth" }

# --- 5. 리소스 수 (EC2, Batch 노드) ---
$ec2Count = 0
try {
    $inst = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=instance-state-name,Values=running", "Name=tag:Project,Values=academy", "--region", $R, "--output", "json")
    if ($inst -and $inst.Reservations) { $ec2Count = ($inst.Reservations | ForEach-Object { $_.Instances } | Where-Object { $_ }).Count }
} catch { }
$batchActiveJobs = "n/a"
try {
    $jobs = Invoke-AwsJson @("batch", "list-jobs", "--job-queue", $script:VideoQueueName, "--job-status", "RUNNING", "--region", $R, "--output", "json")
    if ($jobs -and $jobs.jobSummaryList) { $batchActiveJobs = $jobs.jobSummaryList.Count }
} catch { }

# --- 6. R2 / CDN (선택: wrangler) ---
$r2Status = "not checked"
$cdnStatus = "not checked"
if (Get-Command npx -ErrorAction SilentlyContinue) {
    try {
        $r2Out = npx wrangler r2 bucket list 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { $r2Status = "OK (wrangler list success)" } else { $r2Status = "wrangler failed" }
    } catch { $r2Status = "wrangler not run" }
}

# --- 7. 프론트/API Smoke (선택: env) ---
$frontStatus = "not checked"
$apiSmokeStatus = "not checked"
$frontUrl = $env:FRONT_APP_URL
if ($frontUrl) {
    try {
        $fr = Invoke-WebRequest -Uri $frontUrl -UseBasicParsing -TimeoutSec 15
        $frontStatus = if ($fr.StatusCode -eq 200) { "OK" } else { "HTTP $($fr.StatusCode)" }
    } catch { $frontStatus = "unreachable"; Add-Finding -Severity "WARNING" -Area "Front" -Message "Front URL unreachable" }
}
if ($albDns) {
    $apiRoot = "http://${albDns}/"
    try {
        $root = Invoke-WebRequest -Uri $apiRoot -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        $apiSmokeStatus = "root $($root.StatusCode)"
    } catch { $apiSmokeStatus = "root unreachable" }
}

# --- 8. CloudWatch 알람 (있으면) ---
$alarmSummary = "not listed"
try {
    $alarms = Invoke-AwsJson @("cloudwatch", "describe-alarms", "--region", $R, "--output", "json")
    if ($alarms -and $alarms.MetricAlarms) {
        $academyAlarms = @($alarms.MetricAlarms | Where-Object { $_.AlarmName -like "academy*" -or $_.AlarmName -like "*v1*" })
        $alarmSummary = "$($academyAlarms.Count) alarms (academy/v1)"
    }
} catch { }

# --- 9. 보고서 생성 ---
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# V1 Deployment Verification Report")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 배포 정보")
[void]$sb.AppendLine("| 항목 | 값 |")
[void]$sb.AppendLine("|------|-----|")
[void]$sb.AppendLine("| **검증 시각** | $verificationTime |")
[void]$sb.AppendLine("| **리전** | $R |")
[void]$sb.AppendLine("| **배포 스크립트** | scripts/v1/deploy.ps1 |")
[void]$sb.AppendLine("| **SSOT** | docs/00-SSOT/v1/params.yaml |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 인프라 상태")
[void]$sb.AppendLine("| 영역 | 항목 | 상태 |")
[void]$sb.AppendLine("|------|------|------|")
[void]$sb.AppendLine("| **API** | ASG desired/min/max | $($ev.apiAsgDesired) / $($ev.apiAsgMin) / $($ev.apiAsgMax) |")
[void]$sb.AppendLine("| **API** | ALB target health | $targetHealthyCount / $targetTotalCount healthy |")
[void]$sb.AppendLine("| **API** | /health | $apiHealthStatus $apiHealthResponseTime |")
[void]$sb.AppendLine("| **Video Batch** | CE (video) | $($ev.batchVideoCeStatus) / $($ev.batchVideoCeState) |")
[void]$sb.AppendLine("| **Video Batch** | Queue | $($ev.videoQueueState) |")
[void]$sb.AppendLine("| **Video Batch** | JobDef revision | $($ev.videoJobDefRevision) |")
[void]$sb.AppendLine("| **AI Worker** | ASG | $($ev.asgAiDesired)/$($ev.asgAiMin)/$($ev.asgAiMax) |")
[void]$sb.AppendLine("| **Messaging Worker** | ASG | $($ev.asgMessagingDesired)/$($ev.asgMessagingMin)/$($ev.asgMessagingMax) |")
[void]$sb.AppendLine("| **DB** | RDS | $rdsStatus |")
[void]$sb.AppendLine("| **Cache** | Redis | $redisStatus |")
[void]$sb.AppendLine("| **스토리지** | R2 | $r2Status |")
[void]$sb.AppendLine("| **CDN/프론트** | 접근 | $frontStatus |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## SQS / 메시징")
[void]$sb.AppendLine("| 큐 | 메인 depth | DLQ depth |")
[void]$sb.AppendLine("|-----|-------------|------------|")
[void]$sb.AppendLine("| Messaging | $msgQueueDepth | $msgDlqDepth |")
[void]$sb.AppendLine("| AI | $aiQueueDepth | $aiDlqDepth |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 기능 테스트")
[void]$sb.AppendLine("| 항목 | 결과 |")
[void]$sb.AppendLine("|------|------|")
[void]$sb.AppendLine("| API /health | $apiHealthStatus |")
[void]$sb.AppendLine("| API root/smoke | $apiSmokeStatus |")
[void]$sb.AppendLine("| 프론트 URL | $frontStatus |")
[void]$sb.AppendLine("| 메시징/AI enqueue·DLQ | 수동 검증 권장 |")
[void]$sb.AppendLine("| Video pipeline | 수동 검증 권장 (샘플 업로드 → Job → READY) |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 관측/알람")
[void]$sb.AppendLine("| 항목 | 상태 |")
[void]$sb.AppendLine("|------|------|")
[void]$sb.AppendLine("| CloudWatch 알람 | $alarmSummary |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 비용·리소스 요약")
[void]$sb.AppendLine("| 항목 | 값 |")
[void]$sb.AppendLine("|------|-----|")
[void]$sb.AppendLine("| EC2 (running, Project=academy) | $ec2Count |")
[void]$sb.AppendLine("| Batch RUNNING jobs (video queue) | $batchActiveJobs |")
[void]$sb.AppendLine("| RDS | $rdsStatus |")
[void]$sb.AppendLine("| Redis | $redisStatus |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 리스크 및 권장 사항")
if ($findings.Count -gt 0) {
    foreach ($f in $findings) {
        [void]$sb.AppendLine("- **$($f.Severity)** [$($f.Area)] $($f.Message)")
    }
} else {
    [void]$sb.AppendLine("- Drift 없음, 인프라 상태 정상 범위.")
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 최종 상태")
[void]$sb.AppendLine("**$finalStatus**")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("**Evidence/Drift:** audit.latest.md, drift.latest.md 동시 갱신됨.")

$reportPath = Join-Path $RepoRoot "docs\00-SSOT\v1\reports"
if (-not (Test-Path $reportPath)) { New-Item -ItemType Directory -Path $reportPath -Force | Out-Null }
Save-DeployVerificationReport -MarkdownContent $sb.ToString()

Write-Host "`n=== 검증 완료 ===" -ForegroundColor Green
Write-Host "  최종 상태: $finalStatus" -ForegroundColor $(if ($finalStatus -eq "PASS") { "Green" } elseif ($finalStatus -eq "WARNING") { "Yellow" } else { "Red" })
Write-Host "  보고서: docs/00-SSOT/v1/reports/deploy-verification-latest.md" -ForegroundColor Cyan
Write-Host "  audit.latest.md, drift.latest.md 갱신됨." -ForegroundColor Gray
if ($findings.Count -gt 0) {
    Write-Host "  발견 사항: $($findings.Count)건" -ForegroundColor Yellow
    $findings | ForEach-Object { Write-Host "    [$($_.Severity)] $($_.Area): $($_.Message)" -ForegroundColor Gray }
}

exit $(if ($finalStatus -eq "FAIL") { 1 } else { 0 })
