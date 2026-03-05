# V1 배포 검증 자동화 — 인프라/기능/Evidence 수집 후 최종 보고서 생성.
# 리소스 변경 없음. 검증만 수행. 결과: docs/00-SSOT/v1/reports/deploy-verification-latest.md, audit.latest.md, drift.latest.md 갱신.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키를 환경변수로 넣어 배포·검증·인증을 진행한다. 스크립트는 .env를 로드하지 않는다.
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
    Add-Finding -Severity "WARNING" -Area "Drift" -Message ("SSOT와 불일치 $($driftFail.Count)건: " + ($driftFail | ForEach-Object { "$($_.ResourceType)/$($_.Name)" } -join ", "))
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

# --- 9. 보고서 생성 (PASS/WARNING/FAIL + 근거, GO/NO-GO) ---
$s1Infra = "PASS"
if ($apiHealthStatus -ne "OK") { $s1Infra = "FAIL" }
elseif ($targetHealthyCount -eq 0 -and $targetTotalCount -gt 0) { $s1Infra = "FAIL" }
elseif ($rdsStatus -ne "available" -or $redisStatus -ne "available") { $s1Infra = "WARNING" }
elseif ($driftFail -and $driftFail.Count -gt 0) { $s1Infra = "WARNING" }

$s2Smoke = "PASS"
if ($apiHealthStatus -ne "OK") { $s2Smoke = "FAIL" }
elseif ($apiHealthResponseTime -and [int]($apiHealthResponseTime -replace 'ms','') -gt 2000) { $s2Smoke = "WARNING" }
elseif ($apiSmokeStatus -eq "root unreachable") { $s2Smoke = "WARNING" }

$s3Front = "PASS"
if ($frontStatus -eq "not checked") { $s3Front = "WARNING" }
elseif ($frontStatus -ne "OK") { $s3Front = "WARNING" }
if ($r2Status -ne "OK (wrangler list success)" -and $r2Status -ne "not checked") { $s3Front = "WARNING" }

$s4Sqs = "PASS"
if ([int]$msgDlqDepth -gt 0 -or [int]$aiDlqDepth -gt 0) { $s4Sqs = "WARNING" }

$s5Video = "WARNING"
$s5VideoNote = "수동 검증 권장: 3시간 샘플 1건 end-to-end, READY 전 미공개, 업로드 재시도·동시 2~3건. 근거: deploy-verification-latest.md 또는 수동 실행 로그."

$s6Obs = "PASS"
if ($alarmSummary -eq "not listed" -or $alarmSummary -match "0 alarms") { $s6Obs = "WARNING" }

$goNoGo = "GO"
$goNoGoDetail = ""
if ($finalStatus -eq "FAIL") { $goNoGo = "NO-GO"; $goNoGoDetail = "FAIL 항목 해결 후 재검증 필요." }
elseif ($finalStatus -eq "WARNING") { $goNoGo = "CONDITIONAL GO"; $goNoGoDetail = "WARNING 영향도·완화책·추적 계획 확인 후 배포 판단. 상세: 아래 리스크 섹션 및 deploy-verification-latest.md." }

$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# V1 Deployment Verification Report")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**명칭:** V1 통일 (V1.1 미사용). **SSOT:** docs/00-SSOT/v1/params.yaml. **리전:** $R. **전제:** 사용자 1,000~1,500, 동시 50~300 버스트, 운영 1인, 장애 대응 10~60분.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 배포 정보")
[void]$sb.AppendLine("| 항목 | 값 |")
[void]$sb.AppendLine("|------|-----|")
[void]$sb.AppendLine("| 검증 시각 | $verificationTime |")
[void]$sb.AppendLine("| 리전 | $R |")
[void]$sb.AppendLine("| 배포 스크립트 | scripts/v1/deploy.ps1 |")
[void]$sb.AppendLine("| 근거·로그 | reports/audit.latest.md, reports/drift.latest.md |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 1) 인프라 상태 (PASS/WARNING/FAIL + 근거)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| 항목 | 결과 | 근거(로그/지표/스크린샷 경로) |")
[void]$sb.AppendLine("|------|------|-------------------------------------|")
[void]$sb.AppendLine("| API ASG min/desired/max | $($ev.apiAsgDesired)/$($ev.apiAsgMin)/$($ev.apiAsgMax) | reports/audit.latest.md (apiAsg*) |")
[void]$sb.AppendLine("| ALB target health | $targetHealthyCount / $targetTotalCount healthy | AWS Console EC2 > Target Groups > academy-v1-api-tg |")
[void]$sb.AppendLine("| /health 200 | $apiHealthStatus $apiHealthResponseTime | curl 위 URL 또는 ALB DNS 직접 호출 |")
[void]$sb.AppendLine("| AI/Messaging ASG | $($ev.asgAiDesired)/$($ev.asgMessagingDesired) | reports/audit.latest.md (asgAi*, asgMessaging*) |")
[void]$sb.AppendLine("| SQS queue 연결·DLQ | Messaging depth $msgQueueDepth DLQ $msgDlqDepth / AI depth $aiQueueDepth DLQ $aiDlqDepth | SQS Console 또는 get-queue-attributes |")
[void]$sb.AppendLine("| Video Batch CE/Queue/JobDef | CE $($ev.batchVideoCeStatus) Queue $($ev.videoQueueState) JobDef rev $($ev.videoJobDefRevision) | reports/audit.latest.md, Batch Console |")
[void]$sb.AppendLine("| RDS 연결 가능 | $rdsStatus | RDS describe-db-instances (연결 테스트는 앱/psql 수동) |")
[void]$sb.AppendLine("| Redis 연결 가능 | $redisStatus | ElastiCache describe-replication-groups |")
[void]$sb.AppendLine("| **섹션 1 종합** | **$s1Infra** | |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 2) 기능 Smoke Test (PASS/WARNING/FAIL + 근거)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| 항목 | 결과 | 근거 |")
[void]$sb.AppendLine("|------|------|------|")
[void]$sb.AppendLine("| /health | $apiHealthStatus | 응답시간: $apiHealthResponseTime (기준 p95 &lt; 2s, 샘플 1회) |")
[void]$sb.AppendLine("| API root | $apiSmokeStatus | 동일 ALB DNS |")
[void]$sb.AppendLine("| 핵심 API 1~2개(인증/CRUD) | 수동 검증 권장 | 샘플 20회 평균/최대 기록 시 reports/ 에 URL 또는 로그 경로 기입 |")
[void]$sb.AppendLine("| **섹션 2 종합** | **$s2Smoke** | |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 3) 프론트 / R2 / CDN (PASS/WARNING/FAIL + 근거)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| 항목 | 결과 | 근거 |")
[void]$sb.AppendLine("|------|------|------|")
[void]$sb.AppendLine("| 프론트 URL 접속 | $frontStatus | FRONT_APP_URL env 설정 시 자동 검사 |")
[void]$sb.AppendLine("| 정적 자산(JS/CSS) 로딩 | 수동 검증 권장 | 브라우저 개발자도구 Network 탭 |")
[void]$sb.AppendLine("| CDN 캐시 정책 | 수동 검증 권장 | Cache-Control 헤더, 배포 시 purge 전략 (params front.*) |")
[void]$sb.AppendLine("| 프론트→API(CORS/쿠키/CSRF) | 수동 검증 권장 | 동일 도메인/credentials 요청 |")
[void]$sb.AppendLine("| R2 버킷 접근 | $r2Status | wrangler r2 bucket list |")
[void]$sb.AppendLine("| **섹션 3 종합** | **$s3Front** | |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 4) SQS 워커 테스트 (PASS/WARNING/FAIL + 근거)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| 항목 | 결과 | 근거 |")
[void]$sb.AppendLine("|------|------|------|")
[void]$sb.AppendLine("| AI queue enqueue→consume | 수동 검증 권장 | SQS 메시지 발송 후 워커 로그 확인 |")
[void]$sb.AppendLine("| Messaging queue enqueue→consume | 수동 검증 권장 | 동일 |")
[void]$sb.AppendLine("| DLQ 적재 없음 | Messaging DLQ=$msgDlqDepth AI DLQ=$aiDlqDepth | get-queue-attributes ApproximateNumberOfMessages (DLQ) |")
[void]$sb.AppendLine("| **섹션 4 종합** | **$s4Sqs** | |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 5) Video Pipeline 테스트 (3시간 영상 기준)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| 항목 | 결과 | 근거 |")
[void]$sb.AppendLine("|------|------|------|")
[void]$sb.AppendLine("| 3시간 샘플 1건 end-to-end | 수동 검증 권장 | 인코딩→R2 staging→검증→READY, HLS 재생 |")
[void]$sb.AppendLine("| 유령데이터 방지(READY 전 미공개) | 설계 반영 | API playback_mixin READY만 허용, 목록 READY 필터 |")
[void]$sb.AppendLine("| 업로드 실패 재시도/복구 | 설계 반영 | DynamoDB checkpoint, 재인코딩 최소화 (V1-DEPLOYMENT-VERIFICATION §7.3) |")
[void]$sb.AppendLine("| 동시 2~3건 | 수동 검증 권장 | 2~3건 동시 제출 후 Job 완료·queue depth 확인 |")
[void]$sb.AppendLine("| **섹션 5 종합** | **$s5Video** | $s5VideoNote |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 6) 관측/알람")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| 항목 | 결과 | 근거 |")
[void]$sb.AppendLine("|------|------|------|")
[void]$sb.AppendLine("| 최소 알람 세트(API 5XX, SQS depth/DLQ, Batch failed/stuck/backlog, RDS, Redis) | $alarmSummary | CloudWatch > Alarms (academy/v1 필터) |")
[void]$sb.AppendLine("| 로그 retention 30d | params observability.logRetentionDays | Ensure-VideoBatchLogRetention, Batch 로그 그룹 |")
[void]$sb.AppendLine("| **섹션 6 종합** | **$s6Obs** | |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 7) 리스크 및 GO/NO-GO 권고")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("### 발견 사항(리스크)")
if ($findings.Count -gt 0) {
    foreach ($f in $findings) {
        [void]$sb.AppendLine("- **$($f.Severity)** [$($f.Area)] $($f.Message)")
    }
} else {
    [void]$sb.AppendLine("- Drift 없음, 인프라 상태 정상 범위.")
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("### GO/NO-GO")
[void]$sb.AppendLine("| 판정 | 내용 |")
[void]$sb.AppendLine("|------|------|")
[void]$sb.AppendLine("| **$goNoGo** | $goNoGoDetail |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("- **FAIL 1건 이상** → **NO-GO**. 재검증 후 재실행.")
[void]$sb.AppendLine("- **WARNING만** → **CONDITIONAL GO**. 영향도·완화책·추적 계획 확인 후 배포 여부 결정.")
[void]$sb.AppendLine("- **PASS만** → **GO**.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 최종 상태")
[void]$sb.AppendLine("**$finalStatus**")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**연관 보고서:** audit.latest.md, drift.latest.md (동시 갱신됨).")

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
