# ==============================================================================
# Worker ASG redeploy (root/admin one-shot)
# Requires: root or IAM admin access key (env or aws configure)
#
# Default VPC/subnet/SG: cd C:\academy; .\scripts\redeploy_worker_asg.ps1
# Infra only (skip SSM/IAM): .\scripts\redeploy_worker_asg.ps1 -SkipSetup
# Custom: .\scripts\redeploy_worker_asg.ps1 -SubnetIds "subnet-a,subnet-b" -SecurityGroupId "sg-xxx"
# ==============================================================================

param(
    [string]$SubnetIds = "subnet-07a8427d3306ce910,subnet-09231ed7ecf59cfa4",
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

if (-not $SkipSetup) {
    Write-Host "`n--- SSM + EC2 role refresh ---`n" -ForegroundColor Cyan
    & (Join-Path $ScriptRoot "setup_worker_iam_and_ssm.ps1") `
        -SsmUserName "admin97" `
        -IamInstanceProfileName $IamInstanceProfileName `
        -Region $Region
}

Write-Host "`n=== Redeploy Done ===`n" -ForegroundColor Green
