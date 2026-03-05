# API: ALB + ASG (Step D). EIP 제거. Private subnet + sg-app, health /health.
$ErrorActionPreference = "Stop"

# EC2 부팅 시 Docker 설치 → ECR Pull → Django 컨테이너 8000 포트 실행. API_IMAGE_URI 등은 런타임 치환.
function Get-ApiLaunchTemplateUserData {
    param([string]$ApiImageUri, [string]$Region, [string]$SsmApiEnvParam)
    if (-not $ApiImageUri -or -not $Region) { return "" }
    $ecrHost = $ApiImageUri.Split("/")[0]
    $script = @"
#!/bin/bash
set -e
export AWS_REGION="$Region"
# 1) Docker 설치 및 기동 (Amazon Linux 2 / AL2023)
if command -v dnf &>/dev/null; then
  dnf install -y docker
else
  yum install -y docker
fi
systemctl start docker
systemctl enable docker
# 2) ECR 로그인 및 이미지 Pull
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ecrHost
docker pull $ApiImageUri
# 3) API env (SSM, 선택) -> env 파일로 저장
API_ENV_FILE=""
if [ -n "$SsmApiEnvParam" ]; then
  ENV_JSON="`$(aws ssm get-parameter --name "$SsmApiEnvParam" --with-decryption --query Parameter.Value --output text --region $Region 2>/dev/null)" || true
  if [ -n "`$ENV_JSON" ]; then
    mkdir -p /opt
    echo "`$ENV_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(k+'='+str(v)) for k,v in d.items()]" 2>/dev/null > /opt/api.env || true
    [ -s /opt/api.env ] && API_ENV_FILE="--env-file /opt/api.env"
  fi
fi
# 4) Django 컨테이너 8000 포트로 실행
docker run -d --restart unless-stopped -p 8000:8000 `$API_ENV_FILE $ApiImageUri
"@
    return $script.Trim()
}

# ECR academy-api 리포지터리에서 최신 이미지 태그 1개 반환. (latest 제외 우선, 없으면 latest 사용)
function Get-LatestApiImageUri {
    $repo = $script:EcrApiRepo
    if (-not $repo) { return $null }
    $list = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $repo, "--region", $script:Region, "--output", "json") 2>$null
    if (-not $list -or -not $list.imageDetails -or $list.imageDetails.Count -eq 0) { return $null }
    $nonLatest = @($list.imageDetails | Where-Object { $_.imageTags -and ($_.imageTags | Where-Object { $_ -ne "latest" }) } | ForEach-Object {
        $tag = ($_.imageTags | Where-Object { $_ -ne "latest" } | Select-Object -First 1)
        if ($tag) { [PSCustomObject]@{ Tag = $tag; Pushed = $_.imagePushedAt } }
    } | Where-Object { $_ })
    $tagToUse = $null
    if ($nonLatest.Count -gt 0) {
        $latest = $nonLatest | Sort-Object { $_.Pushed } -Descending | Select-Object -First 1
        $tagToUse = $latest.Tag
    } else {
        $withLatest = $list.imageDetails | Where-Object { $_.imageTags -and ($_.imageTags -contains "latest") } | Select-Object -First 1
        if ($withLatest) { $tagToUse = "latest" }
    }
    if (-not $tagToUse) { return $null }
    $acc = $script:AccountId
    $reg = $script:Region
    return "${acc}.dkr.ecr.${reg}.amazonaws.com/${repo}:$tagToUse"
}

function Get-APIASGInstanceIds {
    $r = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:ApiASGName, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.AutoScalingGroups -or $r.AutoScalingGroups.Count -eq 0) { return @() }
    $instances = $r.AutoScalingGroups[0].Instances | Where-Object { $_.LifecycleState -eq "InService" -or $_.LifecycleState -eq "Pending" } | ForEach-Object { $_.InstanceId }
    return @($instances)
}

function Test-APIHealth200 {
    param([string]$BaseUrl)
    $url = if ($BaseUrl) { $BaseUrl.TrimEnd('/') } else { $script:ApiBaseUrl }
    if (-not $url) { return $false }
    try {
        $r = Invoke-WebRequest -Uri "$url/$($script:ApiHealthPath.TrimStart('/'))" -UseBasicParsing -TimeoutSec 10
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

# Ensure API Launch Template: drift on AMI, SG, UserData, InstanceProfile → new version.
function Ensure-API-LaunchTemplate {
    if ($script:PlanMode) { return @{ LtId = $null; Updated = $false } }
    $ltName = $script:ApiLaunchTemplateName
    $currentSg = $script:ApiSecurityGroupId
    if (-not $currentSg -and $script:SecurityGroupApp) { $currentSg = $script:SecurityGroupApp }
    if (-not $currentSg) { throw "API SG required (SecurityGroupApp or api.securityGroupId)" }
    $r = Invoke-AwsJson @("ec2", "describe-launch-templates", "--launch-template-names", $ltName, "--region", $script:Region, "--output", "json")
    $currentAmi = $script:ApiAmiId
    $currentType = $script:ApiInstanceType
    $currentProfile = $script:ApiInstanceProfile
    $userDataRaw = $script:ApiUserData
    if (-not $userDataRaw -or $userDataRaw.Trim() -eq "") {
        $apiUri = Get-LatestApiImageUri
        if ($apiUri) {
            $userDataRaw = Get-ApiLaunchTemplateUserData -ApiImageUri $apiUri -Region $script:Region -SsmApiEnvParam $script:SsmApiEnv
        } else {
            Write-Warn "No API image in ECR ($($script:EcrApiRepo)); Launch Template UserData left empty. Push academy-api image and re-run deploy."
        }
    }
    $currentUserData = if ($userDataRaw) { [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($userDataRaw)) } else { "" }

    $tagSpec = "TagSpecifications=[{ResourceType=instance,Tags=[{Key=$($script:ApiInstanceTagKey),Value=$($script:ApiInstanceTagValue)}]}]"
    $baseData = "ImageId=$currentAmi,InstanceType=$currentType,SecurityGroupIds=$currentSg,IamInstanceProfile={Name=$currentProfile},$tagSpec"
    if ($currentUserData) { $baseData += ",UserData=$currentUserData" }

    if (-not $r -or -not $r.LaunchTemplates -or $r.LaunchTemplates.Count -eq 0) {
        $create = Invoke-AwsJson @("ec2", "create-launch-template", "--launch-template-name", $ltName, "--version-description", "SSOT v1", "--launch-template-data", $baseData, "--region", $script:Region, "--output", "json")
        if (-not $create -or -not $create.LaunchTemplate) { throw "create-launch-template failed for $ltName" }
        Write-Ok "LaunchTemplate $ltName created"
        $script:ChangesMade = $true
        return @{ LtId = $create.LaunchTemplate.LaunchTemplateId; Updated = $true }
    }

    $ltId = $r.LaunchTemplates[0].LaunchTemplateId
    $verR = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $ltId, "--versions", '$Default', "--region", $script:Region, "--output", "json")
    if (-not $verR -or -not $verR.LaunchTemplateVersions -or $verR.LaunchTemplateVersions.Count -eq 0) { return @{ LtId = $ltId; Updated = $false } }
    $data = $verR.LaunchTemplateVersions[0].LaunchTemplateData
    $actualAmi = if ($data.PSObject.Properties['ImageId']) { $data.ImageId } else { $null }
    $actualType = if ($data.PSObject.Properties['InstanceType']) { $data.InstanceType } else { $null }
    $actualSg = $null
    if ($data.PSObject.Properties['SecurityGroupIds'] -and $data.SecurityGroupIds -and $data.SecurityGroupIds.Count -gt 0) { $actualSg = $data.SecurityGroupIds[0] }
    elseif ($data.PSObject.Properties['SecurityGroupIds']) { $actualSg = $data.SecurityGroupIds }
    $actualProfile = $null
    if ($data.PSObject.Properties['IamInstanceProfile'] -and $data.IamInstanceProfile) { $actualProfile = $data.IamInstanceProfile.Name }
    $actualUserData = if ($data.PSObject.Properties['UserData']) { $data.UserData } else { "" }

    $drift = ($actualAmi -ne $currentAmi) -or ($actualType -ne $currentType) -or ($actualSg -ne $currentSg) -or ($actualProfile -ne $currentProfile) -or ($actualUserData -ne $currentUserData)
    if (-not $drift) { return @{ LtId = $ltId; Updated = $false } }

    $newVer = Invoke-AwsJson @("ec2", "create-launch-template-version", "--launch-template-id", $ltId, "--version-description", "SSOT v1 drift", "--launch-template-data", $baseData, "--region", $script:Region, "--output", "json")
    if (-not $newVer -or -not $newVer.LaunchTemplateVersion) { throw "create-launch-template-version failed" }
    $newVersion = $newVer.LaunchTemplateVersion.VersionNumber
    Invoke-Aws @("ec2", "modify-launch-template", "--launch-template-id", $ltId, "--default-version", $newVersion.ToString(), "--region", $script:Region) -ErrorMessage "modify-launch-template set default failed" | Out-Null
    Write-Ok "LaunchTemplate $ltName new default version $newVersion (drift)"
    $script:ChangesMade = $true
    return @{ LtId = $ltId; Updated = $true }
}

# Ensure API ASG: create if missing; capacity drift → update; LT updated → instance-refresh.
function Ensure-API-ASG {
    Write-Step "Ensure API ASG $($script:ApiASGName)"
    if ($script:PlanMode) { Write-Ok "Ensure-API-ASG skipped (Plan)"; return }

    $ltResult = Ensure-API-LaunchTemplate
    $subnets = @($script:PrivateSubnets | Where-Object { $_ })
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @($script:PublicSubnets | Where-Object { $_ }) }
    $vpcZone = ($subnets -join ",")
    if (-not $vpcZone) { throw "PublicSubnets or PrivateSubnets empty" }

    $asgList = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $script:Region, "--output", "json")
    $asgArr = if ($asgList -and $asgList.PSObject.Properties['AutoScalingGroups']) { @($asgList.AutoScalingGroups) } else { @() }
    $asg = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $script:ApiASGName } | Select-Object -First 1

    if (-not $asg) {
        $ltSpec = "LaunchTemplateId=$($ltResult.LtId),Version=`$Latest"
        Invoke-Aws @("autoscaling", "create-auto-scaling-group", "--auto-scaling-group-name", $script:ApiASGName, "--launch-template", $ltSpec, "--min-size", $script:ApiASGMinSize.ToString(), "--max-size", $script:ApiASGMaxSize.ToString(), "--desired-capacity", $script:ApiASGDesiredCapacity.ToString(), "--vpc-zone-identifier", $vpcZone, "--region", $script:Region) -ErrorMessage "create-auto-scaling-group API ASG failed" | Out-Null
        Write-Ok "ASG $($script:ApiASGName) created"
        $script:ChangesMade = $true
        return
    }

    $capacityDrift = ($asg.MinSize -ne $script:ApiASGMinSize) -or ($asg.MaxSize -ne $script:ApiASGMaxSize) -or ($asg.DesiredCapacity -ne $script:ApiASGDesiredCapacity)
    if ($capacityDrift) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $script:ApiASGName, "--min-size", $script:ApiASGMinSize.ToString(), "--max-size", $script:ApiASGMaxSize.ToString(), "--desired-capacity", $script:ApiASGDesiredCapacity.ToString(), "--region", $script:Region) -ErrorMessage "update-auto-scaling-group API ASG failed" | Out-Null
        Write-Ok "ASG $($script:ApiASGName) capacity updated"
        $script:ChangesMade = $true
    }
    if ($ltResult.Updated) {
        $minHealthy = if ($script:ApiInstanceRefreshMinHealthyPercentage -gt 0) { $script:ApiInstanceRefreshMinHealthyPercentage } else { 100 }
        $warmup = if ($script:ApiInstanceRefreshInstanceWarmup -gt 0) { $script:ApiInstanceRefreshInstanceWarmup } else { 300 }
        $prefs = "{`"MinHealthyPercentage`":$minHealthy,`"InstanceWarmup`":$warmup}"
        Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:ApiASGName, "--preferences", $prefs, "--region", $script:Region) -ErrorMessage "start-instance-refresh API ASG failed" | Out-Null
        Write-Ok "ASG $($script:ApiASGName) instance-refresh started (MinHealthyPercentage=$minHealthy, InstanceWarmup=${warmup}s)"
        $script:ChangesMade = $true
    }
    if (-not $capacityDrift -and -not $ltResult.Updated) {
        Write-Ok "ASG $($script:ApiASGName) idempotent"
    }
    if ($script:ApiTargetGroupArn) {
        $attached = Invoke-AwsJson @("autoscaling", "describe-load-balancer-target-groups", "--auto-scaling-group-name", $script:ApiASGName, "--region", $script:Region, "--output", "json")
        $hasTg = $attached.LoadBalancerTargetGroups | Where-Object { $_.TargetGroupARN -eq $script:ApiTargetGroupArn } | Select-Object -First 1
        if (-not $hasTg) {
            Invoke-Aws @("autoscaling", "attach-load-balancer-target-groups", "--auto-scaling-group-name", $script:ApiASGName, "--target-group-arns", $script:ApiTargetGroupArn, "--region", $script:Region) -ErrorMessage "attach target group to API ASG" | Out-Null
            Write-Ok "API ASG attached to Target Group"
            $script:ChangesMade = $true
        }
    }
}

# Wait for ASG instance then health on ApiBaseUrl (ALB DNS). No EIP.
function Ensure-API-Instance {
    Write-Step "Ensure API Instance (ALB health)"
    if ($script:PlanMode) { Write-Ok "Ensure-API-Instance skipped (Plan)"; return }

    $maxWait = if ($script:SkipApiSSMWait) { 30 } else { 300 }
    $elapsed = 0
    $instanceId = $null
    while ($elapsed -lt $maxWait) {
        $ids = @(Get-APIASGInstanceIds)
        if ($ids -and $ids.Count -gt 0) { $instanceId = [string]$ids[0]; break }
        Write-Host "  Waiting for API ASG instance..." -ForegroundColor Gray
        Start-Sleep -Seconds 15
        $elapsed += 15
    }
    if (-not $instanceId) {
        if ($script:SkipApiSSMWait) {
            Write-Warn "No API ASG instance after ${maxWait}s (-SkipApiSSMWait). Deploy continues; check ASG/ALB manually."
            return
        }
        throw "No API ASG instance after ${maxWait}s"
    }

    if ($script:SkipApiSSMWait) {
        Write-Warn "Skip API SSM wait (-SkipApiSSMWait). Instance $instanceId may not be in SSM yet."
    } else {
        try {
            Wait-SSMOnline -InstanceId $instanceId -Reg $script:Region -TimeoutSec 600
        } catch {
            Write-Warn "SSM wait failed: $_. Instance may not have SSM agent or IAM policy. Continuing to API health check."
        }
    }
    if ($script:ApiBaseUrl) {
        if ($script:SkipApiSSMWait) {
            Write-Warn "Skip API health wait (-SkipApiSSMWait). Check $($script:ApiBaseUrl)/$($script:ApiHealthPath) manually."
        } else {
            try {
                Wait-ApiHealth200 -ApiBaseUrl $script:ApiBaseUrl -TimeoutSec 300
            } catch {
                Write-Warn "API health 200 timeout. $_. Deploy continues; check $($script:ApiBaseUrl)/$($script:ApiHealthPath) and ASG/ALB manually."
                $minHealthy = if ($script:ApiInstanceRefreshMinHealthyPercentage -gt 0) { $script:ApiInstanceRefreshMinHealthyPercentage } else { 100 }
                $warmup = if ($script:ApiInstanceRefreshInstanceWarmup -gt 0) { $script:ApiInstanceRefreshInstanceWarmup } else { 300 }
                $prefs = "{`"MinHealthyPercentage`":$minHealthy,`"InstanceWarmup`":$warmup}"
                Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:ApiASGName, "--preferences", $prefs, "--region", $script:Region) -ErrorMessage "start-instance-refresh failed" 2>$null | Out-Null
                $script:ChangesMade = $true
            }
        }
    } else {
        Write-Ok "API instance $instanceId ready (ApiBaseUrl not set)"
    }
}

function Confirm-APIHealth {
    Write-Step "API health"
    if ($script:PlanMode) { Write-Ok "API check skipped (Plan)"; return }
    $url = $script:ApiBaseUrl
    if (-not $url) { Write-Warn "ApiBaseUrl not set"; return }
    try {
        $path = $script:ApiHealthPath.TrimStart('/')
        $r = Invoke-WebRequest -Uri "$($url.TrimEnd('/'))/$path" -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) { Write-Ok "GET $url/$path -> 200" } else { throw "status=$($r.StatusCode)" }
    } catch {
        Write-Fail "API health check failed: $_"
        throw "API health check failed: $_"
    }
}

function Ensure-API {
    Ensure-API-ASG
    Ensure-API-Instance
}
