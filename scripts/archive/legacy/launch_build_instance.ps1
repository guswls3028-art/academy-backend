# ==============================================================================
# Launch EC2 arm64 spot instance (native Docker image build)
# Usage: .\scripts\launch_build_instance.ps1 -SubnetId "subnet-xxx" -SecurityGroupId "sg-xxx"
# ==============================================================================

param(
    [Parameter(Mandatory = $true)]
    [string]$SubnetId,
    [Parameter(Mandatory = $true)]
    [string]$SecurityGroupId,
    [string]$Region = "ap-northeast-2",
    [string]$InstanceType = "t4g.medium",
    [string]$RoleName = "academy-ec2-role"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$AsgInfra = Join-Path $RepoRoot "infra\worker_asg"

$AccountId = (aws sts get-caller-identity --query Account --output text)
$AmiId = (aws ec2 describe-images --region $Region --owners amazon `
    --filters "Name=name,Values=al2023-ami-*-kernel-6.1-arm64" "Name=state,Values=available" `
    --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)

Write-Host "AMI (arm64): $AmiId" -ForegroundColor Cyan
Write-Host "Instance: $InstanceType spot" -ForegroundColor Cyan

# Try adding ECR push policy (skip if no permission)
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$hasBuild = $false
try {
    $rolePolicies = (aws iam list-role-policies --role-name $RoleName --query "PolicyNames" --output json 2>$null) | ConvertFrom-Json
    $hasBuild = $rolePolicies -and ($rolePolicies -contains "academy-ec2-build")
} catch { }
if (-not $hasBuild) {
    Write-Host "Adding ECR push policy to role $RoleName..." -ForegroundColor Yellow
    $policyPath = Join-Path $AsgInfra "iam_policy_ec2_build.json"
    if (Test-Path $policyPath) {
        $policyUri = "file://$($policyPath -replace '\\','/' -replace ' ', '%20')"
        aws iam put-role-policy --role-name $RoleName --policy-name academy-ec2-build --policy-document $policyUri 2>$null
        if ($LASTEXITCODE -eq 0) { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  (No permission - add policy as root then ECR push from instance)" -ForegroundColor Yellow }
    }
}
$ErrorActionPreference = $ea

# Launch spot instance
$userData = @"
#!/bin/bash
yum update -y
yum install -y docker git
systemctl start docker
usermod -aG docker ec2-user
echo 'Build instance ready'
"@
$userDataB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($userData))

Write-Host "Launching spot instance..." -ForegroundColor Cyan
$spotFile = Join-Path $RepoRoot "spot_options.json"
'{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time","MaxPrice":"0.05"}}' | Set-Content $spotFile -Encoding ASCII -NoNewline
$spotUri = "file://$($spotFile -replace '\\','/' -replace ' ', '%20')"
$result = aws ec2 run-instances --image-id $AmiId --instance-type $InstanceType `
    --count 1 --subnet-id $SubnetId --security-group-ids $SecurityGroupId `
    --iam-instance-profile "Name=$RoleName" --user-data $userDataB64 `
    --instance-market-options $spotUri `
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=academy-build-arm64}]" `
    --region $Region --output json 2>&1 | ConvertFrom-Json
Remove-Item $spotFile -Force -ErrorAction SilentlyContinue

if (-not $result -or -not $result.Instances -or $result.Instances.Count -eq 0) {
    Write-Host "run-instances failed. Check above error." -ForegroundColor Red
    exit 1
}
$instanceId = $result.Instances[0].InstanceId
Write-Host "Instance: $instanceId" -ForegroundColor Green

Write-Host "`nWaiting for instance running (2 min)..." -ForegroundColor Yellow
aws ec2 wait instance-running --instance-ids $instanceId --region $Region

Write-Host "`n=== Connect and build ===`n" -ForegroundColor Cyan
Write-Host "1) SSM connect (if IAM has SSM permission):" -ForegroundColor White
Write-Host "   aws ssm start-session --target $instanceId --region $Region" -ForegroundColor Gray
Write-Host "`n2) Commands to run inside instance (copy/paste):" -ForegroundColor White
$registry = "${AccountId}.dkr.ecr.${Region}.amazonaws.com"
Write-Host @"

cd /tmp
git clone https://github.com/guswls3028-art/academy-backend.git academy 2>/dev/null || (echo 'git clone failed - check repo URL/token')
cd academy

registry='$registry'
region='$Region'
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $registry

docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
docker build -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest .

docker tag academy-messaging-worker:latest `$registry/academy-messaging-worker:latest
docker tag academy-video-worker:latest `$registry/academy-video-worker:latest
docker tag academy-ai-worker-cpu:latest `$registry/academy-ai-worker-cpu:latest

docker push `$registry/academy-messaging-worker:latest
docker push `$registry/academy-video-worker:latest
docker push `$registry/academy-ai-worker-cpu:latest

echo Done. Exit: exit

"@ -ForegroundColor Gray

Write-Host "`n3) Terminate (from local terminal):" -ForegroundColor White
Write-Host "   aws ec2 terminate-instances --instance-ids $instanceId --region $Region" -ForegroundColor Gray
Write-Host "`nInstanceId: $instanceId" -ForegroundColor Green
