# Academy 운영 상태 점검 (One-Click Ops Healthcheck)
# 사용법: pwsh -File scripts/v1/run-with-env.ps1 -- pwsh -File scripts/v1/run-ops-healthcheck.ps1
# 또는:  pwsh -File scripts/v1/run-ops-healthcheck.ps1  (AWS 자격증명이 환경변수에 이미 있을 때)
#
# ⚠ 이 스크립트는 읽기 전용입니다. 아무것도 수정하지 않습니다.
# ⚠ 필요: aws CLI, curl, gh (GitHub CLI)

$ErrorActionPreference = "Continue"
$OutputEncoding = [System.Text.Encoding]::UTF8

# ── 설정 ──────────────────────────────────────────────────────
$REGION = "ap-northeast-2"
$API_DOMAIN = "https://api.hakwonplus.com"

# ASG 이름
$ASG_API = "academy-v1-api-asg"
$ASG_MESSAGING = "academy-v1-messaging-worker-asg"
$ASG_AI = "academy-v1-ai-worker-asg"

# SQS 큐 이름
$SQS_QUEUES = @(
    @{ Name = "academy-v1-messaging-queue"; Label = "Messaging" },
    @{ Name = "academy-v1-messaging-queue-dlq"; Label = "Messaging DLQ" },
    @{ Name = "academy-v1-ai-queue"; Label = "AI" },
    @{ Name = "academy-v1-ai-queue-dlq"; Label = "AI DLQ" }
)

# ECR 리포지토리
$ECR_REPOS = @("academy-base", "academy-api", "academy-ai-worker-cpu", "academy-messaging-worker", "academy-video-worker")

# RDS
$RDS_IDENTIFIER = "academy-db"

# Redis (ElastiCache)
$REDIS_REPLICATION_GROUP = "academy-v1-redis"

# GitHub repo
$GH_REPO = "guswls3028-art/academy-backend"

# Budget
$BUDGET_NAME = "academy-monthly-infra"
$AWS_ACCOUNT_ID = "809466760795"

# ── 상태 추적 ─────────────────────────────────────────────────
$script:issues = [System.Collections.ArrayList]::new()
$script:warnings = [System.Collections.ArrayList]::new()

function Add-Issue($msg) { [void]$script:issues.Add($msg) }
function Add-Warning($msg) { [void]$script:warnings.Add($msg) }

function Write-Check($icon, $msg) {
    Write-Host "  $icon $msg"
}

# ── 헤더 ──────────────────────────────────────────────────────
$now = (Get-Date).ToUniversalTime().AddHours(9).ToString("yyyy-MM-dd HH:mm")
Write-Host ""
Write-Host ([char]0x2554 + ([string][char]0x2550) * 42 + [char]0x2557)
Write-Host ([string][char]0x2551 + "  Academy 운영 상태 점검                  " + [char]0x2551)
Write-Host ([string][char]0x2551 + "  $now KST               " + [char]0x2551)
Write-Host ([char]0x255A + ([string][char]0x2550) * 42 + [char]0x255D)
Write-Host ""

# ══════════════════════════════════════════════════════════════
# [1/10] API 상태
# ══════════════════════════════════════════════════════════════
Write-Host "[1/10] API 상태"

try {
    $healthzCode = $null
    try {
        $resp = Invoke-WebRequest -Uri "$API_DOMAIN/healthz" -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        $healthzCode = $resp.StatusCode
    } catch {
        if ($_.Exception.Response) {
            $healthzCode = [int]$_.Exception.Response.StatusCode
        }
    }
    if ($healthzCode -eq 200) {
        Write-Check "✅" "/healthz: 200 (서버 살아있음)"
    } else {
        Write-Check "❌" "/healthz: $healthzCode (서버 응답 이상)"
        Add-Issue "/healthz 응답 코드 $healthzCode — 서버 상태를 확인하세요"
    }
} catch {
    Write-Check "❌" "/healthz: 연결 실패 (서버 다운 가능)"
    Add-Issue "/healthz 연결 실패 — 서버가 다운되었을 수 있습니다"
}

try {
    $healthCode = $null
    try {
        $resp = Invoke-WebRequest -Uri "$API_DOMAIN/health" -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        $healthCode = $resp.StatusCode
    } catch {
        if ($_.Exception.Response) {
            $healthCode = [int]$_.Exception.Response.StatusCode
        }
    }
    if ($healthCode -eq 200) {
        Write-Check "✅" "/health:  200 (DB 연결 정상)"
    } else {
        Write-Check "❌" "/health:  $healthCode (DB 연결 문제 가능)"
        Add-Issue "/health 응답 코드 $healthCode — DB 연결을 확인하세요"
    }
} catch {
    Write-Check "❌" "/health:  연결 실패"
    Add-Issue "/health 연결 실패 — DB 연결 문제일 수 있습니다"
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [2/10] ASG (서버 대수)
# ══════════════════════════════════════════════════════════════
Write-Host "[2/10] ASG (서버 대수)"

function Check-ASG($asgName, $label) {
    try {
        $asgJson = aws autoscaling describe-auto-scaling-groups `
            --auto-scaling-group-names $asgName `
            --region $REGION --output json 2>&1
        $asg = $asgJson | ConvertFrom-Json

        if (-not $asg.AutoScalingGroups -or $asg.AutoScalingGroups.Count -eq 0) {
            Write-Check "❌" "${label}: ASG를 찾을 수 없음"
            Add-Issue "${label} ASG($asgName)를 찾을 수 없습니다"
            return
        }

        $g = $asg.AutoScalingGroups[0]
        $minSize = $g.MinSize
        $desired = $g.DesiredCapacity
        $maxSize = $g.MaxSize
        $instances = $g.Instances

        $healthyCount = 0
        $unhealthyList = @()
        foreach ($inst in $instances) {
            if ($inst.HealthStatus -eq "Healthy" -and $inst.LifecycleState -eq "InService") {
                $healthyCount++
            } else {
                $unhealthyList += "$($inst.InstanceId)($($inst.HealthStatus)/$($inst.LifecycleState))"
            }
        }

        $sizeStr = "$minSize/$desired/$maxSize"
        if ($healthyCount -ge $desired -and $unhealthyList.Count -eq 0) {
            Write-Check "✅" "${label}: $sizeStr (인스턴스 ${healthyCount}대 정상)"
        } elseif ($healthyCount -ge $minSize) {
            Write-Check "⚠️" "${label}: $sizeStr (정상 ${healthyCount}대, 비정상: $($unhealthyList -join ', '))"
            Add-Warning "${label} ASG에 비정상 인스턴스 있음"
        } else {
            Write-Check "❌" "${label}: $sizeStr (정상 ${healthyCount}대 — 최소($minSize) 미달!)"
            Add-Issue "${label} ASG 정상 인스턴스가 최소 요구($minSize)보다 적습니다"
        }
    } catch {
        Write-Check "❌" "${label}: 조회 실패 ($($_.Exception.Message))"
        Add-Issue "${label} ASG 조회 실패"
    }
}

Check-ASG $ASG_API "API"
Check-ASG $ASG_MESSAGING "Messaging"
Check-ASG $ASG_AI "AI"

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [3/10] SQS (대기열)
# ══════════════════════════════════════════════════════════════
Write-Host "[3/10] SQS (대기열)"

foreach ($q in $SQS_QUEUES) {
    try {
        $urlJson = aws sqs get-queue-url --queue-name $q.Name --region $REGION --output json 2>&1
        $urlObj = $urlJson | ConvertFrom-Json
        $queueUrl = $urlObj.QueueUrl

        $attrJson = aws sqs get-queue-attributes `
            --queue-url $queueUrl `
            --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible `
            --region $REGION --output json 2>&1
        $attrs = ($attrJson | ConvertFrom-Json).Attributes

        $visible = [int]$attrs.ApproximateNumberOfMessages
        $inFlight = [int]$attrs.ApproximateNumberOfMessagesNotVisible
        $total = $visible + $inFlight

        $isDlq = $q.Name -match "dlq"

        if ($isDlq -and $visible -gt 0) {
            Write-Check "❌" "$($q.Label): 대기 $visible / 처리중 $inFlight (DLQ에 메시지 있음!)"
            Add-Issue "$($q.Label)에 $visible 건의 실패 메시지 — 원인 조사 필요"
        } elseif (-not $isDlq -and $visible -gt 100) {
            Write-Check "⚠️" "$($q.Label): 대기 $visible / 처리중 $inFlight (대기열 쌓임)"
            Add-Warning "$($q.Label) 대기열에 $visible 건 대기 중 — 워커 상태 확인"
        } else {
            Write-Check "✅" "$($q.Label): 대기 $visible / 처리중 $inFlight"
        }
    } catch {
        Write-Check "⚠️" "$($q.Label): 조회 실패 (큐가 없거나 권한 부족)"
        Add-Warning "$($q.Label) SQS 조회 실패"
    }
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [4/10] 최근 배포 (GitHub Actions)
# ══════════════════════════════════════════════════════════════
Write-Host "[4/10] 최근 배포 (GitHub Actions)"

try {
    $ghRuns = gh run list --repo $GH_REPO --limit 3 --json status,conclusion,name,createdAt,headBranch,event 2>&1
    $runs = $ghRuns | ConvertFrom-Json

    if ($runs -and $runs.Count -gt 0) {
        foreach ($run in $runs) {
            $runName = $run.name
            if ($runName.Length -gt 30) { $runName = $runName.Substring(0, 27) + "..." }
            $branch = $run.headBranch
            $created = $run.createdAt
            if ($created -and $created.Length -gt 16) { $created = $created.Substring(0, 16).Replace("T", " ") }
            $status = $run.status
            $conclusion = $run.conclusion

            if ($conclusion -eq "success") {
                Write-Check "✅" "$runName ($branch) $created — 성공"
            } elseif ($conclusion -eq "failure") {
                Write-Check "❌" "$runName ($branch) $created — 실패"
                Add-Warning "최근 GitHub Actions 실행 실패: $runName"
            } elseif ($status -eq "in_progress") {
                Write-Check "⏳" "$runName ($branch) $created — 진행 중"
            } else {
                Write-Check "⚠️" "$runName ($branch) $created — $status/$conclusion"
            }
        }
    } else {
        Write-Check "⚠️" "최근 실행 내역 없음"
    }
} catch {
    Write-Check "⚠️" "GitHub CLI(gh) 조회 실패 — gh auth login 필요할 수 있음"
    Add-Warning "GitHub Actions 조회 실패"
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [5/10] RDS (데이터베이스)
# ══════════════════════════════════════════════════════════════
Write-Host "[5/10] RDS (데이터베이스)"

try {
    $rdsJson = aws rds describe-db-instances `
        --db-instance-identifier $RDS_IDENTIFIER `
        --region $REGION --output json 2>&1
    $rds = ($rdsJson | ConvertFrom-Json).DBInstances[0]

    $rdsStatus = $rds.DBInstanceStatus
    $rdsClass = $rds.DBInstanceClass
    $rdsStorage = $rds.AllocatedStorage
    $rdsEngine = "$($rds.Engine) $($rds.EngineVersion)"

    if ($rdsStatus -eq "available") {
        Write-Check "✅" "상태: $rdsStatus ($rdsClass, $rdsEngine)"
    } else {
        Write-Check "❌" "상태: $rdsStatus — 비정상!"
        Add-Issue "RDS 상태가 '$rdsStatus' — 즉시 확인 필요"
    }

    # FreeStorageSpace (CloudWatch, 최근 5분)
    try {
        $endTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        $startTime = (Get-Date).ToUniversalTime().AddMinutes(-10).ToString("yyyy-MM-ddTHH:mm:ssZ")

        $freeStorageJson = aws cloudwatch get-metric-statistics `
            --namespace AWS/RDS `
            --metric-name FreeStorageSpace `
            --dimensions "Name=DBInstanceIdentifier,Value=$RDS_IDENTIFIER" `
            --start-time $startTime --end-time $endTime `
            --period 300 --statistics Average `
            --region $REGION --output json 2>&1
        $freeStorageData = ($freeStorageJson | ConvertFrom-Json).Datapoints

        if ($freeStorageData -and $freeStorageData.Count -gt 0) {
            $latestPoint = $freeStorageData | Sort-Object Timestamp -Descending | Select-Object -First 1
            $freeGB = [math]::Round($latestPoint.Average / 1073741824, 1)
            $usedGB = [math]::Round($rdsStorage - $freeGB, 1)

            if ($freeGB -lt 2) {
                Write-Check "❌" "저장공간: ${usedGB}GB 사용 / ${rdsStorage}GB 전체 (남은 공간 ${freeGB}GB — 위험!)"
                Add-Issue "RDS 저장공간 부족 (${freeGB}GB 남음) — 즉시 확장 필요"
            } elseif ($freeGB -lt 5) {
                Write-Check "⚠️" "저장공간: ${usedGB}GB 사용 / ${rdsStorage}GB 전체 (남은 공간 ${freeGB}GB)"
                Add-Warning "RDS 저장공간 여유 부족 (${freeGB}GB)"
            } else {
                Write-Check "✅" "저장공간: ${usedGB}GB 사용 / ${rdsStorage}GB 전체 (남은 공간 ${freeGB}GB)"
            }
        } else {
            Write-Check "⚠️" "저장공간: CloudWatch 데이터 없음 (할당: ${rdsStorage}GB)"
        }
    } catch {
        Write-Check "⚠️" "저장공간: 조회 실패 (할당: ${rdsStorage}GB)"
    }

    # DatabaseConnections (CloudWatch)
    try {
        $connJson = aws cloudwatch get-metric-statistics `
            --namespace AWS/RDS `
            --metric-name DatabaseConnections `
            --dimensions "Name=DBInstanceIdentifier,Value=$RDS_IDENTIFIER" `
            --start-time $startTime --end-time $endTime `
            --period 300 --statistics Average `
            --region $REGION --output json 2>&1
        $connData = ($connJson | ConvertFrom-Json).Datapoints

        if ($connData -and $connData.Count -gt 0) {
            $latestConn = $connData | Sort-Object Timestamp -Descending | Select-Object -First 1
            $connCount = [math]::Round($latestConn.Average)
            # t4g.medium max_connections ~= 405
            if ($connCount -gt 320) {
                Write-Check "❌" "연결 수: ${connCount}개 (한계에 가까움!)"
                Add-Issue "RDS 연결 수 $connCount — 최대치(~405)에 근접"
            } elseif ($connCount -gt 200) {
                Write-Check "⚠️" "연결 수: ${connCount}개"
                Add-Warning "RDS 연결 수가 높음 ($connCount)"
            } else {
                Write-Check "✅" "연결 수: ${connCount}개"
            }
        } else {
            Write-Check "⚠️" "연결 수: CloudWatch 데이터 없음"
        }
    } catch {
        Write-Check "⚠️" "연결 수: 조회 실패"
    }
} catch {
    Write-Check "❌" "RDS 조회 실패 ($($_.Exception.Message))"
    Add-Issue "RDS 조회 실패"
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [6/10] Redis (캐시)
# ══════════════════════════════════════════════════════════════
Write-Host "[6/10] Redis (캐시)"

try {
    $redisJson = aws elasticache describe-replication-groups `
        --replication-group-id $REDIS_REPLICATION_GROUP `
        --region $REGION --output json 2>&1
    $redis = ($redisJson | ConvertFrom-Json).ReplicationGroups[0]

    $redisStatus = $redis.Status
    $nodeCount = 0
    if ($redis.MemberClusters) { $nodeCount = $redis.MemberClusters.Count }

    if ($redisStatus -eq "available") {
        Write-Check "✅" "상태: $redisStatus (노드 ${nodeCount}개)"
    } else {
        Write-Check "❌" "상태: $redisStatus — 비정상!"
        Add-Issue "Redis 상태가 '$redisStatus' — 메시징 중단 가능"
    }

    # Redis memory usage (CloudWatch)
    try {
        $endTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        $startTime = (Get-Date).ToUniversalTime().AddMinutes(-10).ToString("yyyy-MM-ddTHH:mm:ssZ")

        # Get the first member cluster ID for CloudWatch metrics
        if ($redis.MemberClusters -and $redis.MemberClusters.Count -gt 0) {
            $cacheClusterId = $redis.MemberClusters[0]

            $memJson = aws cloudwatch get-metric-statistics `
                --namespace AWS/ElastiCache `
                --metric-name DatabaseMemoryUsagePercentage `
                --dimensions "Name=CacheClusterId,Value=$cacheClusterId" `
                --start-time $startTime --end-time $endTime `
                --period 300 --statistics Average `
                --region $REGION --output json 2>&1
            $memData = ($memJson | ConvertFrom-Json).Datapoints

            if ($memData -and $memData.Count -gt 0) {
                $latestMem = $memData | Sort-Object Timestamp -Descending | Select-Object -First 1
                $memPct = [math]::Round($latestMem.Average, 1)

                if ($memPct -gt 80) {
                    Write-Check "⚠️" "메모리 사용률: ${memPct}% (높음)"
                    Add-Warning "Redis 메모리 사용률 ${memPct}%"
                } else {
                    Write-Check "✅" "메모리 사용률: ${memPct}%"
                }
            } else {
                Write-Check "⚠️" "메모리: CloudWatch 데이터 없음"
            }
        }
    } catch {
        Write-Check "⚠️" "메모리 사용률: 조회 실패"
    }
} catch {
    Write-Check "❌" "Redis 조회 실패 ($($_.Exception.Message))"
    Add-Issue "Redis 조회 실패"
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [7/10] ECR (컨테이너 이미지)
# ══════════════════════════════════════════════════════════════
Write-Host "[7/10] ECR (컨테이너 이미지)"

foreach ($repo in $ECR_REPOS) {
    try {
        $imgJson = aws ecr describe-images `
            --repository-name $repo `
            --region $REGION `
            --query "length(imageDetails)" `
            --output text 2>&1
        $imgCount = [int]$imgJson

        if ($imgCount -gt 100) {
            Write-Check "⚠️" "${repo}: ${imgCount}개 이미지 (정리 필요)"
            Add-Warning "ECR $repo에 이미지 ${imgCount}개 — 100개 초과"
        } else {
            Write-Check "✅" "${repo}: ${imgCount}개 이미지"
        }
    } catch {
        Write-Check "⚠️" "${repo}: 조회 실패"
    }
}

# Lifecycle policy evaluation check (sample first repo)
try {
    $lcJson = aws ecr get-lifecycle-policy `
        --repository-name $ECR_REPOS[0] `
        --region $REGION --output json 2>&1
    $lc = $lcJson | ConvertFrom-Json
    $lastEval = $lc.lastEvaluatedAt

    if ($lastEval -and $lastEval -notmatch "1970-01-01") {
        Write-Check "✅" "Lifecycle 마지막 평가: $lastEval"
    } else {
        Write-Check "⚠️" "Lifecycle 정책이 아직 평가되지 않음 (수동 정리 필요할 수 있음)"
        Add-Warning "ECR Lifecycle 정책이 평가되지 않음 — 2~3일 대기 후 재확인"
    }
} catch {
    Write-Check "⚠️" "Lifecycle 정책 조회 실패 (정책 미설정 가능)"
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [8/10] CloudWatch 알람
# ══════════════════════════════════════════════════════════════
Write-Host "[8/10] CloudWatch 알람"

try {
    $alarmsJson = aws cloudwatch describe-alarms `
        --state-value ALARM `
        --region $REGION --output json 2>&1
    $alarms = ($alarmsJson | ConvertFrom-Json).MetricAlarms

    if ($alarms -and $alarms.Count -gt 0) {
        Write-Check "❌" "$($alarms.Count)개 알람이 ALARM 상태!"
        foreach ($alarm in $alarms) {
            Write-Check "  🔴" "$($alarm.AlarmName): $($alarm.MetricName) $($alarm.ComparisonOperator) $($alarm.Threshold)"
            Add-Issue "CloudWatch 알람 발생: $($alarm.AlarmName)"
        }
    } else {
        Write-Check "✅" "모든 알람 정상 (ALARM 상태 없음)"
    }

    # Also check INSUFFICIENT_DATA (may indicate misconfigured alarms)
    $insuffJson = aws cloudwatch describe-alarms `
        --state-value INSUFFICIENT_DATA `
        --region $REGION --output json 2>&1
    $insuffAlarms = ($insuffJson | ConvertFrom-Json).MetricAlarms

    if ($insuffAlarms -and $insuffAlarms.Count -gt 0) {
        Write-Check "⚠️" "$($insuffAlarms.Count)개 알람 데이터 부족 (INSUFFICIENT_DATA)"
        Add-Warning "$($insuffAlarms.Count)개 CloudWatch 알람 데이터 부족"
    }
} catch {
    Write-Check "⚠️" "CloudWatch 알람 조회 실패"
    Add-Warning "CloudWatch 알람 조회 실패"
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [9/10] 비용 (AWS Budget)
# ══════════════════════════════════════════════════════════════
Write-Host "[9/10] 비용 (AWS Budget)"

try {
    $budgetJson = aws budgets describe-budget `
        --account-id $AWS_ACCOUNT_ID `
        --budget-name $BUDGET_NAME `
        --region us-east-1 --output json 2>&1
    $budget = ($budgetJson | ConvertFrom-Json).Budget

    if ($budget) {
        $limitAmount = $budget.BudgetLimit.Amount
        $limitUnit = $budget.BudgetLimit.Unit
        $actualAmount = $budget.CalculatedSpend.ActualSpend.Amount
        $forecastAmount = $budget.CalculatedSpend.ForecastedSpend.Amount

        $actualNum = [double]$actualAmount
        $limitNum = [double]$limitAmount
        $pct = if ($limitNum -gt 0) { [math]::Round(($actualNum / $limitNum) * 100, 1) } else { 0 }

        if ($forecastAmount) {
            $forecastNum = [double]$forecastAmount
            $forecastStr = "`${0:N0}" -f $forecastNum
        }

        if ($actualNum -gt $limitNum) {
            Write-Check "❌" "이번 달: `$$([math]::Round($actualNum, 0)) / `$$([math]::Round($limitNum, 0)) ($pct%) — 예산 초과!"
            Add-Issue "AWS 비용이 예산(`$$([math]::Round($limitNum, 0)))을 초과했습니다"
        } elseif ($pct -gt 80) {
            Write-Check "⚠️" "이번 달: `$$([math]::Round($actualNum, 0)) / `$$([math]::Round($limitNum, 0)) ($pct%)"
            Add-Warning "AWS 비용이 예산의 ${pct}%에 도달"
        } else {
            Write-Check "✅" "이번 달: `$$([math]::Round($actualNum, 0)) / `$$([math]::Round($limitNum, 0)) ($pct%)"
        }

        if ($forecastAmount) {
            $forecastNum = [double]$forecastAmount
            Write-Check "  📊" "예상 월말: `$$([math]::Round($forecastNum, 0))"
        }
    } else {
        Write-Check "⚠️" "Budget '$BUDGET_NAME' 정보를 가져올 수 없음"
    }
} catch {
    Write-Check "⚠️" "Budget 조회 실패 (budget 미설정 또는 권한 부족)"
    Add-Warning "AWS Budget 조회 실패"
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# [10/10] 비디오 처리 상태
# ══════════════════════════════════════════════════════════════
Write-Host "[10/10] 비디오 처리 상태"

# Check video-related CloudWatch custom metrics or Batch jobs
try {
    # Check AWS Batch job queue for FAILED jobs in last 24h
    $batchQueuesExist = $false

    $batchJson = aws batch describe-job-queues `
        --region $REGION --output json 2>&1
    $batchQueues = ($batchJson | ConvertFrom-Json).jobQueues

    if ($batchQueues -and $batchQueues.Count -gt 0) {
        $batchQueuesExist = $true
        foreach ($bq in $batchQueues) {
            $qName = $bq.jobQueueName
            $qState = $bq.state
            $qStatus = $bq.status

            if ($qState -eq "ENABLED" -and $qStatus -eq "VALID") {
                Write-Check "✅" "Batch 큐 ${qName}: $qState/$qStatus"
            } else {
                Write-Check "⚠️" "Batch 큐 ${qName}: $qState/$qStatus"
                Add-Warning "Batch 큐 $qName 상태 이상: $qState/$qStatus"
            }
        }
    }

    # Check for FAILED batch jobs in last 24h
    if ($batchQueuesExist) {
        $failedJson = aws batch list-jobs `
            --job-queue "academy-v1-video-batch-queue" `
            --job-status FAILED `
            --region $REGION --output json 2>&1
        $failedJobs = ($failedJson | ConvertFrom-Json).jobSummaryList

        if ($failedJobs -and $failedJobs.Count -gt 0) {
            Write-Check "⚠️" "최근 실패한 Batch 작업: $($failedJobs.Count)건"
            Add-Warning "실패한 비디오 Batch 작업 $($failedJobs.Count)건"
        } else {
            Write-Check "✅" "최근 실패한 Batch 작업: 없음"
        }

        # Check for RUNNING/RUNNABLE jobs (shows current processing load)
        $runningJson = aws batch list-jobs `
            --job-queue "academy-v1-video-batch-queue" `
            --job-status RUNNING `
            --region $REGION --output json 2>&1
        $runningJobs = ($runningJson | ConvertFrom-Json).jobSummaryList
        $runningCount = if ($runningJobs) { $runningJobs.Count } else { 0 }

        $runnableJson = aws batch list-jobs `
            --job-queue "academy-v1-video-batch-queue" `
            --job-status RUNNABLE `
            --region $REGION --output json 2>&1
        $runnableJobs = ($runnableJson | ConvertFrom-Json).jobSummaryList
        $runnableCount = if ($runnableJobs) { $runnableJobs.Count } else { 0 }

        if ($runningCount -gt 0 -or $runnableCount -gt 0) {
            Write-Check "⏳" "진행 중: ${runningCount}건 / 대기: ${runnableCount}건"
        } else {
            Write-Check "✅" "현재 처리 중인 작업 없음"
        }
    } else {
        Write-Check "⚠️" "Batch 작업 큐를 찾을 수 없음"
    }
} catch {
    Write-Check "⚠️" "비디오 처리 상태 조회 실패"
    Add-Warning "비디오 Batch 상태 조회 실패"
}

Write-Host ""

# ══════════════════════════════════════════════════════════════
# 최종 판정
# ══════════════════════════════════════════════════════════════
Write-Host ([string]([char]0x2550) * 44)

if ($script:issues.Count -gt 0) {
    Write-Host "  최종 판정: ❌ 즉시 확인 필요" -ForegroundColor Red
    Write-Host ""
    Write-Host "  발견된 문제:" -ForegroundColor Red
    foreach ($issue in $script:issues) {
        Write-Host "    ❌ $issue" -ForegroundColor Red
    }
    if ($script:warnings.Count -gt 0) {
        Write-Host ""
        Write-Host "  주의 사항:" -ForegroundColor Yellow
        foreach ($w in $script:warnings) {
            Write-Host "    ⚠️ $w" -ForegroundColor Yellow
        }
    }
    Write-Host ""
    Write-Host "  조치: 위 문제를 확인하고 해결하세요." -ForegroundColor Red
    Write-Host "        긴급한 경우 운영자에게 연락하세요." -ForegroundColor Red
} elseif ($script:warnings.Count -gt 0) {
    Write-Host "  최종 판정: ⚠️ 주의 필요" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  주의 사항:" -ForegroundColor Yellow
    foreach ($w in $script:warnings) {
        Write-Host "    ⚠️ $w" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  조치: 당장 문제는 아니지만 확인이 필요합니다." -ForegroundColor Yellow
} else {
    Write-Host "  최종 판정: ✅ 정상" -ForegroundColor Green
    Write-Host ""
    Write-Host "  문제 없음. 안심하고 주무세요." -ForegroundColor Green
}

Write-Host ([string]([char]0x2550) * 44)
Write-Host ""
