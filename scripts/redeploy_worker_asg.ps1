# ==============================================================================
# Worker ASG redeploy (root/admin one-shot)
# Requires: root or IAM admin access key (env or aws configure)
#
# Video = AWS Batch 전용 (기본). -ExcludeVideo (기본 true)로 video ASG 생성/업데이트 스킵.
# Video ASG 사용 시: -ExcludeVideo:$false (레거시)
#
# Default VPC/subnet/SG: cd C:\academy; .\scripts\redeploy_worker_asg.ps1
# Infra only (skip SSM/IAM): .\scripts\redeploy_worker_asg.ps1 -SkipSetup
# Custom: .\scripts\redeploy_worker_asg.ps1 -SubnetIds "subnet-a,subnet-b" -SecurityGroupId "sg-xxx"
# Lambda in VPC (API fetch 실패 시): .\scripts\redeploy_worker_asg.ps1 -LambdaVpcSubnetId "subnet-049e711f41fdff71b" -LambdaVpcSecurityGroupId "academy-api-sg"
# ==============================================================================

param(
    [string]$SubnetIds = "subnet-07a8427d3306ce910",   # same as build (SSM works there); was two subnets, use one so workers get SSM
    [string]$SecurityGroupId = "sg-02692600fbf8e26f7",
    [string]$IamInstanceProfileName = "academy-ec2-role",
    [string]$Region = "ap-northeast-2",
    [switch]$ExcludeVideo = $true,  # if true, skip video ASG (Video = Batch only)
    [switch]$SkipSetup = $false,   # if true, deploy only; skip SSM/EC2 policy refresh
    [string]$LambdaVpcSubnetId = "",       # optional: put queue-depth Lambda in VPC (WAF/API fetch 실패 시)
    [string]$LambdaVpcSecurityGroupId = "" # optional: e.g. academy-api-sg; requires LambdaVpcSubnetId
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

Write-Host "`n=== Worker ASG Redeploy (root/admin) ===`n" -ForegroundColor Cyan

# 1) Infra deploy (Lambda, Launch Template, ASG, Target Tracking)
$deployParams = @{
    SubnetIds               = $SubnetIds
    SecurityGroupId         = $SecurityGroupId
    IamInstanceProfileName  = $IamInstanceProfileName
    Region                  = $Region
    UploadEnvToSsm          = $false
    AttachEc2Policy         = $false
    GrantSsmPutToCaller     = $false
}
if ($LambdaVpcSubnetId) { $deployParams["LambdaVpcSubnetId"] = $LambdaVpcSubnetId }
if ($LambdaVpcSecurityGroupId) { $deployParams["LambdaVpcSecurityGroupId"] = $LambdaVpcSecurityGroupId }
& (Join-Path $ScriptRoot "deploy_worker_asg.ps1") @deployParams

if (-not $SkipSetup) {
    Write-Host "`n--- SSM + EC2 role refresh ---`n" -ForegroundColor Cyan
    & (Join-Path $ScriptRoot "setup_worker_iam_and_ssm.ps1") `
        -SsmUserName "admin97" `
        -IamInstanceProfileName $IamInstanceProfileName `
        -Region $Region
}

Write-Host "`n=== Redeploy Done ===`n" -ForegroundColor Green
Write-Host "API 서버도 SQS/Redis 등 같은 .env를 씁니다. SSM만 올렸다면 API .env는 자동 갱신되지 않습니다." -ForegroundColor Yellow
Write-Host "업로드 완료 503 방지: .\scripts\sync_api_env_lambda_internal.ps1  (SSM -> API EC2 .env + docker restart academy-api)" -ForegroundColor Gray
