# ==============================================================================
# AWS Batch CE: ARM64 ECS Optimized AMI 설정
# imageId가 null이면 Batch가 EC2를 띄우지 못해 RUNNABLE 상태에서 멈춤.
# user-provided role CE는 SLR로 전환 불가 → 새 CE를 SLR+imageId로 생성 후 큐를 옮기는 Blue-Green.
#
# Usage:
#   .\scripts\infra\batch_update_ce_ami.ps1
#   .\scripts\infra\batch_update_ce_ami.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$ComputeEnv = "academy-video-batch-ce-v3",
    [string]$NewCeName = "academy-video-batch-ce-v4",
    [string]$JobQueueName = "academy-video-batch-queue",
    [switch]$Verify
)

$ErrorActionPreference = "Stop"

$SsmParam = "/aws/service/ecs/optimized-ami/amazon-linux-2/arm64/recommended/image_id"

Write-Host ""
Write-Host "[1] Fetching latest ARM64 ECS Optimized AMI (AL2)..." -ForegroundColor Cyan
$amiId = aws ssm get-parameter `
    --name $SsmParam `
    --region $Region `
    --query "Parameter.Value" `
    --output text 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "  FAIL: $amiId" -ForegroundColor Red
    exit 1
}
$amiId = $amiId.Trim()
if (-not $amiId -or $amiId -eq "None") {
    Write-Host "  FAIL: AMI ID empty or None." -ForegroundColor Red
    exit 1
}
Write-Host "  image_id = $amiId" -ForegroundColor Green

Write-Host ""
Write-Host "[2] Current compute environment: $ComputeEnv" -ForegroundColor Cyan
$ceJson = aws batch describe-compute-environments --compute-environments $ComputeEnv --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  FAIL: $ceJson" -ForegroundColor Red
    exit 1
}
$ce = $ceJson | ConvertFrom-Json
if (-not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
    Write-Host "  FAIL: Compute environment not found." -ForegroundColor Red
    exit 1
}
$ce0 = $ce.computeEnvironments[0]
$res = $ce0.computeResources
$currentImageId = $res.imageId
Write-Host "  current imageId = $(if ($currentImageId) { $currentImageId } else { 'null' })" -ForegroundColor Gray
Write-Host "  instanceTypes = $($res.instanceTypes -join ', ')" -ForegroundColor Gray

if ($currentImageId -eq $amiId) {
    Write-Host ""
    Write-Host "  Already using this AMI. No update needed." -ForegroundColor Green
    if ($Verify) {
        Write-Host "  imageId is non-null. OK." -ForegroundColor Green
    }
    exit 0
}

# 새 CE가 이미 있으면(이전 실행에서 생성만 된 경우) 생성 생략 후 [4][5]만 수행
$existing = aws batch describe-compute-environments --compute-environments $NewCeName --region $Region --output json 2>&1 | ConvertFrom-Json
$newCeAlreadyExists = ($existing.computeEnvironments -and $existing.computeEnvironments.Count -gt 0)

function Wait-CeValid {
    param([string]$CeName)
    $maxWait = 300
    $elapsed = 0
    while ($elapsed -lt $maxWait) {
        Start-Sleep -Seconds 15
        $elapsed += 15
        $ceJson = aws batch describe-compute-environments --compute-environments $CeName --region $Region --output json 2>&1
        $ce2 = $ceJson | ConvertFrom-Json
        if (-not $ce2.computeEnvironments -or $ce2.computeEnvironments.Count -eq 0) { continue }
        $st = $ce2.computeEnvironments[0].status
        Write-Host "    status=$st ($elapsed s)" -ForegroundColor Gray
        if ($st -eq "VALID") { return $true }
        if ($st -eq "INVALID") {
            Write-Host "  FAIL: CE INVALID. $($ce2.computeEnvironments[0].statusReason)" -ForegroundColor Red
            return $false
        }
    }
    Write-Host "  FAIL: VALID wait timeout." -ForegroundColor Red
    return $false
}

# [3] 새 CE 생성(없을 때만): SLR(serviceRole="") + imageId, 나머지는 기존 CE와 동일
if ($newCeAlreadyExists) {
    Write-Host ""
    Write-Host "[3] New CE '$NewCeName' already exists (resuming from previous run). Ensuring VALID..." -ForegroundColor Yellow
    $exSt = $existing.computeEnvironments[0].status
    if ($exSt -ne "VALID") {
        if (-not (Wait-CeValid -CeName $NewCeName)) { exit 1 }
    }
    Write-Host "  OK: $NewCeName is VALID." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "[3] Creating new CE: $NewCeName (SLR + imageId)..." -ForegroundColor Yellow
    $subnets = @($res.subnets)
    $securityGroupIds = @($res.securityGroupIds)
    $instanceTypes = @($res.instanceTypes)
    $createPayload = @{
        computeEnvironmentName = $NewCeName
        type                   = "MANAGED"
        state                  = "ENABLED"
        serviceRole            = ""
        computeResources       = @{
            type                = "EC2"
            allocationStrategy  = $res.allocationStrategy
            minvCpus             = [int]$res.minvCpus
            maxvCpus             = [int]$res.maxvCpus
            desiredvCpus         = [int]$res.desiredvCpus
            instanceTypes        = $instanceTypes
            subnets              = $subnets
            securityGroupIds     = $securityGroupIds
            instanceRole         = $res.instanceRole
            imageId              = $amiId
        }
    }
    $tmpFile = [System.IO.Path]::GetTempFileName()
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tmpFile, ($createPayload | ConvertTo-Json -Depth 6 -Compress), $utf8NoBom)
    $fileUri = "file://" + ($tmpFile -replace '\\', '/')
    try {
        $prevErr = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $createOut = & aws batch create-compute-environment --cli-input-json $fileUri --region $Region 2>&1
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevErr
        if ($exitCode -ne 0) {
            Write-Host "  FAIL: $createOut" -ForegroundColor Red
            exit 1
        }
    } finally {
        Remove-Item -LiteralPath $tmpFile -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  Create requested. Waiting for VALID (max 5 min)..." -ForegroundColor Green
    if (-not (Wait-CeValid -CeName $NewCeName)) { exit 1 }
    Write-Host "  OK: New CE VALID." -ForegroundColor Green
}

# [4] Job Queue를 새 CE로 연결 (JSON 따옴표 보존을 위해 임시 파일 사용)
Write-Host ""
Write-Host "[4] Updating job queue: $JobQueueName -> $NewCeName" -ForegroundColor Yellow
$queueUpdatePayload = @{
    jobQueue                 = $JobQueueName
    computeEnvironmentOrder  = @(@{ order = 1; computeEnvironment = $NewCeName })
} | ConvertTo-Json -Depth 4 -Compress
$qTmpFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($qTmpFile, $queueUpdatePayload, (New-Object System.Text.UTF8Encoding $false))
$qFileUri = "file://" + ($qTmpFile -replace '\\', '/')
try {
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $qOut = & aws batch update-job-queue --cli-input-json $qFileUri --region $Region 2>&1
    $qExit = $LASTEXITCODE
    $ErrorActionPreference = $prevErr
    if ($qExit -ne 0) {
        Write-Host "  FAIL: $qOut" -ForegroundColor Red
        exit 1
    }
} finally {
    Remove-Item -LiteralPath $qTmpFile -Force -ErrorAction SilentlyContinue
}
Write-Host "  OK: Queue linked to $NewCeName." -ForegroundColor Green

# [5] 구 CE 비활성화
Write-Host ""
Write-Host "[5] Disabling old CE: $ComputeEnv" -ForegroundColor Yellow
$ErrorActionPreference = "Continue"
& aws batch update-compute-environment --compute-environment $ComputeEnv --state DISABLED --region $Region 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  WARN: Disable request failed (CE may already be disabled)." -ForegroundColor Yellow
} else {
    Write-Host "  OK: Old CE DISABLED." -ForegroundColor Green
}

if ($Verify) {
    Write-Host ""
    Write-Host "[6] Verifying new CE imageId..." -ForegroundColor Cyan
    $ce3Json = aws batch describe-compute-environments --compute-environments $NewCeName --region $Region --output json 2>&1
    $ce3 = $ce3Json | ConvertFrom-Json
    $newImageId = $ce3.computeEnvironments[0].computeResources.imageId
    Write-Host "  imageId = $(if ($newImageId) { $newImageId } else { 'null' })" -ForegroundColor $(if ($newImageId) { "Green" } else { "Yellow" })
    if ($newImageId) {
        Write-Host "  Batch can launch ARM EC2 instances." -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Done. Active CE is now: $NewCeName (SLR + imageId=$amiId)." -ForegroundColor Green
Write-Host "  To remove old CE after drain: aws batch delete-compute-environment --compute-environment $ComputeEnv --region $Region" -ForegroundColor Gray
Write-Host "  (Wait 2+ min after DISABLED before delete.)" -ForegroundColor Gray
Write-Host ""
