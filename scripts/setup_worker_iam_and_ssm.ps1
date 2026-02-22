# ==============================================================================
# One-time setup after worker deploy: admin97 SSM permission + SSM .env upload + EC2 role policy
# Must run as root or IAM admin (put-user-policy, put-role-policy).
# Usage: .\scripts\setup_worker_iam_and_ssm.ps1
# ==============================================================================

param(
    [string]$SsmUserName = "admin97",
    [string]$IamInstanceProfileName = "academy-ec2-role",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$AsgInfra = Join-Path $RepoRoot "infra\worker_asg"

$AccountId = (aws sts get-caller-identity --query Account --output text)
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

Write-Host "Account: $AccountId | User/Role: $((aws sts get-caller-identity --query Arn --output text))" -ForegroundColor Cyan
Write-Host ""

# 1) Grant SSM PutParameter to admin97
Write-Host "[1/3] Attaching SSM PutParameter policy to user '$SsmUserName'..." -ForegroundColor Cyan
$ssmPolicyPath = Join-Path $AsgInfra "iam_policy_ssm_put_workers_env.json"
$ssmPolicyJson = (Get-Content $ssmPolicyPath -Raw) -replace '\{\{Region\}\}', $Region -replace '\{\{AccountId\}\}', $AccountId
$ssmPolicyFile = Join-Path $RepoRoot "iam_ssm_put_temp.json"
[System.IO.File]::WriteAllText($ssmPolicyFile, $ssmPolicyJson, $utf8NoBom)
$ssmPolicyUri = "file://$($ssmPolicyFile -replace '\\','/' -replace ' ', '%20')"
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws iam put-user-policy --user-name $SsmUserName --policy-name academy-ssm-put-workers-env --policy-document $ssmPolicyUri 2>$null
Remove-Item $ssmPolicyFile -Force -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) {
    Write-Host "      FAILED. Run this script as root or IAM admin (need iam:PutUserPolicy)." -ForegroundColor Red
    exit 1
}
Write-Host "      OK." -ForegroundColor Green
$ErrorActionPreference = $ea

# 2) .env -> SSM (Windows-safe via upload_env_to_ssm.ps1)
Write-Host "[2/3] Uploading .env to /academy/workers/env..." -ForegroundColor Cyan
$uploadScript = Join-Path $ScriptRoot "upload_env_to_ssm.ps1"
if (-not (Test-Path $uploadScript)) {
    Write-Host "      upload_env_to_ssm.ps1 not found; skip." -ForegroundColor Yellow
} else {
    & $uploadScript -RepoRoot $RepoRoot -Region $Region
    if ($LASTEXITCODE -eq 0) { Write-Host "      OK." -ForegroundColor Green } else { Write-Host "      FAILED (or .env missing)." -ForegroundColor Red }
}

# 3) Attach SSM+ECR policy + AmazonSSMManagedInstanceCore to EC2 role
Write-Host "[3/3] Attaching SSM+ECR + AmazonSSMManagedInstanceCore to EC2 role (instance profile: $IamInstanceProfileName)..." -ForegroundColor Cyan
$roleName = (aws iam get-instance-profile --instance-profile-name $IamInstanceProfileName --query "InstanceProfile.Roles[0].RoleName" --output text 2>$null)
if (-not $roleName) {
    Write-Host "      Instance profile not found; skip." -ForegroundColor Yellow
} else {
    $ec2PolicyPath = Join-Path $AsgInfra "iam_policy_ec2_worker.json"
    $ec2PolicyUri = "file://$($ec2PolicyPath -replace '\\','/' -replace ' ', '%20')"
    $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    aws iam put-role-policy --role-name $roleName --policy-name academy-workers-ssm-ecr --policy-document $ec2PolicyUri 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "      OK (role: $roleName)." -ForegroundColor Green } else { Write-Host "      FAILED." -ForegroundColor Red }
    # SSM Run Command용 (investigate_video_worker_runtime.ps1)
    aws iam attach-role-policy --role-name $roleName --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "      AmazonSSMManagedInstanceCore attached." -ForegroundColor Green } else { Write-Host "      (AmazonSSMManagedInstanceCore may already be attached)" -ForegroundColor Gray }
    $ErrorActionPreference = $ea
}

Write-Host ""
Write-Host "Done. Next: admin97 can run deploy/SSM commands. If ECR images missing: .\scripts\build_and_push_ecr.ps1" -ForegroundColor Green
Write-Host "Prevent ASG worker stop loop: .\scripts\remove_ec2_stop_from_worker_role.ps1 (docs_cursor/11-worker-self-stop-root-cause.md)" -ForegroundColor Gray
