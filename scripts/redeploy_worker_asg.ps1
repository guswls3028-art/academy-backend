# ==============================================================================
# Worker ASG redeploy (root/admin one-shot)
# Requires: root or IAM admin access key (env or aws configure)
#
# Default VPC/subnet/SG: cd C:\academy; .\scripts\redeploy_worker_asg.ps1
# Infra only (skip SSM/IAM): .\scripts\redeploy_worker_asg.ps1 -SkipSetup
# Custom: .\scripts\redeploy_worker_asg.ps1 -SubnetIds "subnet-a,subnet-b" -SecurityGroupId "sg-xxx"
# ==============================================================================

param(
    [string]$SubnetIds = "subnet-07a8427d3306ce910",   # same as build (SSM works there); was two subnets, use one so workers get SSM
    [string]$SecurityGroupId = "sg-02692600fbf8e26f7",
    [string]$IamInstanceProfileName = "academy-ec2-role",
    [string]$Region = "ap-northeast-2",
    [switch]$SkipSetup = $false   # if true, deploy only; skip SSM/EC2 policy refresh
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

Write-Host "`n=== Worker ASG Redeploy (root/admin) ===`n" -ForegroundColor Cyan

# 1) Infra deploy (Lambda, Launch Template, ASG, Target Tracking)
& (Join-Path $ScriptRoot "deploy_worker_asg.ps1") `
    -SubnetIds $SubnetIds `
    -SecurityGroupId $SecurityGroupId `
    -IamInstanceProfileName $IamInstanceProfileName `
    -Region $Region `
    -UploadEnvToSsm:$false `
    -AttachEc2Policy:$false `
    -GrantSsmPutToCaller:$false

# 2) CloudWatch log groups: 7-day retention (avoid unbounded cost)
$logGroups = @("/aws/ec2/academy-video-worker", "/aws/ec2/academy-messaging-worker", "/aws/ec2/academy-ai-worker")
foreach ($lg in $logGroups) {
    aws logs create-log-group --log-group-name $lg --region $Region 2>$null
    aws logs put-retention-policy --log-group-name $lg --retention-in-days 7 --region $Region 2>$null
}

if (-not $SkipSetup) {
    Write-Host "`n--- SSM + EC2 role refresh ---`n" -ForegroundColor Cyan
    & (Join-Path $ScriptRoot "setup_worker_iam_and_ssm.ps1") `
        -SsmUserName "admin97" `
        -IamInstanceProfileName $IamInstanceProfileName `
        -Region $Region
}

Write-Host "`n=== Redeploy Done ===`n" -ForegroundColor Green
