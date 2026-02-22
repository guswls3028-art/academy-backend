# ==============================================================================
# Video ASG를 MixedInstancesPolicy로 전환 (c6g.large Spot 1순위, t4g.medium Spot 2순위)
# 기존 ASG가 LaunchTemplate 단일 타입이면 삭제 후 재생성. 기존 서브넷/min/max 유지.
# ==============================================================================
# 사용: .\scripts\apply_video_mixed_instances.ps1
#      .\scripts\apply_video_mixed_instances.ps1 -Region ap-northeast-2
# 주의: ASG 삭제 시 기존 인스턴스 전부 종료됨. 재생성 후 새 Spot 인스턴스가 뜸.
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$LtName = "academy-video-worker-lt"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

# 1) 현재 ASG 조회
$asgJson = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --region $Region --query "AutoScalingGroups[0]" --output json 2>$null
if (-not $asgJson -or $asgJson -eq "null") {
    Write-Error "ASG $AsgName not found in region $Region. Create it first (e.g. deploy_worker_asg.ps1)."
    exit 1
}
$asg = $asgJson | ConvertFrom-Json
$subnets = $asg.VpcZoneIdentifier
$minSize = [int]$asg.MinSize
$maxSize = [int]$asg.MaxSize
$desired = [int]$asg.DesiredCapacity
$hasMixed = $asg.MixedInstancesPolicy -and $asg.MixedInstancesPolicy.LaunchTemplate

if ($hasMixed) {
    Write-Host "ASG $AsgName already has MixedInstancesPolicy. Overrides: $($asg.MixedInstancesPolicy.LaunchTemplate.Overrides | ConvertTo-Json -Compress)" -ForegroundColor Green
    Write-Host "No change needed. Exiting." -ForegroundColor Gray
    exit 0
}

# 2) Launch Template 존재 확인
$ltCheck = aws ec2 describe-launch-templates --launch-template-names $LtName --region $Region --query "LaunchTemplates[0].LaunchTemplateName" --output text 2>$null
if (-not $ltCheck -or $ltCheck -eq "None") {
    Write-Error "Launch template $LtName not found. Create it first (run deploy_worker_asg.ps1 once)."
    exit 1
}
Write-Host "Using Launch Template: $LtName" -ForegroundColor Gray

# 3) MixedInstancesPolicy JSON (c6g.large 1순위, t4g.medium 2순위, 100% Spot, capacity-optimized)
$mixedPolicyJson = @"
{"LaunchTemplate":{"LaunchTemplateSpecification":{"LaunchTemplateName":"$LtName","Version":"`$Latest"},"Overrides":[{"InstanceType":"c6g.large"},{"InstanceType":"t4g.medium"}]},"InstancesDistribution":{"OnDemandBaseCapacity":0,"OnDemandPercentageAboveBaseCapacity":0,"SpotAllocationStrategy":"capacity-optimized"}}
"@
$mixedPolicyFile = Join-Path $RepoRoot "asg_video_mixed_policy.json"
[System.IO.File]::WriteAllText($mixedPolicyFile, $mixedPolicyJson.Trim(), $utf8NoBom)
$mixedPolicyPath = "file://$($mixedPolicyFile -replace '\\','/' -replace ' ', '%20')"

# 4) ASG 삭제 (기존 인스턴스 전부 종료)
Write-Host "Deleting ASG $AsgName (current instances will be terminated)..." -ForegroundColor Yellow
aws autoscaling delete-auto-scaling-group --auto-scaling-group-name $AsgName --force-delete --region $Region
if ($LASTEXITCODE -ne 0) {
    Remove-Item $mixedPolicyFile -Force -ErrorAction SilentlyContinue
    throw "delete-auto-scaling-group failed."
}

# 5) 삭제 완료 대기
$waitSec = 0
$maxWait = 120
while ($waitSec -lt $maxWait) {
    $count = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --region $Region --query "length(AutoScalingGroups)" --output text 2>$null
    if ($count -eq "0" -or $count -eq "" -or $count -eq "None") {
        Write-Host "ASG deleted (confirmed after ${waitSec}s)." -ForegroundColor Gray
        break
    }
    Start-Sleep -Seconds 5
    $waitSec += 5
    Write-Host "Waiting for ASG deletion... ${waitSec}s" -ForegroundColor Gray
}
if ($waitSec -ge $maxWait) {
    Remove-Item $mixedPolicyFile -Force -ErrorAction SilentlyContinue
    Write-Error "ASG still exists after ${maxWait}s. Aborting."
    exit 1
}

# 6) MixedInstancesPolicy로 재생성
Write-Host "Creating ASG $AsgName with MixedInstancesPolicy (c6g.large, t4g.medium Spot)..." -ForegroundColor Cyan
aws autoscaling create-auto-scaling-group --auto-scaling-group-name $AsgName `
    --mixed-instances-policy $mixedPolicyPath `
    --min-size $minSize --max-size $maxSize --desired-capacity $desired `
    --vpc-zone-identifier $subnets --region $Region
if ($LASTEXITCODE -ne 0) {
    Remove-Item $mixedPolicyFile -Force -ErrorAction SilentlyContinue
    throw "create-auto-scaling-group failed."
}
Remove-Item $mixedPolicyFile -Force -ErrorAction SilentlyContinue
Write-Host "ASG created." -ForegroundColor Green

# 7) TargetTracking 정책 재적용 (SSOT: scripts/infra/apply_video_asg_scaling_policy.ps1)
Write-Host "Re-applying scaling policy via SSOT (video-visible-only-tt)..." -ForegroundColor Cyan
& (Join-Path $ScriptRoot "infra\apply_video_asg_scaling_policy.ps1") -Region $Region -AsgName $AsgName
Write-Host "Done. Video ASG now uses MixedInstancesPolicy (c6g.large Spot primary, t4g.medium fallback)." -ForegroundColor Green
