# ==============================================================================
# Video Batch - Full Setup (VPC/Subnet/SG 자동 탐색 + setup + verify)
# 인프라가 없을 때 한 번에 실행. 파라미터 없이 실행 가능.
#
# Usage (파라미터 없음 - 자동 탐색):
#   cd C:\academy
#   .\scripts\infra\batch_video_setup_full.ps1
#
# Usage (값 직접 지정):
#   .\scripts\infra\batch_video_setup_full.ps1 -VpcId "vpc-xxx" -SubnetIds @("subnet-a","subnet-b") -SecurityGroupId "sg-xxx"
#
# Usage (탐색만 - 실제 생성 안 함):
#   .\scripts\infra\batch_video_setup_full.ps1 -DiscoverOnly
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$EcrRepoUri = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest",
    [string]$VpcId = "",
    [string[]]$SubnetIds = @(),
    [string]$SecurityGroupId = "",
    [switch]$DiscoverOnly = $false
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"

function Invoke-AwsText {
    param([string[]]$Arguments)
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & aws @Arguments 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        $text = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
        return $text
    } catch { return $null }
    finally { $ErrorActionPreference = $prevErr }
}

function Discover-VpcAndNetwork {
    Write-Host "[Discover] VPC/Subnet/SG 자동 탐색..." -ForegroundColor Cyan
    $vpcId = Invoke-AwsText @("ec2", "describe-vpcs", "--filters", "Name=isDefault,Values=true", "--query", "Vpcs[0].VpcId", "--output", "text", "--region", $Region)
    if (-not $vpcId -or $vpcId -eq "None") {
        $vpcId = Invoke-AwsText @("ec2", "describe-vpcs", "--query", "Vpcs[0].VpcId", "--output", "text", "--region", $Region)
    }
    if (-not $vpcId -or $vpcId -eq "None") {
        Write-Host "  VPC 없음. -VpcId, -SubnetIds, -SecurityGroupId 를 직접 지정하세요." -ForegroundColor Red
        return $null
    }
    $subnets = (Invoke-AwsText @("ec2", "describe-subnets", "--filters", "Name=vpc-id,Values=$vpcId", "--query", "Subnets[*].SubnetId", "--output", "text", "--region", $Region)) -split "\s+"
    $subnets = $subnets | Where-Object { $_ }
    if (-not $subnets -or $subnets.Count -eq 0) {
        Write-Host "  Subnet 없음." -ForegroundColor Red
        return $null
    }
    $sgId = Invoke-AwsText @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$vpcId", "Name=group-name,Values=default", "--query", "SecurityGroups[0].GroupId", "--output", "text", "--region", $Region)
    if (-not $sgId -or $sgId -eq "None") {
        $sgId = (Invoke-AwsText @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$vpcId", "--query", "SecurityGroups[0].GroupId", "--output", "text", "--region", $Region))
    }
    if (-not $sgId -or $sgId -eq "None") {
        Write-Host "  Security Group 없음." -ForegroundColor Red
        return $null
    }
    Write-Host "  VpcId=$vpcId" -ForegroundColor Gray
    Write-Host "  Subnets=$($subnets -join ', ')" -ForegroundColor Gray
    Write-Host "  SecurityGroupId=$sgId" -ForegroundColor Gray
    return @{ VpcId = $vpcId; SubnetIds = @($subnets); SecurityGroupId = $sgId }
}

Write-Host ""
Write-Host "== Video Batch Full Setup ==" -ForegroundColor Cyan
Write-Host "Region=$Region EcrRepoUri=$EcrRepoUri" -ForegroundColor Gray

# AWS 인증 확인
$account = Invoke-AwsText @("sts", "get-caller-identity", "--query", "Account", "--output", "text", "--region", $Region)
if (-not $account) {
    Write-Host "FAIL: AWS 인증 실패. aws configure 또는 환경변수 확인." -ForegroundColor Red
    exit 1
}
Write-Host "Account=$account" -ForegroundColor Gray

# VPC/Subnet/SG 결정
if (-not $VpcId -or -not $SecurityGroupId -or $SubnetIds.Count -eq 0) {
    $discovered = Discover-VpcAndNetwork
    if (-not $discovered) {
        Write-Host ""
        Write-Host "FAIL: VPC/Subnet/SG 를 찾을 수 없습니다. 아래처럼 직접 지정하세요:" -ForegroundColor Red
        Write-Host '  .\scripts\infra\batch_video_setup_full.ps1 -VpcId "vpc-xxx" -SubnetIds @("subnet-a","subnet-b") -SecurityGroupId "sg-xxx"' -ForegroundColor White
        exit 1
    }
    if (-not $VpcId) { $VpcId = $discovered.VpcId }
    if (-not $SecurityGroupId) { $SecurityGroupId = $discovered.SecurityGroupId }
    if ($SubnetIds.Count -eq 0) { $SubnetIds = $discovered.SubnetIds }
}

Write-Host ""
Write-Host "사용 값: VpcId=$VpcId Subnets=$($SubnetIds -join ',') SecurityGroupId=$SecurityGroupId" -ForegroundColor Yellow

if ($DiscoverOnly) {
    Write-Host ""
    Write-Host "DiscoverOnly: 위 값으로 setup 을 실행하려면 -DiscoverOnly 없이 다시 실행하세요." -ForegroundColor Cyan
    exit 0
}

# 1) batch_video_setup.ps1
Write-Host ""
Write-Host "=== [1/2] batch_video_setup.ps1 ===" -ForegroundColor Cyan
$setupParams = @{
    Region          = $Region
    VpcId           = $VpcId
    SubnetIds       = $SubnetIds
    SecurityGroupId = $SecurityGroupId
    EcrRepoUri      = $EcrRepoUri
}
& (Join-Path $InfraPath "batch_video_setup.ps1") @setupParams
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: batch_video_setup.ps1 실패" -ForegroundColor Red
    exit 1
}

# 2) batch_video_verify_and_register.ps1
Write-Host ""
Write-Host "=== [2/2] batch_video_verify_and_register.ps1 ===" -ForegroundColor Cyan
& (Join-Path $InfraPath "batch_video_verify_and_register.ps1") -Region $Region -EcrRepoUri $EcrRepoUri
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: batch_video_verify_and_register.ps1 실패" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "DONE. Video Batch 인프라 준비 완료." -ForegroundColor Green
exit 0
