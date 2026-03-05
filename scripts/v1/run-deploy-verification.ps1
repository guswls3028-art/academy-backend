# V1 배포 검증 자동화 — 인프라/기능/Evidence 수집 후 최종 보고서 생성.
# 리소스 변경 없음. 검증만 수행. 결과: docs/00-SSOT/v1/reports/deploy-verification-latest.md, V1-FINAL-REPORT.md, audit.latest.md, drift.latest.md 갱신.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키를 환경변수로 넣어 배포·검증·인증을 진행한다. 스크립트는 .env를 로드하지 않는다.
# 사용: pwsh -File scripts/v1/run-deploy-verification.ps1 [-AwsProfile default] (run-with-env 권장)
param([string]$AwsProfile = "")
$ErrorActionPreference = "Stop"
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
    Add-Finding -Severity "WARNING" -Area "Drift" -Message ("SSOT와 불일치 $($driftFail.Count)건: " + (($driftFail | ForEach-Object { "$($_.ResourceType)/$($_.Name)" }) -join ", "))
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

# --- 2b. API 공개 URL(도메인/HTTPS) /health — API_PUBLIC_URL 또는 front.domains.api 기반
$apiPublicHealthStatus = "not checked"
$apiPublicUrl = $env:API_PUBLIC_URL
if (-not $apiPublicUrl -and $script:FrontDomainApi -and $script:FrontDomainApi.Trim() -ne "") {
    $apiPublicUrl = "https://$($script:FrontDomainApi.Trim())"
}
if ($apiPublicUrl) {
    $apiPublicUrl = $apiPublicUrl.TrimEnd('/')
    $publicHealthUrl = "$apiPublicUrl/$($script:ApiHealthPath.TrimStart('/'))"
    try {
        $phr = Invoke-WebRequest -Uri $publicHealthUrl -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        $apiPublicHealthStatus = if ($phr.StatusCode -eq 200) { "OK" } else { "HTTP $($phr.StatusCode)" }
        if ($phr.StatusCode -ne 200) { Add-Finding -Severity "WARNING" -Area "API" -Message "API_PUBLIC_URL/도메인 /health returned $($phr.StatusCode)" }
    } catch {
        $apiPublicHealthStatus = "unreachable"
        Add-Finding -Severity "WARNING" -Area "API" -Message "API 공개 URL /health unreachable: $publicHealthUrl — $($_.Exception.Message)"
    }
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
        $vis = [int](if ($null -eq $a.Attributes.ApproximateNumberOfMessages) { 0 } else { $a.Attributes.ApproximateNumberOfMessages })
        $inFlight = [int](if ($null -eq $a.Attributes.ApproximateNumberOfMessagesNotVisible) { 0 } else { $a.Attributes.ApproximateNumberOfMessagesNotVisible })
        return "$vis (in-flight $inFlight)"
    } catch { return "error" }
}
if ($script:MessagingSqsQueueUrl) { $msgQueueDepth = Get-SqsDepth -QueueUrl $script:MessagingSqsQueueUrl }
if ($script:AiSqsQueueUrl) { $aiQueueDepth = Get-SqsDepth -QueueUrl $script:AiSqsQueueUrl }
$msgDlqUrl = $null
$aiDlqUrl = $null
if ($script:MessagingSqsQueueName -and $script:MessagingDlqSuffix) {
    $msgDlqName = $script:MessagingSqsQueueName.TrimEnd() + $script:MessagingDlqSuffix.TrimStart()
    try {
        $mq = Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $msgDlqName, "--region", $R, "--output", "json")
        if ($mq -and $mq.QueueUrl) { $msgDlqUrl = $mq.QueueUrl }
    } catch { }
}
if (-not $msgDlqUrl) { $msgDlqUrl = $script:MessagingSqsQueueUrl -replace '/academy-v1-messaging-queue$', '/academy-v1-messaging-queue-dlq' }
if ($script:AiSqsQueueName -and $script:AiDlqSuffix) {
    $aiDlqName = $script:AiSqsQueueName.TrimEnd() + $script:AiDlqSuffix.TrimStart()
    try {
        $aq = Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $aiDlqName, "--region", $R, "--output", "json")
        if ($aq -and $aq.QueueUrl) { $aiDlqUrl = $aq.QueueUrl }
    } catch { }
}
if (-not $aiDlqUrl) { $aiDlqUrl = $script:AiSqsQueueUrl -replace '/academy-v1-ai-queue$', '/academy-v1-ai-queue-dlq' }
if ($msgDlqUrl) {
    try {
        $dlqA = Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $msgDlqUrl, "--attribute-names", "ApproximateNumberOfMessages", "--region", $R, "--output", "json")
        $msgDlqDepth = $dlqA.Attributes.ApproximateNumberOfMessages
    } catch { $msgDlqDepth = "n/a" }
} else { $msgDlqDepth = "n/a" }
if ($aiDlqUrl) {
    try {
        $dlqB = Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $aiDlqUrl, "--attribute-names", "ApproximateNumberOfMessages", "--region", $R, "--output", "json")
        $aiDlqDepth = $dlqB.Attributes.ApproximateNumberOfMessages
    } catch { $aiDlqDepth = "n/a" }
} else { $aiDlqDepth = "n/a" }
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

# --- 5b. Video E2E 근거 (VIDEO_E2E_TEST_JOB_ID 설정 시 자동 수집) ---
$videoE2EEvidence = ""
$e2eJobId = $env:VIDEO_E2E_TEST_JOB_ID
if ($e2eJobId -and $e2eJobId.Trim() -ne "") {
    try {
        $jobDesc = Invoke-AwsJson @("batch", "describe-jobs", "--jobs", $e2eJobId.Trim(), "--region", $R, "--output", "json")
        if ($jobDesc -and $jobDesc.jobs -and $jobDesc.jobs.Count -gt 0) {
            $j = $jobDesc.jobs[0]
            $exitCode = ""
            if ($j.container -and $null -ne $j.container.exitCode) { $exitCode = " containerExitCode=$($j.container.exitCode)" }
            $videoE2EEvidence = "jobId=$($j.jobId) status=$($j.status) statusReason=$($j.statusReason) createdAt=$($j.createdAt) stoppedAt=$($j.stoppedAt)$exitCode"
            if ($j.status -eq "SUCCEEDED") { $videoE2EEvidence += " (E2E 완주 근거)" }
            else { Add-Finding -Severity "WARNING" -Area "Video" -Message "VIDEO_E2E_TEST_JOB_ID=$e2eJobId status=$($j.status). SUCCEEDED이면 보고서에 근거로 기록됨." }
        } else {
            $videoE2EEvidence = "jobId=$e2eJobId (describe-jobs 결과 없음)"
        }
    } catch {
        $videoE2EEvidence = "jobId=$e2eJobId (조회 실패: $($_.Exception.Message))"
    }
}

# --- 6. R2 / CDN (선택: wrangler) ---
$r2Status = "not checked"
$cdnStatus = "not checked"
if (Get-Command npx -ErrorAction SilentlyContinue) {
    try {
        $r2Out = npx wrangler r2 bucket list 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { $r2Status = "OK (wrangler list success)" } else { $r2Status = "wrangler failed" }
    } catch { $r2Status = "wrangler not run" }
}

# --- 6b. 프론트 증거 기반 검증 (SSOT front.domains.app / api 있으면) ---
$frontAppUrl = ""
if ($script:FrontDomainApp -and $script:FrontDomainApp.Trim() -ne "") {
    $frontAppUrl = "https://$($script:FrontDomainApp.Trim())".TrimEnd('/')
}
if (-not $frontAppUrl -and $env:FRONT_APP_URL) { $frontAppUrl = $env:FRONT_APP_URL.Trim().TrimEnd('/') }

$frontAppStatusCode = ""
$frontIndexCacheControl = ""
$frontAssetSampleUrl = ""
$frontAssetCacheControl = ""
$corsStaticStatus = "not checked"
$corsStaticDetail = ""

if ($frontAppUrl) {
    try {
        $frMain = Invoke-WebRequest -Uri "$frontAppUrl/" -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
        $frontAppStatusCode = $frMain.StatusCode
        $frontIndexCacheControl = $frMain.Headers["Cache-Control"]
        if (-not $frontIndexCacheControl) { $frontIndexCacheControl = "(none)" }
        # no-cache 계열 여부: max-age=0 또는 no-cache 포함
        $indexNoCacheOk = ($frontIndexCacheControl -match "max-age\s*=\s*0" -or $frontIndexCacheControl -match "no-cache")
        if (-not $indexNoCacheOk -and $frontIndexCacheControl -ne "(none)") {
            Add-Finding -Severity "WARNING" -Area "Front" -Message "index Cache-Control no-cache 권장: 현재 '$frontIndexCacheControl'"
        }
        # 해시된 asset 1개 찾기: script src 또는 link href에서 .js/.css (해시 패턴 또는 /assets/)
        $content = $frMain.Content
        $assetUrl = $null
        if ($content -match '<script[^>]+src\s*=\s*["'']([^"'']+\.(?:js|mjs))["'']') { $assetUrl = $matches[1] }
        elseif ($content -match '<link[^>]+href\s*=\s*["'']([^"'']+\.css)["'']') { $assetUrl = $matches[1] }
        if ($assetUrl) {
            if ($assetUrl -notmatch '^https?://') {
                $base = $frontAppUrl.TrimEnd('/')
                $assetUrl = if ($assetUrl.StartsWith("/")) { "$base$assetUrl" } else { "$base/$assetUrl" }
            }
            $frontAssetSampleUrl = $assetUrl
            try {
                $frAsset = Invoke-WebRequest -Uri $assetUrl -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
                $frontAssetCacheControl = $frAsset.Headers["Cache-Control"]
                if (-not $frontAssetCacheControl) { $frontAssetCacheControl = "(none)" }
                $assetLongCacheOk = $frontAssetCacheControl -match "max-age\s*=\s*31536000|31536000"
                if (-not $assetLongCacheOk -and $frontAssetCacheControl -ne "(none)") {
                    Add-Finding -Severity "WARNING" -Area "Front" -Message "해시 자산 Cache-Control 1년 권장: 현재 '$frontAssetCacheControl'"
                }
            } catch {
                $frontAssetCacheControl = "fetch failed: $($_.Exception.Message)"
                Add-Finding -Severity "WARNING" -Area "Front" -Message "자산 요청 실패: $assetUrl"
            }
        } else {
            $frontAssetCacheControl = "no hashed asset found in index"
        }
    } catch {
        $frontAppStatusCode = "error"
        $frontIndexCacheControl = $_.Exception.Message
        Add-Finding -Severity "WARNING" -Area "Front" -Message "프론트 URL 접속 실패: $frontAppUrl — $_"
    }
    # CORS 정적 검사: allowedOrigins에 app 도메인 포함 여부
    $appOrigin = "https://$($script:FrontDomainApp.Trim())".TrimEnd('/')
    if (-not $script:FrontCorsAllowedOrigins -or $script:FrontCorsAllowedOrigins.Count -eq 0) {
        $corsStaticStatus = "WARNING"
        $corsStaticDetail = "front.cors.allowedOrigins 비어 있음. CORS 사용 시 params에 app 도메인 추가 권장."
        Add-Finding -Severity "WARNING" -Area "Front" -Message $corsStaticDetail
    } else {
        $found = $false
        foreach ($o in $script:FrontCorsAllowedOrigins) {
            $oo = if ($o -is [string]) { $o.Trim().TrimEnd('/') } else { "" }
            if ($oo -eq $appOrigin -or $oo -eq "https://$($script:FrontDomainApp.Trim())") { $found = $true; break }
        }
        $corsStaticStatus = if ($found) { "OK" } else { "WARNING" }
        $corsStaticDetail = if ($found) { "app 도메인 포함됨" } else { "allowedOrigins에 $appOrigin 없음" }
        if (-not $found) { Add-Finding -Severity "WARNING" -Area "Front" -Message $corsStaticDetail }
    }
}

# --- 7. 프론트/API Smoke (선택: env) ---
$frontStatus = "not checked"
$apiSmokeStatus = "not checked"
if ($frontAppStatusCode -eq 200) { $frontStatus = "OK" }
elseif ($frontAppStatusCode -match "^\d+$") { $frontStatus = "HTTP $frontAppStatusCode" }
elseif ($frontAppStatusCode -eq "error") { $frontStatus = "unreachable" }
$frontUrl = $frontAppUrl
if (-not $frontUrl -and $env:FRONT_APP_URL) {
    $frontUrl = $env:FRONT_APP_URL
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
if ($apiPublicUrl) {
    [void]$sb.AppendLine("| API 공개 URL(도메인) /health | $apiPublicHealthStatus | API_PUBLIC_URL 또는 front.domains.api: $apiPublicUrl |")
}
[void]$sb.AppendLine("| AI/Messaging ASG | $($ev.asgAiDesired)/$($ev.asgMessagingDesired) | reports/audit.latest.md (asgAi*, asgMessaging*) |")
[void]$sb.AppendLine("| SQS queue 연결·DLQ | Messaging depth $msgQueueDepth DLQ $msgDlqDepth / AI depth $aiQueueDepth DLQ $aiDlqDepth | SQS Console 또는 get-queue-attributes |")
[void]$sb.AppendLine("| Video Batch CE/Queue/JobDef | CE $($ev.batchVideoCeStatus) Queue $($ev.videoQueueState) JobDef rev $($ev.videoJobDefRevision) | reports/audit.latest.md, Batch Console |")
[void]$sb.AppendLine("| Video Ops CE/Queue, EventBridge | Ops CE $($ev.opsCeStatus) Ops Queue $($ev.opsQueueState) Reconcile $($ev.eventBridgeReconcileState) ScanStuck $($ev.eventBridgeScanStuckState) | reports/audit.latest.md, rca.video.latest.md |")
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
[void]$sb.AppendLine("| 프론트 URL 접속 | $frontStatus | " + $(if ($frontAppUrl) { "URL: $frontAppUrl/ 응답코드: $frontAppStatusCode" } else { "FRONT_APP_URL 또는 SSOT front.domains.app 설정 시 검사" }) + " |")
if ($frontAppUrl -and $frontIndexCacheControl -ne "") {
    [void]$sb.AppendLine("| index.html Cache-Control | " + $(if ($frontIndexCacheControl -match "max-age\s*=\s*0|no-cache") { "PASS (no-cache 계열)" } else { "WARNING/수동 확인" }) + " | $frontIndexCacheControl |")
}
if ($frontAssetSampleUrl -ne "") {
    [void]$sb.AppendLine("| 해시 자산(JS/CSS) Cache-Control | " + $(if ($frontAssetCacheControl -match "31536000") { "PASS (1년)" } else { "WARNING/수동 확인" }) + " | 샘플: $frontAssetSampleUrl → $frontAssetCacheControl |")
}
[void]$sb.AppendLine("| 정적 자산(JS/CSS) 로딩 | " + $(if ($frontAssetSampleUrl) { "자동 검사 완료" } else { "수동 검증 권장" }) + " | " + $(if ($frontAssetSampleUrl) { "위 해시 자산 요청 근거" } else { "브라우저 개발자도구 Network 탭" }) + " |")
[void]$sb.AppendLine("| CDN 캐시 정책 | " + $(if ($frontIndexCacheControl -ne "" -or $frontAssetCacheControl -ne "") { "근거 위 참조" } else { "수동 검증 권장" }) + " | Cache-Control 헤더, 배포 시 purge: SSOT front.purgeOnDeploy |")
[void]$sb.AppendLine("| 프론트→API(CORS/쿠키/CSRF) | 수동 검증 권장 | 동일 도메인/credentials 요청 |")
if ($corsStaticStatus -ne "not checked") {
    [void]$sb.AppendLine("| CORS allowedOrigins 정적 검사 | $corsStaticStatus | $corsStaticDetail |")
}
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
if ($videoE2EEvidence) {
    [void]$sb.AppendLine("| Video E2E 근거 (VIDEO_E2E_TEST_JOB_ID) | 자동 수집 | $videoE2EEvidence |")
}
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

# V1 최종 배포 검증 보고서 (reports/V1-FINAL-REPORT.md)
$finalSb = [System.Text.StringBuilder]::new()
[void]$finalSb.AppendLine("# V1 최종 배포 검증 보고서")
[void]$finalSb.AppendLine("")
[void]$finalSb.AppendLine("**명칭:** V1 통일. **SSOT:** docs/00-SSOT/v1/params.yaml. **배포:** scripts/v1/deploy.ps1. **리전:** $R.")
[void]$finalSb.AppendLine("")
[void]$finalSb.AppendLine("## 요약")
[void]$finalSb.AppendLine("| 항목 | 값 |")
[void]$finalSb.AppendLine("|------|-----|")
[void]$finalSb.AppendLine("| 검증 시각 | $verificationTime |")
[void]$finalSb.AppendLine("| 최종 상태 | $finalStatus |")
[void]$finalSb.AppendLine("| GO/NO-GO | **$goNoGo** |")
[void]$finalSb.AppendLine("")
[void]$finalSb.AppendLine("$goNoGoDetail")
[void]$finalSb.AppendLine("")
[void]$finalSb.AppendLine("## 상세 보고서")
[void]$finalSb.AppendLine("- [deploy-verification-latest.md](./deploy-verification-latest.md) — 인프라·Smoke·**프론트/R2/CDN(근거)**·SQS·Video·관측·GO/NO-GO 상세")
[void]$finalSb.AppendLine("- [front-pipeline-mapping.latest.md](./front-pipeline-mapping.latest.md) — 프론트 Git 파이프라인 ↔ SSOT 매핑")
[void]$finalSb.AppendLine("- [audit.latest.md](./audit.latest.md) — 리소스·지표 스냅샷")
[void]$finalSb.AppendLine("- [drift.latest.md](./drift.latest.md) — SSOT 대비 drift")
[void]$finalSb.AppendLine("")
Save-V1FinalReportInReports -MarkdownContent $finalSb.ToString()

Write-Host "`n=== 검증 완료 ===" -ForegroundColor Green
Write-Host "  최종 상태: $finalStatus" -ForegroundColor $(if ($finalStatus -eq "PASS") { "Green" } elseif ($finalStatus -eq "WARNING") { "Yellow" } else { "Red" })
Write-Host "  GO/NO-GO: $goNoGo" -ForegroundColor Cyan
Write-Host "  보고서: docs/00-SSOT/v1/reports/deploy-verification-latest.md" -ForegroundColor Cyan
Write-Host "  V1 최종: docs/00-SSOT/v1/reports/V1-FINAL-REPORT.md" -ForegroundColor Cyan
Write-Host "  audit.latest.md, drift.latest.md 갱신됨." -ForegroundColor Gray
if ($findings.Count -gt 0) {
    Write-Host "  발견 사항: $($findings.Count)건" -ForegroundColor Yellow
    $findings | ForEach-Object { Write-Host "    [$($_.Severity)] $($_.Area): $($_.Message)" -ForegroundColor Gray }
}

exit $(if ($finalStatus -eq "FAIL") { 1 } else { 0 })
