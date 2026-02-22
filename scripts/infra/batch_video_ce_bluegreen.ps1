# ==============================================================================
# Video Batch CE Blue-Green Migration
# 기존 CE가 BEST_FIT(또는 null)이면 instanceTypes 업데이트 불가.
# 새 CE 생성(BEST_FIT_PROGRESSIVE) -> Queue 연결 -> 구 CE 삭제.
#
# Usage (batch_video_setup_full.ps1와 동일한 파라미터):
#   .\scripts\infra\batch_video_ce_bluegreen.ps1
#   .\scripts\infra\batch_video_ce_bluegreen.ps1 -VpcId "vpc-xxx" -SubnetIds @("subnet-a","subnet-b") -SecurityGroupId "sg-xxx"
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$EcrRepoUri = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest",
    [string]$VpcId = "",
    [string[]]$SubnetIds = @(),
    [string]$SecurityGroupId = "",
    [string]$OldCeName = "academy-video-batch-ce",
    [string]$NewCeName = "academy-video-batch-ce-v2",
    [string]$JobQueueName = "academy-video-batch-queue"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

function Invoke-AwsText {
    param([string[]]$Arguments)
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & aws @Arguments 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
    } catch { return $null }
    finally { $ErrorActionPreference = $prevErr }
}

function Discover-VpcAndNetwork {
    Write-Host "[Discover] VPC/Subnet/SG 자동 탐색..." -ForegroundColor Cyan
    $vpcId = Invoke-AwsText @("ec2", "describe-vpcs", "--filters", "Name=isDefault,Values=true", "--query", "Vpcs[0].VpcId", "--output", "text", "--region", $Region)
    if (-not $vpcId -or $vpcId -eq "None") {
        $vpcId = Invoke-AwsText @("ec2", "describe-vpcs", "--query", "Vpcs[0].VpcId", "--output", "text", "--region", $Region)
    }
    if (-not $vpcId -or $vpcId -eq "None") { return $null }
    $subnets = (Invoke-AwsText @("ec2", "describe-subnets", "--filters", "Name=vpc-id,Values=$vpcId", "--query", "Subnets[*].SubnetId", "--output", "text", "--region", $Region)) -split "\s+"
    $subnets = $subnets | Where-Object { $_ }
    if (-not $subnets) { return $null }
    $sgId = Invoke-AwsText @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$vpcId", "Name=group-name,Values=default", "--query", "SecurityGroups[0].GroupId", "--output", "text", "--region", $Region)
    if (-not $sgId -or $sgId -eq "None") {
        $sgId = Invoke-AwsText @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$vpcId", "--query", "SecurityGroups[0].GroupId", "--output", "text", "--region", $Region)
    }
    if (-not $sgId -or $sgId -eq "None") { return $null }
    Write-Host "  VpcId=$vpcId Subnets=$($subnets -join ',') SecurityGroupId=$sgId" -ForegroundColor Gray
    return @{ VpcId = $vpcId; SubnetIds = @($subnets); SecurityGroupId = $sgId }
}

Write-Host ""
Write-Host "== Video Batch CE Blue-Green Migration ==" -ForegroundColor Cyan
Write-Host "Old CE: $OldCeName -> New CE: $NewCeName (BEST_FIT_PROGRESSIVE + c6g.large/xlarge/2xlarge)" -ForegroundColor Gray

# VPC/Subnet/SG
if (-not $VpcId -or -not $SecurityGroupId -or $SubnetIds.Count -eq 0) {
    $discovered = Discover-VpcAndNetwork
    if (-not $discovered) {
        Write-Host "FAIL: VPC/Subnet/SG 탐색 실패" -ForegroundColor Red
        exit 1
    }
    $VpcId = $discovered.VpcId
    $SubnetIds = $discovered.SubnetIds
    $SecurityGroupId = $discovered.SecurityGroupId
}

# 1) 현재 CE allocationStrategy 확인
Write-Host ""
Write-Host "[1] 현재 CE 확인" -ForegroundColor Cyan
$ce = ExecJson "aws batch describe-compute-environments --compute-environments $OldCeName --region $Region --output json 2>&1"
$ceObj = $ce.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $OldCeName }
if (-not $ceObj) {
    Write-Host "  CE $OldCeName 없음. batch_video_setup.ps1 먼저 실행." -ForegroundColor Yellow
    exit 1
}
$alloc = $ceObj.computeResources.allocationStrategy
if ($alloc -eq "BEST_FIT_PROGRESSIVE" -or $alloc -eq "SPOT_CAPACITY_OPTIMIZED") {
    Write-Host "  CE가 이미 $alloc. update-compute-environment 사용 가능. batch_video_setup.ps1 실행." -ForegroundColor Green
    exit 0
}
Write-Host "  allocationStrategy=$alloc (또는 null). Blue-Green 필요." -ForegroundColor Yellow

# 2) IAM ARN
Write-Host ""
Write-Host "[2] IAM ARN 조회" -ForegroundColor Cyan
$serviceRoleArn = (ExecJson "aws iam get-role --role-name academy-batch-service-role --output json 2>&1").Role.Arn
$instanceProfileArn = (ExecJson "aws iam get-instance-profile --instance-profile-name academy-batch-ecs-instance-profile --output json 2>&1").InstanceProfile.Arn
if (-not $serviceRoleArn -or -not $instanceProfileArn) {
    Write-Host "FAIL: IAM Role/InstanceProfile 없음. batch_video_setup.ps1 먼저 실행." -ForegroundColor Red
    exit 1
}

# 3) 새 CE 생성 (BEST_FIT_PROGRESSIVE)
Write-Host ""
Write-Host "[3] 새 CE 생성: $NewCeName" -ForegroundColor Cyan
$subnetArr = ($SubnetIds | ForEach-Object { "`"$_`"" }) -join ","
$ceNewJson = @"
{"computeEnvironmentName":"$NewCeName","type":"MANAGED","state":"ENABLED","serviceRole":"$serviceRoleArn","computeResources":{"type":"EC2","allocationStrategy":"BEST_FIT_PROGRESSIVE","minvCpus":0,"maxvCpus":32,"desiredvCpus":0,"instanceTypes":["c6g.large","c6g.xlarge","c6g.2xlarge"],"subnets":[$subnetArr],"securityGroupIds":["$SecurityGroupId"],"instanceRole":"$instanceProfileArn"}}
"@
$ceFile = Join-Path $RepoRoot "batch_ce_new_temp.json"
[System.IO.File]::WriteAllText($ceFile, $ceNewJson, (New-Object System.Text.UTF8Encoding $false))
$ceUri = "file://" + (Resolve-Path -LiteralPath $ceFile).Path.Replace('\', '/')
aws batch create-compute-environment --cli-input-json $ceUri --region $Region
Remove-Item $ceFile -Force -ErrorAction SilentlyContinue
Write-Host "  생성 요청 완료" -ForegroundColor Gray

# 4) 새 CE VALID 대기
Write-Host "  새 CE VALID 대기 (최대 5분)..." -ForegroundColor Gray
$wait = 0
while ($wait -lt 300) {
    Start-Sleep -Seconds 15
    $wait += 15
    $ce2 = ExecJson "aws batch describe-compute-environments --compute-environments $NewCeName --region $Region --output json 2>&1"
    $obj = $ce2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $NewCeName }
    if (-not $obj) { continue }
    $st = $obj.status
    Write-Host "    status=$st ($wait s)" -ForegroundColor Gray
    if ($st -eq "VALID") {
        Write-Host "  OK: 새 CE VALID" -ForegroundColor Green
        break
    }
    if ($st -eq "INVALID") {
        Write-Host "FAIL: 새 CE INVALID. $($obj.statusReason)" -ForegroundColor Red
        exit 1
    }
}
if ($st -ne "VALID") {
    Write-Host "FAIL: 새 CE VALID 대기 시간 초과" -ForegroundColor Red
    exit 1
}

# 5) Job Queue computeEnvironmentOrder 업데이트
Write-Host ""
Write-Host "[5] Job Queue 업데이트: $JobQueueName -> $NewCeName" -ForegroundColor Cyan
$orderJson = "[{`"order`":1,`"computeEnvironment`":`"$NewCeName`"}]"
aws batch update-job-queue --job-queue $JobQueueName --compute-environment-order $orderJson --region $Region
Write-Host "  OK: Queue 연결 변경" -ForegroundColor Green

# 6) 구 CE 비활성화
Write-Host ""
Write-Host "[6] 구 CE 비활성화: $OldCeName" -ForegroundColor Cyan
aws batch update-compute-environment --compute-environment $OldCeName --state DISABLED --region $Region
Write-Host "  DISABLED 요청 완료" -ForegroundColor Gray

# 7) 구 CE 삭제 대기 (DISABLED 후 인스턴스 드레이닝)
Write-Host "  삭제 전 대기 (인스턴스 드레이닝, 2분)..." -ForegroundColor Gray
Start-Sleep -Seconds 120
aws batch delete-compute-environment --compute-environment $OldCeName --region $Region
Write-Host "  삭제 요청 완료" -ForegroundColor Gray

# 8) (선택) 새 CE 이름을 academy-video-batch-ce로 유지하려면 CE를 삭제 후 재생성 필요.
#    현재는 academy-video-batch-ce-v2 유지. 스크립트 기본값에 영향 없음.

Write-Host ""
Write-Host "DONE. Blue-Green 완료." -ForegroundColor Green
Write-Host "  새 CE: $NewCeName (BEST_FIT_PROGRESSIVE, c6g.large/xlarge/2xlarge)" -ForegroundColor Gray
Write-Host "  다음: .\scripts\infra\batch_video_verify_and_register.ps1 -Region $Region -EcrRepoUri $EcrRepoUri" -ForegroundColor Cyan
exit 0
