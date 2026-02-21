# ==============================================================================
# ASG Target Tracking worker deploy (Min=0, SQS queue depth based)
# Requires: AWS CLI configured, SSM /academy/workers/env has .env content
# Usage: .\scripts\deploy_worker_asg.ps1 -SubnetIds "subnet-xxx,subnet-yyy" -SecurityGroupId "sg-xxx" -IamInstanceProfileName "academy-ec2-role"
# ==============================================================================

param(
    [Parameter(Mandatory = $true)]
    [string]$SubnetIds,          # comma-separated, e.g. "subnet-aaa,subnet-bbb"
    [Parameter(Mandatory = $true)]
    [string]$SecurityGroupId,
    [Parameter(Mandatory = $true)]
    [string]$IamInstanceProfileName,
    [string]$KeyNameAi = "",   # from _config_instance_keys (academy-ai-worker-cpu)
    [string]$KeyNameVideo = "",   # from _config_instance_keys (academy-video-worker)
    [string]$KeyNameMessaging = "",   # from _config_instance_keys (academy-messaging-worker)
    [string]$Region = "ap-northeast-2",
    [string]$AmiId = "",         # optional, default latest Amazon Linux 2023
    [int]$MaxCapacity = 20,
    [int]$TargetMessagesPerInstance = 20,
    [switch]$UploadEnvToSsm = $true,   # if .env exists, upload to SSM /academy/workers/env
    [switch]$AttachEc2Policy = $true,   # attach SSM+ECR inline policy to EC2 role
    [switch]$GrantSsmPutToCaller = $true,   # grant SSM PutParameter to caller
    [string]$SsmPutGrantUser = ""           # empty = grant to current caller; set to IAM user (e.g. admin97) to grant to that user
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
. (Join-Path $ScriptRoot "_config_instance_keys.ps1")
$AsgInfra = Join-Path $RepoRoot "infra\worker_asg"
$QueueDepthLambdaDir = Join-Path $AsgInfra "queue_depth_lambda"
# SSOT: Key pair names from _config_instance_keys (PemFile minus .pem)
if (-not $KeyNameAi)       { $KeyNameAi = Get-KeyPairName $INSTANCE_KEY_FILES["academy-ai-worker-cpu"] }
if (-not $KeyNameVideo)    { $KeyNameVideo = Get-KeyPairName $INSTANCE_KEY_FILES["academy-video-worker"] }
if (-not $KeyNameMessaging) { $KeyNameMessaging = Get-KeyPairName $INSTANCE_KEY_FILES["academy-messaging-worker"] }
$UserDataDir = Join-Path $AsgInfra "user_data"

$AccountId = (aws sts get-caller-identity --query Account --output text)
$ECRRegistry = "${AccountId}.dkr.ecr.${Region}.amazonaws.com"

# Lambda execution role (reuse academy-lambda; add inline policy for queue depth if needed)
$LambdaRoleName = "academy-lambda"
$QueueDepthLambdaName = "academy-worker-queue-depth-metric"
$EventBridgeRuleName = "academy-worker-queue-depth-rate"

# ------------------------------------------------------------------------------
# 0) AMI (Amazon Linux 2023 - exclude ECS optimized, use plain AL2023 only)
# ------------------------------------------------------------------------------
if (-not $AmiId) {
    Write-Host "[0/8] Resolving latest Amazon Linux 2023 AMI (arm64, non-ECS)..." -ForegroundColor Cyan
    $all = aws ec2 describe-images --region $Region --owners amazon `
        --filters "Name=name,Values=al2023-ami-*-kernel-6.1-arm64" "Name=state,Values=available" `
        --query "sort_by(Images, &CreationDate)" --output json | ConvertFrom-Json
    # Exclude ECS optimized AMI (al2023-ami-ecs-hvm-*); workers use user_data for docker run only
    $nonEcs = $all | Where-Object { $_.Name -notmatch "ecs" }
    if ($nonEcs) {
        $AmiId = ($nonEcs | Select-Object -Last 1).ImageId
    }
    if (-not $AmiId) {
        $AmiId = (aws ec2 describe-images --region $Region --owners amazon `
            --filters "Name=name,Values=amzn2-ami-*-arm64-gp2" "Name=state,Values=available" `
            --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)
    }
    Write-Host "      AMI: $AmiId" -ForegroundColor Gray
}

# ------------------------------------------------------------------------------
# 1) Queue depth Lambda + EventBridge
# ------------------------------------------------------------------------------
Write-Host "[1/8] Queue depth Lambda + EventBridge (1 min)..." -ForegroundColor Cyan
$ZipPath = Join-Path $RepoRoot "worker_queue_depth_lambda.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $QueueDepthLambdaDir "lambda_function.py") -DestinationPath $ZipPath -Force

$RoleArn = "arn:aws:iam::${AccountId}:role/${LambdaRoleName}"
$lambdaExists = $false
try { aws lambda get-function --function-name $QueueDepthLambdaName --region $Region 2>$null | Out-Null; $lambdaExists = $true } catch {}

if ($lambdaExists) {
    aws lambda update-function-code --function-name $QueueDepthLambdaName --zip-file "fileb://$ZipPath" --region $Region | Out-Null
    # B1: VIDEO_BACKLOG_API_URL 설정 시 DB 기반 BacklogCount 사용 (예: https://api.example.com)
    # 미설정 시 SQS visible+inflight fallback
    # $envJson = '{"Variables":{"VIDEO_BACKLOG_API_URL":"https://api.example.com"}}'
    # aws lambda update-function-configuration --function-name $QueueDepthLambdaName --region $Region --environment $envJson
} else {
    aws lambda create-function --function-name $QueueDepthLambdaName --runtime python3.11 --role $RoleArn `
        --handler lambda_function.lambda_handler --zip-file "fileb://$ZipPath" --timeout 30 --memory-size 128 `
        --region $Region | Out-Null
}
Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

aws events put-rule --name $EventBridgeRuleName --schedule-expression "rate(1 minute)" --state ENABLED --region $Region | Out-Null
$LambdaArn = (aws lambda get-function --function-name $QueueDepthLambdaName --region $Region --query "Configuration.FunctionArn" --output text)
aws events put-targets --rule $EventBridgeRuleName --targets "Id=1,Arn=$LambdaArn" --region $Region | Out-Null
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws lambda add-permission --function-name $QueueDepthLambdaName --statement-id "EventBridgeInvoke" `
    --action "lambda:InvokeFunction" --principal "events.amazonaws.com" `
    --source-arn "arn:aws:events:${Region}:${AccountId}:rule/${EventBridgeRuleName}" --region $Region 2>$null
$ErrorActionPreference = $ea

# Lambda role needs SQS/CloudWatch (add iam_policy_queue_depth_lambda.json inline or separate policy)
Write-Host "      Ensure role $LambdaRoleName has SQS GetQueueAttributes + CloudWatch PutMetricData (Academy/Workers)." -ForegroundColor Yellow

# ------------------------------------------------------------------------------
# 2) Launch Template AI
# ------------------------------------------------------------------------------
Write-Host "[2/8] Launch Template (AI worker)..." -ForegroundColor Cyan
$aiUserDataPath = Join-Path $UserDataDir "ai_worker_user_data.sh"
$aiUserDataRaw = Get-Content $aiUserDataPath -Raw
$aiUserDataRaw = $aiUserDataRaw -replace "{{ECR_REGISTRY}}", $ECRRegistry
$aiUserDataB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($aiUserDataRaw))

$LtAiName = "academy-ai-worker-asg"
# AI 워커: 루트 볼륨 20GB (Docker 이미지 + 컨테이너용, Video보다 작지만 충분)
$aiBlockDevices = '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]'
$ltAiKey = if ($KeyNameAi) { ",`"KeyName`":`"$KeyNameAi`"" } else { "" }
$ltAiJson = @"
{"ImageId":"$AmiId","InstanceType":"t4g.small","IamInstanceProfile":{"Name":"$IamInstanceProfileName"},"SecurityGroupIds":["$SecurityGroupId"]$ltAiKey,"UserData":"$aiUserDataB64","BlockDeviceMappings":$aiBlockDevices,"TagSpecifications":[{"ResourceType":"instance","Tags":[{"Key":"Name","Value":"academy-ai-worker-cpu"}]}]}
"@
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$ltAiFile = Join-Path $RepoRoot "lt_ai_data.json"
[System.IO.File]::WriteAllText($ltAiFile, $ltAiJson.Trim(), $utf8NoBom)
$ltAiPath = "file://$($ltAiFile -replace '\\','/' -replace ' ', '%20')"
$ltAiExists = $false
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try { aws ec2 describe-launch-templates --launch-template-names $LtAiName --region $Region 2>$null | Out-Null; $ltAiExists = $true } catch {}
if (-not $ltAiExists) {
    aws ec2 create-launch-template --launch-template-name $LtAiName --version-description "ASG AI worker" --launch-template-data $ltAiPath --region $Region 2>$null | Out-Null
} else {
    $newVer = aws ec2 create-launch-template-version --launch-template-name $LtAiName --launch-template-data $ltAiPath --region $Region --query "LaunchTemplateVersion.VersionNumber" --output text 2>$null
    if ($newVer) { aws ec2 modify-launch-template --launch-template-name $LtAiName --default-version $newVer --region $Region 2>$null | Out-Null }
}
$ErrorActionPreference = $ea
Remove-Item $ltAiFile -Force -ErrorAction SilentlyContinue

# ------------------------------------------------------------------------------
# 3) Launch Template Video (academy-video-worker-lt for MixedInstancesPolicy)
#     LT default InstanceType t4g.medium (fallback); Overrides add c6g.large (Spot primary)
# ------------------------------------------------------------------------------
Write-Host "[3/8] Launch Template (Video worker, academy-video-worker-lt)..." -ForegroundColor Cyan
$videoUserDataPath = Join-Path $UserDataDir "video_worker_user_data.sh"
$videoUserDataRaw = Get-Content $videoUserDataPath -Raw
$videoUserDataRaw = $videoUserDataRaw -replace "{{ECR_REGISTRY}}", $ECRRegistry
$videoUserDataB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($videoUserDataRaw))

$LtVideoName = "academy-video-worker-lt"
# Root volume >= 30GB (AMI snapshot requirement); second volume 100GB for transcode
$blockDevices = '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}},{"DeviceName":"/dev/sdb","Ebs":{"VolumeSize":100,"VolumeType":"gp3"}}]'
$ltVideoKey = if ($KeyNameVideo) { ",`"KeyName`":`"$KeyNameVideo`"" } else { "" }
$ltVideoJson = @"
{"ImageId":"$AmiId","InstanceType":"t4g.medium","IamInstanceProfile":{"Name":"$IamInstanceProfileName"},"SecurityGroupIds":["$SecurityGroupId"]$ltVideoKey,"UserData":"$videoUserDataB64","BlockDeviceMappings":$blockDevices,"TagSpecifications":[{"ResourceType":"instance","Tags":[{"Key":"Name","Value":"academy-video-worker"}]}]}
"@
$ltVideoFile = Join-Path $RepoRoot "lt_video_data.json"
[System.IO.File]::WriteAllText($ltVideoFile, $ltVideoJson.Trim(), $utf8NoBom)
$ltVideoPath = "file://$($ltVideoFile -replace '\\','/' -replace ' ', '%20')"
$ltVideoExists = $false
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try { aws ec2 describe-launch-templates --launch-template-names $LtVideoName --region $Region 2>$null | Out-Null; $ltVideoExists = $true } catch {}
if (-not $ltVideoExists) {
    aws ec2 create-launch-template --launch-template-name $LtVideoName --version-description "ASG Video worker (MixedInstancesPolicy)" --launch-template-data $ltVideoPath --region $Region 2>$null | Out-Null
} else {
    $newVer = aws ec2 create-launch-template-version --launch-template-name $LtVideoName --launch-template-data $ltVideoPath --region $Region --query "LaunchTemplateVersion.VersionNumber" --output text 2>$null
    if ($newVer) { aws ec2 modify-launch-template --launch-template-name $LtVideoName --default-version $newVer --region $Region 2>$null | Out-Null }
}
$ErrorActionPreference = $ea
Remove-Item $ltVideoFile -Force -ErrorAction SilentlyContinue

# ------------------------------------------------------------------------------
# 3.5) Launch Template Messaging (Min=1 always on)
# ------------------------------------------------------------------------------
Write-Host "[3.5/8] Launch Template (Messaging worker)..." -ForegroundColor Cyan
$messagingUserDataPath = Join-Path $UserDataDir "messaging_worker_user_data.sh"
$messagingUserDataRaw = Get-Content $messagingUserDataPath -Raw
$messagingUserDataRaw = $messagingUserDataRaw -replace "{{ECR_REGISTRY}}", $ECRRegistry
$messagingUserDataB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($messagingUserDataRaw))

$LtMessagingName = "academy-messaging-worker-asg"
# 메시지 워커: 루트 볼륨 20GB (AI 워커와 동일)
$messagingBlockDevices = '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]'
$ltMessagingKey = if ($KeyNameMessaging) { ",`"KeyName`":`"$KeyNameMessaging`"" } else { "" }
$ltMessagingJson = @"
{"ImageId":"$AmiId","InstanceType":"t4g.small","IamInstanceProfile":{"Name":"$IamInstanceProfileName"},"SecurityGroupIds":["$SecurityGroupId"]$ltMessagingKey,"UserData":"$messagingUserDataB64","BlockDeviceMappings":$messagingBlockDevices,"TagSpecifications":[{"ResourceType":"instance","Tags":[{"Key":"Name","Value":"academy-messaging-worker"}]}]}
"@
$ltMessagingFile = Join-Path $RepoRoot "lt_messaging_data.json"
[System.IO.File]::WriteAllText($ltMessagingFile, $ltMessagingJson.Trim(), $utf8NoBom)
$ltMessagingPath = "file://$($ltMessagingFile -replace '\\','/' -replace ' ', '%20')"
$ltMessagingExists = $false
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try { aws ec2 describe-launch-templates --launch-template-names $LtMessagingName --region $Region 2>$null | Out-Null; $ltMessagingExists = $true } catch {}
if (-not $ltMessagingExists) {
    aws ec2 create-launch-template --launch-template-name $LtMessagingName --version-description "ASG Messaging worker" --launch-template-data $ltMessagingPath --region $Region 2>$null | Out-Null
} else {
    $newVer = aws ec2 create-launch-template-version --launch-template-name $LtMessagingName --launch-template-data $ltMessagingPath --region $Region --query "LaunchTemplateVersion.VersionNumber" --output text 2>$null
    if ($newVer) { aws ec2 modify-launch-template --launch-template-name $LtMessagingName --default-version $newVer --region $Region 2>$null | Out-Null }
}
$ErrorActionPreference = $ea
Remove-Item $ltMessagingFile -Force -ErrorAction SilentlyContinue

$SubnetList = $SubnetIds -split "," | ForEach-Object { $_.Trim() }
$SubnetListJson = ($SubnetList | ForEach-Object { "`"$_`"" }) -join ","

# ------------------------------------------------------------------------------
# 4) ASG AI
# ------------------------------------------------------------------------------
Write-Host "[4/8] ASG (AI worker, Min=1 Max=$MaxCapacity)..." -ForegroundColor Cyan
$AsgAiName = "academy-ai-worker-asg"
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws autoscaling create-auto-scaling-group --auto-scaling-group-name $AsgAiName `
    --launch-template "LaunchTemplateName=$LtAiName,Version=`$Latest" `
    --min-size 1 --max-size $MaxCapacity --desired-capacity 1 `
    --vpc-zone-identifier $SubnetIds --region $Region 2>$null
if ($LASTEXITCODE -ne 0) {
    aws autoscaling update-auto-scaling-group --auto-scaling-group-name $AsgAiName `
        --launch-template "LaunchTemplateName=$LtAiName,Version=`$Latest" `
        --vpc-zone-identifier $SubnetIds `
        --min-size 1 --max-size $MaxCapacity --desired-capacity 1 --region $Region 2>$null
}
$ErrorActionPreference = $ea

# ------------------------------------------------------------------------------
# 5) ASG Video (MixedInstancesPolicy only; LaunchTemplate 필드 사용 금지)
#     c6g.large Spot primary, t4g.medium fallback. Drain-safe: ENTERPRISE DRAIN.
#     기존 ASG가 LaunchTemplate 기반이면 Update로 전환 불가 → 삭제 후 재생성.
# ------------------------------------------------------------------------------
Write-Host "[5/8] ASG (Video worker, MixedInstancesPolicy Spot, Min=1 Max=$MaxCapacity)..." -ForegroundColor Cyan
$AsgVideoName = "academy-video-worker-asg"
$mixedPolicyVideo = @"
{"LaunchTemplate":{"LaunchTemplateSpecification":{"LaunchTemplateName":"$LtVideoName","Version":"`$Latest"},"Overrides":[{"InstanceType":"c6g.large"},{"InstanceType":"t4g.medium"}]},"InstancesDistribution":{"OnDemandBaseCapacity":0,"OnDemandPercentageAboveBaseCapacity":0,"SpotAllocationStrategy":"capacity-optimized"}}
"@
$mixedPolicyVideoFile = Join-Path $RepoRoot "asg_video_mixed_policy.json"
[System.IO.File]::WriteAllText($mixedPolicyVideoFile, $mixedPolicyVideo.Trim(), $utf8NoBom)
$mixedPolicyVideoPath = "file://$($mixedPolicyVideoFile -replace '\\','/' -replace ' ', '%20')"

# delete 후 create 전에 삭제 완료될 때까지 polling (Eventually Consistent, 최대 90초+ 소요 가능)
function Wait-VideoAsgDeleted {
    param([string]$AsgName, [string]$Reg, [int]$MaxWaitSec = 120, [int]$IntervalSec = 5)
    $elapsed = 0
    while ($elapsed -lt $MaxWaitSec) {
        $count = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --region $Reg --query "length(AutoScalingGroups)" --output text 2>$null
        if ($count -eq "0" -or $count -eq "" -or $count -eq "None") {
            Write-Host "      ASG deleted (confirmed after ${elapsed}s)." -ForegroundColor Gray
            return $true
        }
        Start-Sleep -Seconds $IntervalSec
        $elapsed += $IntervalSec
        Write-Host "      Waiting for ASG deletion... ${elapsed}s" -ForegroundColor Gray
    }
    Write-Host "      WARNING: ASG still present after ${MaxWaitSec}s." -ForegroundColor Yellow
    return $false
}

$videoAsgJson = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgVideoName --region $Region --query "AutoScalingGroups[0]" --output json 2>$null
$needRecreate = $false
if ($videoAsgJson) {
    $asgObj = $videoAsgJson | ConvertFrom-Json
    if (-not $asgObj.MixedInstancesPolicy -or $null -eq $asgObj.MixedInstancesPolicy) {
        $needRecreate = $true
        Write-Host "      Video ASG is LaunchTemplate-based (MixedInstancesPolicy null). Deleting for recreate..." -ForegroundColor Yellow
    }
}

$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
if ($needRecreate) {
    aws autoscaling delete-auto-scaling-group --auto-scaling-group-name $AsgVideoName --force-delete --region $Region 2>$null
    Wait-VideoAsgDeleted -AsgName $AsgVideoName -Reg $Region -MaxWaitSec 120 -IntervalSec 5 | Out-Null
}

$createDone = $false
aws autoscaling create-auto-scaling-group --auto-scaling-group-name $AsgVideoName `
    --mixed-instances-policy $mixedPolicyVideoPath `
    --min-size 1 --max-size $MaxCapacity --desired-capacity 1 `
    --vpc-zone-identifier $SubnetIds --region $Region 2>$null
if ($LASTEXITCODE -eq 0) {
    $createDone = $true
} else {
    if (-not $needRecreate) {
        Write-Host "      ASG exists; update does not switch LaunchTemplate→MixedInstancesPolicy. Forcing delete and recreate..." -ForegroundColor Yellow
        aws autoscaling delete-auto-scaling-group --auto-scaling-group-name $AsgVideoName --force-delete --region $Region 2>$null
        Wait-VideoAsgDeleted -AsgName $AsgVideoName -Reg $Region -MaxWaitSec 120 -IntervalSec 5 | Out-Null
        aws autoscaling create-auto-scaling-group --auto-scaling-group-name $AsgVideoName `
            --mixed-instances-policy $mixedPolicyVideoPath `
            --min-size 1 --max-size $MaxCapacity --desired-capacity 1 `
            --vpc-zone-identifier $SubnetIds --region $Region 2>$null
        if ($LASTEXITCODE -eq 0) { $createDone = $true }
    }
}
if ($LASTEXITCODE -eq 0 -and -not $needRecreate) {
    $currentMix = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgVideoName --region $Region --query "AutoScalingGroups[0].MixedInstancesPolicy" --output json 2>$null
    if ($currentMix -and $currentMix -ne "null") {
        Write-Host "      Triggering instance refresh..." -ForegroundColor Gray
        aws autoscaling start-instance-refresh --auto-scaling-group-name $AsgVideoName --region $Region 2>$null
    }
}
$ErrorActionPreference = $ea
Remove-Item $mixedPolicyVideoFile -Force -ErrorAction SilentlyContinue
# 검증: aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-video-worker-asg --region ap-northeast-2 --query "AutoScalingGroups[0].MixedInstancesPolicy"

# ------------------------------------------------------------------------------
# 5.5) ASG Messaging (Min=1 always on, Max=$MaxCapacity)
# ------------------------------------------------------------------------------
Write-Host "[6/8] ASG (Messaging worker, Min=1 Max=$MaxCapacity)..." -ForegroundColor Cyan
$AsgMessagingName = "academy-messaging-worker-asg"
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws autoscaling create-auto-scaling-group --auto-scaling-group-name $AsgMessagingName `
    --launch-template "LaunchTemplateName=$LtMessagingName,Version=`$Latest" `
    --min-size 1 --max-size $MaxCapacity --desired-capacity 1 `
    --vpc-zone-identifier $SubnetIds --region $Region 2>$null
if ($LASTEXITCODE -ne 0) {
    aws autoscaling update-auto-scaling-group --auto-scaling-group-name $AsgMessagingName `
        --launch-template "LaunchTemplateName=$LtMessagingName,Version=`$Latest" `
        --vpc-zone-identifier $SubnetIds `
        --min-size 1 --max-size $MaxCapacity --desired-capacity 1 --region $Region 2>$null
}
$ErrorActionPreference = $ea

# ------------------------------------------------------------------------------
# 6) Application Auto Scaling - Register targets
# ------------------------------------------------------------------------------
Write-Host "[7/8] Application Auto Scaling (register targets)..." -ForegroundColor Cyan
$ResourceIdAi = "auto-scaling-group/$AsgAiName"
$ResourceIdVideo = "auto-scaling-group/$AsgVideoName"
$ResourceIdMessaging = "auto-scaling-group/$AsgMessagingName"
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
# AI/Video/Messaging all min 1 instance (min-capacity 1)
aws application-autoscaling register-scalable-target --service-namespace ec2 --resource-id $ResourceIdAi `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --min-capacity 1 --max-capacity $MaxCapacity --region $Region 2>$null
aws application-autoscaling register-scalable-target --service-namespace ec2 --resource-id $ResourceIdVideo `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --min-capacity 1 --max-capacity $MaxCapacity --region $Region 2>$null
aws application-autoscaling register-scalable-target --service-namespace ec2 --resource-id $ResourceIdMessaging `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --min-capacity 1 --max-capacity $MaxCapacity --region $Region 2>$null
$ErrorActionPreference = $ea

# ------------------------------------------------------------------------------
# 7) Target Tracking policies (custom metric Academy/Workers QueueDepth)
# ------------------------------------------------------------------------------
Write-Host "[8/8] Target Tracking (target $TargetMessagesPerInstance msgs/instance)..." -ForegroundColor Cyan
$policyAi = @"
{
  "TargetTrackingScalingPolicyConfiguration": {
    "TargetValue": $TargetMessagesPerInstance,
    "PredefinedMetricSpecification": null,
    "CustomizedMetricSpecification": {
      "MetricName": "QueueDepth",
      "Namespace": "Academy/Workers",
      "Dimensions": [{"Name": "WorkerType", "Value": "AI"}],
      "Statistic": "Average"
    },
    "ScaleInCooldown": 300,
    "ScaleOutCooldown": 60
  }
}
"@
# Video: B1 TargetTracking (BacklogCount, Academy/VideoProcessing)
$policyVideo = @"
{
  "TargetTrackingScalingPolicyConfiguration": {
    "TargetValue": 3,
    "CustomizedMetricSpecification": {
      "MetricName": "BacklogCount",
      "Namespace": "Academy/VideoProcessing",
      "Dimensions": [
        {"Name": "WorkerType", "Value": "Video"},
        {"Name": "AutoScalingGroupName", "Value": "academy-video-worker-asg"}
      ],
      "Statistic": "Average",
      "Unit": "Count"
    },
    "ScaleOutCooldown": 60,
    "ScaleInCooldown": 300
  }
}
"@
$policyMessaging = @"
{
  "TargetTrackingScalingPolicyConfiguration": {
    "TargetValue": $TargetMessagesPerInstance,
    "CustomizedMetricSpecification": {
      "MetricName": "QueueDepth",
      "Namespace": "Academy/Workers",
      "Dimensions": [{"Name": "WorkerType", "Value": "Messaging"}],
      "Statistic": "Average"
    },
    "ScaleInCooldown": 300,
    "ScaleOutCooldown": 60
  }
}
"@
$policyAiFile = Join-Path $RepoRoot "asg_policy_ai.json"
$policyVideoFile = Join-Path $RepoRoot "asg_policy_video.json"
$policyMessagingFile = Join-Path $RepoRoot "asg_policy_messaging.json"
[System.IO.File]::WriteAllText($policyAiFile, $policyAi, $utf8NoBom)
[System.IO.File]::WriteAllText($policyVideoFile, $policyVideo, $utf8NoBom)
[System.IO.File]::WriteAllText($policyMessagingFile, $policyMessaging, $utf8NoBom)
$policyAiPath = "file://$($policyAiFile -replace '\\','/' -replace ' ', '%20')"
$policyVideoPath = "file://$($policyVideoFile -replace '\\','/' -replace ' ', '%20')"
$policyMessagingPath = "file://$($policyMessagingFile -replace '\\','/' -replace ' ', '%20')"
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws application-autoscaling put-scaling-policy --service-namespace ec2 --resource-id $ResourceIdAi `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --policy-name "QueueDepthTargetTracking" `
    --policy-type "TargetTrackingScaling" --target-tracking-scaling-policy-configuration $policyAiPath --region $Region 2>$null
aws application-autoscaling put-scaling-policy --service-namespace ec2 --resource-id $ResourceIdVideo `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --policy-name "BacklogCountTargetTracking" `
    --policy-type "TargetTrackingScaling" --target-tracking-scaling-policy-configuration $policyVideoPath --region $Region 2>$null
aws application-autoscaling put-scaling-policy --service-namespace ec2 --resource-id $ResourceIdMessaging `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --policy-name "QueueDepthTargetTracking" `
    --policy-type "TargetTrackingScaling" --target-tracking-scaling-policy-configuration $policyMessagingPath --region $Region 2>$null
$ErrorActionPreference = $ea
Remove-Item $policyAiFile, $policyVideoFile, $policyMessagingFile -Force -ErrorAction SilentlyContinue

Write-Host "Done. Lambda: $QueueDepthLambdaName | AI/Messaging/Video=TargetTracking (Video=BacklogCount)" -ForegroundColor Green

# ------------------------------------------------------------------------------
# Optional: grant SSM PutParameter to current caller (IAM user)
# ------------------------------------------------------------------------------
if ($GrantSsmPutToCaller) {
    $callerArn = (aws sts get-caller-identity --query Arn --output text 2>$null)
    $iamUserName = $null
    if ($SsmPutGrantUser) { $iamUserName = $SsmPutGrantUser.Trim() } elseif ($callerArn -match 'arn:aws:iam::\d+:user/(.+)$') { $iamUserName = $Matches[1] }
    if ($iamUserName) {
        Write-Host "[+IAM] Granting SSM PutParameter to user '$iamUserName' (for /academy/workers/env)..." -ForegroundColor Cyan
        $ssmPolicyPath = Join-Path $AsgInfra "iam_policy_ssm_put_workers_env.json"
        if (Test-Path $ssmPolicyPath) {
            $ssmPolicyJson = (Get-Content $ssmPolicyPath -Raw) -replace '\{\{Region\}\}', $Region -replace '\{\{AccountId\}\}', $AccountId
            $ssmPolicyFile = Join-Path $RepoRoot "iam_ssm_put_temp.json"
            [System.IO.File]::WriteAllText($ssmPolicyFile, $ssmPolicyJson, $utf8NoBom)
            $ssmPolicyUri = "file://$($ssmPolicyFile -replace '\\','/' -replace ' ', '%20')"
            $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            aws iam put-user-policy --user-name $iamUserName --policy-name academy-ssm-put-workers-env --policy-document $ssmPolicyUri 2>$null
            if ($LASTEXITCODE -eq 0) { Write-Host "      User $iamUserName now has ssm:PutParameter on /academy/workers/env." -ForegroundColor Gray } else { Write-Host "      (Need IAM permission put-user-policy; run as admin or add policy manually.)" -ForegroundColor Yellow }
            $ErrorActionPreference = $ea
            Remove-Item $ssmPolicyFile -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "      Caller is not an IAM user and -SsmPutGrantUser not set; skip. Use -SsmPutGrantUser admin97 to grant that user." -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------------------------
# Optional: upload .env to SSM (Windows encoding/path safe)
# ------------------------------------------------------------------------------
if ($UploadEnvToSsm) {
    $uploadScript = Join-Path $ScriptRoot "upload_env_to_ssm.ps1"
    if (Test-Path $uploadScript) {
        Write-Host "[+SSM] Uploading .env to /academy/workers/env..." -ForegroundColor Cyan
        & $uploadScript -RepoRoot $RepoRoot -Region $Region
        if ($LASTEXITCODE -ne 0) { Write-Host "      SSM upload failed. Run: .\scripts\upload_env_to_ssm.ps1" -ForegroundColor Yellow }
    } else {
        $envPath = Join-Path $RepoRoot ".env"
        if (Test-Path $envPath) { Write-Host "      upload_env_to_ssm.ps1 not found; skip. Run: .\scripts\upload_env_to_ssm.ps1" -ForegroundColor Yellow }
        else { Write-Host "      .env not found; skip SSM upload. Run: .\scripts\upload_env_to_ssm.ps1 after adding .env" -ForegroundColor Yellow }
    }
}

# ------------------------------------------------------------------------------
# Optional: attach SSM+ECR inline policy to EC2 role
# ------------------------------------------------------------------------------
$ec2PolicyPath = Join-Path $AsgInfra "iam_policy_ec2_worker.json"
if ($AttachEc2Policy -and (Test-Path $ec2PolicyPath)) {
    Write-Host "[+IAM] Attaching SSM+ECR policy to role $IamInstanceProfileName..." -ForegroundColor Cyan
    $policyDocUri = "file://$($ec2PolicyPath -replace '\\','/' -replace ' ', '%20')"
    $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    aws iam put-role-policy --role-name $IamInstanceProfileName --policy-name academy-workers-ssm-ecr --policy-document $policyDocUri 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "      Role $IamInstanceProfileName policy academy-workers-ssm-ecr attached." -ForegroundColor Gray } else { Write-Host "      (Role name may differ from instance profile; attach infra/worker_asg/iam_policy_ec2_worker.json manually.)" -ForegroundColor Yellow }
    $ErrorActionPreference = $ea
}

Write-Host "If ECR images missing: .\scripts\build_and_push_ecr.ps1" -ForegroundColor Yellow
Write-Host "Prevent ASG worker stop loop: .\scripts\remove_ec2_stop_from_worker_role.ps1" -ForegroundColor Gray
