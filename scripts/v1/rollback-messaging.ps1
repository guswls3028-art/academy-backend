<#
.SYNOPSIS
    Messaging Worker 서비스 롤백 스크립트
.DESCRIPTION
    ECR 이미지의 SHA 태그를 :latest로 재태깅하고 ASG Instance Refresh를 실행하여
    Messaging Worker 서비스를 이전 버전으로 롤백합니다.
.PARAMETER Sha
    롤백할 SHA 태그 (예: sha-abcd1234). 생략 시 두 번째 최신 SHA 태그를 자동 선택합니다.
.PARAMETER WhatIf
    실제 실행 없이 어떤 작업이 수행될지만 보여줍니다.
.EXAMPLE
    run-with-env.ps1 -- pwsh -File scripts/v1/rollback-messaging.ps1
    run-with-env.ps1 -- pwsh -File scripts/v1/rollback-messaging.ps1 -Sha sha-abcd1234
    run-with-env.ps1 -- pwsh -File scripts/v1/rollback-messaging.ps1 -WhatIf
#>
param(
    [string]$Sha,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

# ── 서비스 설정 ──────────────────────────────────────────────
$ServiceName     = "Messaging Worker"
$EcrRepo         = "academy-messaging-worker"
$AsgName         = "academy-v1-messaging-worker-asg"
$InstanceWarmup  = 120
$Region          = "ap-northeast-2"

# ── 공통 함수 ────────────────────────────────────────────────
function Write-Step {
    param([string]$Emoji, [string]$Title)
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor DarkGray
    Write-Host "$Emoji $Title" -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor DarkGray
}

function Write-Info  { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor White }
function Write-OK    { param([string]$Msg) Write-Host "  ✅ $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "  ⚠️  $Msg" -ForegroundColor Yellow }
function Write-Fail  { param([string]$Msg) Write-Host "  ❌ $Msg" -ForegroundColor Red }

function Show-MigrationDecisionTree {
    Write-Host ""
    Write-Host "┌──────────────────────────────────────────────────────────┐" -ForegroundColor Yellow
    Write-Host "│  📋 마이그레이션 롤백 결정 트리 (SSOT §9.2)              │" -ForegroundColor Yellow
    Write-Host "├──────────────────────────────────────────────────────────┤" -ForegroundColor Yellow
    Write-Host "│                                                          │" -ForegroundColor Yellow
    Write-Host "│  이번 배포에 DB 마이그레이션이 포함되어 있나요?           │" -ForegroundColor Yellow
    Write-Host "│  │                                                        │" -ForegroundColor Yellow
    Write-Host "│  ├─ 마이그레이션 없음 (코드만 변경)?                      │" -ForegroundColor Yellow
    Write-Host "│  │  └─ ✅ 이미지 롤백만으로 충분합니다                    │" -ForegroundColor Yellow
    Write-Host "│  │                                                        │" -ForegroundColor Yellow
    Write-Host "│  ├─ Additive 마이그레이션 (새 nullable 컬럼/테이블)?      │" -ForegroundColor Yellow
    Write-Host "│  │  └─ ✅ 이미지 롤백만으로 충분합니다                    │" -ForegroundColor Yellow
    Write-Host "│  │     (이전 코드는 새 컬럼을 무시합니다)                 │" -ForegroundColor Yellow
    Write-Host "│  │                                                        │" -ForegroundColor Yellow
    Write-Host "│  ├─ 컬럼 이름변경/타입변경/제약조건 변경?                 │" -ForegroundColor Yellow
    Write-Host "│  │  └─ ⚠️  코드 롤백 전에 마이그레이션을 먼저 되돌려야    │" -ForegroundColor Yellow
    Write-Host "│  │     합니다! 순서가 중요합니다.                         │" -ForegroundColor Yellow
    Write-Host "│  │     $ docker exec <container> python manage.py \       │" -ForegroundColor Yellow
    Write-Host "│  │       migrate <app_name> <이전_마이그레이션_번호>      │" -ForegroundColor Yellow
    Write-Host "│  │                                                        │" -ForegroundColor Yellow
    Write-Host "│  └─ 컬럼/테이블 삭제?                                    │" -ForegroundColor Yellow
    Write-Host "│     └─ ❌ 데이터가 이미 삭제됨. 롤백 불가.               │" -ForegroundColor Yellow
    Write-Host "│        RDS 시점 복구(RPO: 5분)를 사용하세요.              │" -ForegroundColor Yellow
    Write-Host "│                                                          │" -ForegroundColor Yellow
    Write-Host "└──────────────────────────────────────────────────────────┘" -ForegroundColor Yellow
    Write-Host ""
}

# ── Step 1: 현재 상태 확인 ───────────────────────────────────
Write-Step "🔍" "Step 1: 현재 상태 확인 — $ServiceName 서비스"

# 현재 :latest 태그의 digest 조회
Write-Info "현재 :latest 이미지 정보 조회 중..."
$latestInfo = aws ecr describe-images `
    --repository-name $EcrRepo `
    --image-ids imageTag=latest `
    --region $Region `
    --output json 2>$null | ConvertFrom-Json

if ($latestInfo.imageDetails) {
    $latestDigest = $latestInfo.imageDetails[0].imageDigest
    $latestTags = ($latestInfo.imageDetails[0].imageTags | Where-Object { $_ -ne "latest" }) -join ", "
    Write-Info "현재 :latest → digest: $($latestDigest.Substring(0,19))..."
    if ($latestTags) {
        Write-Info "현재 :latest 의 SHA 태그: $latestTags"
    }
} else {
    Write-Fail ":latest 태그를 찾을 수 없습니다. ECR 리포지토리를 확인하세요."
    exit 1
}

# 최근 sha- 태그 목록 조회 (최근 5개)
Write-Info ""
Write-Info "최근 sha- 태그 목록 (최근 5개):"
$allImages = aws ecr describe-images `
    --repository-name $EcrRepo `
    --region $Region `
    --output json 2>$null | ConvertFrom-Json

$shaImages = $allImages.imageDetails | Where-Object {
    $_.imageTags -and ($_.imageTags | Where-Object { $_ -like "sha-*" })
} | Sort-Object imagePushedAt -Descending | Select-Object -First 5

$idx = 0
foreach ($img in $shaImages) {
    $idx++
    $shaTag = $img.imageTags | Where-Object { $_ -like "sha-*" } | Select-Object -First 1
    $hasLatest = if ($img.imageTags -contains "latest") { " ← 현재 운영 중" } else { "" }
    $pushedAt = $img.imagePushedAt
    Write-Info "  ${idx}. $shaTag  (pushed: $pushedAt)$hasLatest"
}

if ($shaImages.Count -lt 2 -and -not $Sha) {
    Write-Fail "sha- 태그가 2개 미만입니다. -Sha 파라미터로 직접 지정해주세요."
    exit 1
}

# ── Step 2: 롤백 대상 결정 ───────────────────────────────────
Write-Step "🎯" "Step 2: 롤백 대상 확인"

if ($Sha) {
    $targetSha = $Sha
    if ($targetSha -notlike "sha-*") {
        $targetSha = "sha-$targetSha"
    }
    Write-Info "사용자 지정 SHA: $targetSha"

    $targetImage = $allImages.imageDetails | Where-Object {
        $_.imageTags -and ($_.imageTags -contains $targetSha)
    }
    if (-not $targetImage) {
        Write-Fail "$targetSha 태그를 찾을 수 없습니다."
        Write-Info "사용 가능한 sha- 태그:"
        foreach ($img in $shaImages) {
            $t = $img.imageTags | Where-Object { $_ -like "sha-*" } | Select-Object -First 1
            Write-Info "  - $t"
        }
        exit 1
    }
} else {
    $nonLatestSha = $shaImages | Where-Object {
        -not ($_.imageTags -contains "latest")
    } | Select-Object -First 1

    if (-not $nonLatestSha) {
        Write-Fail "롤백 대상 SHA를 자동 선택할 수 없습니다. -Sha 파라미터로 직접 지정해주세요."
        exit 1
    }
    $targetSha = $nonLatestSha.imageTags | Where-Object { $_ -like "sha-*" } | Select-Object -First 1
    Write-Info "자동 선택된 롤백 대상: $targetSha (현재 latest 바로 이전 버전)"
}

Write-Host ""
Write-Host "  ┌────────────────────────────────────────────┐" -ForegroundColor Magenta
Write-Host "  │  롤백 요약                                  │" -ForegroundColor Magenta
Write-Host "  │  서비스:    $ServiceName" -ForegroundColor Magenta
Write-Host "  │  ECR 리포:  $EcrRepo" -ForegroundColor Magenta
Write-Host "  │  ASG:       $AsgName" -ForegroundColor Magenta
Write-Host "  │  대상 SHA:  $targetSha" -ForegroundColor Magenta
Write-Host "  │  Warmup:    ${InstanceWarmup}초" -ForegroundColor Magenta
Write-Host "  └────────────────────────────────────────────┘" -ForegroundColor Magenta

if ($WhatIf) {
    Write-Warn "[WhatIf 모드] 여기까지가 실행 미리보기입니다. 실제 변경은 수행되지 않았습니다."
    Write-Host ""
    Write-Warn "⚠️  DB 마이그레이션은 이 스크립트에서 처리하지 않습니다."
    Write-Warn "마이그레이션 롤백이 필요한 경우 아래 결정 트리를 참고하세요."
    Show-MigrationDecisionTree
    exit 0
}

# 사용자 확인
Write-Host ""
Write-Warn "⚠️  DB 마이그레이션은 이 스크립트에서 처리하지 않습니다."
Write-Warn "마이그레이션 롤백이 필요하면 'M'을 입력하세요."
Write-Host ""
$confirm = Read-Host "  $targetSha 로 $ServiceName 를 롤백하시겠습니까? (Y=실행 / M=마이그레이션 안내 / N=취소)"

if ($confirm -eq 'M' -or $confirm -eq 'm') {
    Show-MigrationDecisionTree
    $confirm2 = Read-Host "  마이그레이션 확인 후 롤백을 계속 진행하시겠습니까? (Y/N)"
    if ($confirm2 -ne 'Y' -and $confirm2 -ne 'y') {
        Write-Info "롤백이 취소되었습니다."
        exit 0
    }
} elseif ($confirm -ne 'Y' -and $confirm -ne 'y') {
    Write-Info "롤백이 취소되었습니다."
    exit 0
}

# ── Step 3: manifest 가져오기 ────────────────────────────────
Write-Step "📦" "Step 3: ECR manifest 가져오기"

Write-Info "$targetSha 의 manifest 조회 중..."
$manifest = aws ecr batch-get-image `
    --repository-name $EcrRepo `
    --image-ids imageTag=$targetSha `
    --query 'images[0].imageManifest' `
    --region $Region `
    --output text

if (-not $manifest -or $manifest -eq "None") {
    Write-Fail "manifest를 가져올 수 없습니다. SHA 태그를 확인하세요: $targetSha"
    exit 1
}
Write-OK "manifest 조회 완료 (${manifest.Length} bytes)"

# ── Step 4: latest 재태깅 ───────────────────────────────────
Write-Step "🏷️" "Step 4: :latest 태그를 $targetSha 로 재태깅"

$tempFile = [System.IO.Path]::GetTempFileName()
try {
    $manifest | Set-Content -Path $tempFile -Encoding UTF8 -NoNewline

    aws ecr put-image `
        --repository-name $EcrRepo `
        --image-tag latest `
        --image-manifest file://$tempFile `
        --region $Region `
        --output json 2>$null | Out-Null

    Write-OK ":latest 태그가 $targetSha 를 가리키도록 변경되었습니다"
} catch {
    if ($_.Exception.Message -like "*ImageAlreadyExistsException*") {
        Write-OK ":latest 가 이미 $targetSha 를 가리키고 있습니다 (변경 없음)"
    } else {
        Write-Fail "put-image 실패: $($_.Exception.Message)"
        exit 1
    }
} finally {
    Remove-Item $tempFile -ErrorAction SilentlyContinue
}

# ── Step 5: ASG Instance Refresh ─────────────────────────────
Write-Step "🔄" "Step 5: ASG Instance Refresh 시작"

Write-Info "ASG: $AsgName"
Write-Info "MinHealthyPercentage: 100% (무중단 배포)"
Write-Info "InstanceWarmup: ${InstanceWarmup}초"
Write-Host ""

$prefsJson = @{
    MinHealthyPercentage = 100
    InstanceWarmup       = $InstanceWarmup
} | ConvertTo-Json -Compress

$refreshResult = aws autoscaling start-instance-refresh `
    --auto-scaling-group-name $AsgName `
    --preferences $prefsJson `
    --region $Region `
    --output json | ConvertFrom-Json

$refreshId = $refreshResult.InstanceRefreshId
Write-OK "Instance Refresh 시작됨: $refreshId"

# ── Step 6: 대기 ────────────────────────────────────────────
Write-Step "⏳" "Step 6: Instance Refresh 완료 대기 중"

Write-Info "15초 간격으로 상태를 확인합니다..."
Write-Host ""

$maxWaitSeconds = 600
$elapsed = 0

while ($elapsed -lt $maxWaitSeconds) {
    Start-Sleep -Seconds 15
    $elapsed += 15

    $statusResult = aws autoscaling describe-instance-refreshes `
        --auto-scaling-group-name $AsgName `
        --instance-refresh-ids $refreshId `
        --region $Region `
        --output json | ConvertFrom-Json

    $status = $statusResult.InstanceRefreshes[0].Status
    $pctComplete = $statusResult.InstanceRefreshes[0].PercentageComplete

    $bar = ""
    if ($pctComplete) {
        $filled = [math]::Floor($pctComplete / 5)
        $empty = 20 - $filled
        $bar = ("█" * $filled) + ("░" * $empty)
    }

    Write-Host "`r  [$bar] ${pctComplete}% — 상태: $status (${elapsed}초 경과)" -NoNewline

    if ($status -eq "Successful") {
        Write-Host ""
        Write-OK "Instance Refresh 완료!"
        break
    }
    if ($status -eq "Cancelled" -or $status -eq "Failed" -or $status -eq "RollbackSuccessful") {
        Write-Host ""
        Write-Fail "Instance Refresh 실패: $status"
        Write-Fail "AWS 콘솔에서 ASG를 확인하세요: $AsgName"
        exit 1
    }
}

if ($elapsed -ge $maxWaitSeconds) {
    Write-Warn "10분 경과 — 타임아웃. AWS 콘솔에서 직접 확인하세요."
    Write-Info "Refresh ID: $refreshId"
}

# ── Step 7: 검증 ─────────────────────────────────────────────
Write-Step "✅" "Step 7: 롤백 검증"

# Worker는 HTTP 엔드포인트가 없으므로 ASG 상태만 확인
Write-Info "Messaging Worker는 HTTP 엔드포인트가 없습니다. ASG 인스턴스 상태를 확인합니다."

$asgInfo = aws autoscaling describe-auto-scaling-groups `
    --auto-scaling-group-names $AsgName `
    --region $Region `
    --output json | ConvertFrom-Json

$instances = $asgInfo.AutoScalingGroups[0].Instances
$healthyCount = ($instances | Where-Object { $_.HealthStatus -eq "Healthy" -and $_.LifecycleState -eq "InService" }).Count
$totalCount = $instances.Count

if ($healthyCount -eq $totalCount -and $totalCount -gt 0) {
    Write-OK "ASG 인스턴스: $healthyCount/$totalCount Healthy/InService"
} else {
    Write-Warn "ASG 인스턴스: $healthyCount/$totalCount Healthy/InService"
}

Write-Info ""
Write-Info "추가 확인 권장:"
Write-Info "  - SQS 큐 깊이 확인 (메시지가 정상 처리되는지)"
Write-Info "  - CloudWatch 로그에서 worker 시작 로그 확인"

# ── Step 8: 결과 출력 ────────────────────────────────────────
Write-Step "📋" "Step 8: 롤백 결과"

Write-Host ""
Write-Host "  ┌────────────────────────────────────────────┐" -ForegroundColor Green
Write-Host "  │  🎉 $ServiceName 롤백 완료       │" -ForegroundColor Green
Write-Host "  │                                            │" -ForegroundColor Green
Write-Host "  │  서비스:     $ServiceName" -ForegroundColor Green
Write-Host "  │  롤백 SHA:   $targetSha" -ForegroundColor Green
Write-Host "  │  Refresh ID: $refreshId" -ForegroundColor Green
Write-Host "  │  인스턴스:   $healthyCount/$totalCount Healthy" -ForegroundColor Green
Write-Host "  └────────────────────────────────────────────┘" -ForegroundColor Green
Write-Host ""
Write-Warn "참고: DB 마이그레이션 롤백은 별도로 수행해야 합니다."
Write-Warn "필요 시 이 스크립트를 다시 실행하고 'M'을 선택하여 안내를 확인하세요."
Write-Host ""
