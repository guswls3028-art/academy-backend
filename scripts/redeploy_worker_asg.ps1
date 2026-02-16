# ==============================================================================
# 워커 ASG 재배포 (루트/관리자 한 방)
# 전제: 루트 또는 IAM 관리자 액세스 키로 로그인 (env 또는 aws configure)
#
# 한 방 실행 (기본 VPC/서브넷/보안그룹 사용):
#   cd C:\academy
#   .\scripts\redeploy_worker_asg.ps1
#
# 인프라만 갱신하고 SSM·IAM 설정은 건너뛰기:
#   .\scripts\redeploy_worker_asg.ps1 -SkipSetup
#
# 서브넷/보안그룹 지정:
#   .\scripts\redeploy_worker_asg.ps1 -SubnetIds "subnet-a,subnet-b" -SecurityGroupId "sg-xxx"
# ==============================================================================

param(
    [string]$SubnetIds = "subnet-07a8427d3306ce910,subnet-09231ed7ecf59cfa4",
    [string]$SecurityGroupId = "sg-02692600fbf8e26f7",
    [string]$IamInstanceProfileName = "academy-ec2-role",
    [string]$Region = "ap-northeast-2",
    [switch]$SkipSetup = $false   # true면 deploy만, SSM/EC2 정책 갱신 생략
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

Write-Host "`n=== Worker ASG Redeploy (root/admin) ===`n" -ForegroundColor Cyan

# 1) 인프라 배포 (Lambda, Launch Template, ASG, Target Tracking)
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
