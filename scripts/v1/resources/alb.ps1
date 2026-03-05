# ALB + Target Group + Listener (Step D). API 진입점. EIP 제거.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Ensure-ALB {
    if ($script:PlanMode) { return }
    if (-not $script:ApiAlbName) { return }
    Write-Step "Ensure ALB $($script:ApiAlbName)"
    $subnets = @($script:PublicSubnets | Where-Object { $_ })
    if (-not $subnets -or $subnets.Count -eq 0) { throw "Public subnets required for ALB" }
    $r = Invoke-AwsJson @("elbv2", "describe-load-balancers", "--names", $script:ApiAlbName, "--region", $script:Region, "--output", "json")
    if ($r -and $r.LoadBalancers -and $r.LoadBalancers.Count -gt 0) {
        $script:ApiAlbArn = $r.LoadBalancers[0].LoadBalancerArn
        $script:ApiBaseUrl = "http://$($r.LoadBalancers[0].DNSName)"
        Write-Ok "ALB exists $($script:ApiAlbArn)"
        return
    }
    $subnetIds = @($subnets)
    $args = @("elbv2", "create-load-balancer", "--name", $script:ApiAlbName, "--subnets") + $subnetIds + @("--scheme", "internet-facing", "--type", "application", "--tags", "Key=Project,Value=academy", "--region", $script:Region, "--output", "json")
    $create = Invoke-AwsJson $args
    $script:ApiAlbArn = $create.LoadBalancers[0].LoadBalancerArn
    $script:ApiBaseUrl = "http://$($create.LoadBalancers[0].DNSName)"
    Write-Ok "ALB created $script:ApiAlbArn"
    $script:ChangesMade = $true
}

function Ensure-TargetGroup {
    if ($script:PlanMode) { return }
    if (-not $script:ApiAlbName -or -not $script:ApiTargetGroupName) { return }
    Write-Step "Ensure Target Group $($script:ApiTargetGroupName)"
    $r = Invoke-AwsJson @("elbv2", "describe-target-groups", "--names", $script:ApiTargetGroupName, "--region", $script:Region, "--output", "json")
    if ($r -and $r.TargetGroups -and $r.TargetGroups.Count -gt 0) {
        $script:ApiTargetGroupArn = $r.TargetGroups[0].TargetGroupArn
        $tgArn = $script:ApiTargetGroupArn
        $attrs = Invoke-AwsJson @("elbv2", "describe-target-group-attributes", "--target-group-arn", $tgArn, "--region", $script:Region, "--output", "json")
        $currentPath = ($attrs.Attributes | Where-Object { $_.Key -eq "health_check.path" } | Select-Object -First 1).Value
        $wantedPath = $script:ApiHealthPath.TrimStart('/')
        if ($currentPath -and $currentPath.TrimStart('/') -ne $wantedPath) {
            Invoke-Aws @("elbv2", "modify-target-group-attributes", "--target-group-arn", $tgArn, "--attributes", "Key=health_check.path,Value=/$wantedPath", "--region", $script:Region) -ErrorMessage "modify TG health_check.path" | Out-Null
            Write-Ok "Target Group health check path updated to $($script:ApiHealthPath)"
            $script:ChangesMade = $true
        }
        Write-Ok "Target Group exists"
        return
    }
    $create = Invoke-AwsJson @("elbv2", "create-target-group", "--name", $script:ApiTargetGroupName, "--protocol", "HTTP", "--port", "8000", "--vpc-id", $script:VpcId, "--target-type", "instance", "--health-check-path", $script:ApiHealthPath, "--health-check-interval-seconds", "30", "--healthy-threshold-count", "2", "--unhealthy-threshold-count", "3", "--region", $script:Region, "--output", "json")
    $script:ApiTargetGroupArn = $create.TargetGroups[0].TargetGroupArn
    Write-Ok "Target Group created $script:ApiTargetGroupArn"
    $script:ChangesMade = $true
}

function Ensure-Listener {
    if ($script:PlanMode) { return }
    if (-not $script:ApiAlbArn -or -not $script:ApiTargetGroupArn) { return }
    Write-Step "Ensure Listener (HTTP 80)"
    $r = Invoke-AwsJson @("elbv2", "describe-listeners", "--load-balancer-arn", $script:ApiAlbArn, "--region", $script:Region, "--output", "json")
    $listener = $r.Listeners | Where-Object { $_.Port -eq 80 } | Select-Object -First 1
    if ($listener) {
        Write-Ok "Listener port 80 exists"
        return
    }
    $defaultAction = "Type=forward,TargetGroupArn=$script:ApiTargetGroupArn"
    Invoke-Aws @("elbv2", "create-listener", "--load-balancer-arn", $script:ApiAlbArn, "--protocol", "HTTP", "--port", "80", "--default-actions", $defaultAction, "--region", $script:Region) -ErrorMessage "create-listener" | Out-Null
    Write-Ok "Listener created (80 -> TargetGroup)"
    $script:ChangesMade = $true
}

function Ensure-ALBStack {
    Ensure-ALB
    Ensure-TargetGroup
    Ensure-Listener
}
