# ALB + Target Group + Listener (Step D). API 진입점. EIP 제거.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Ensure-ALBSecurityGroup {
    if ($script:PlanMode -or -not $script:ApiAlbArn) { return }
    $lb = Invoke-AwsJson @("elbv2", "describe-load-balancers", "--load-balancer-arns", $script:ApiAlbArn, "--region", $script:Region, "--output", "json")
    if (-not $lb.LoadBalancers -or $lb.LoadBalancers.Count -eq 0) { return }
    $sgIds = $lb.LoadBalancers[0].SecurityGroups
    if (-not $sgIds -or $sgIds.Count -eq 0) { return }
    foreach ($sgId in $sgIds) {
        $desc = Invoke-AwsJson @("ec2", "describe-security-groups", "--group-ids", $sgId, "--region", $script:Region, "--output", "json")
        if (-not $desc.SecurityGroups -or $desc.SecurityGroups.Count -eq 0) { continue }
        $perms = $desc.SecurityGroups[0].IpPermissions
        $has80 = $false
        $has443 = $false
        foreach ($p in $perms) {
            if ($p.FromPort -eq 80 -and $p.ToPort -eq 80) {
                foreach ($ip in @($p.IpRanges)) { if ($ip.CidrIp -eq "0.0.0.0/0") { $has80 = $true; break } }
            }
            if ($p.FromPort -eq 443 -and $p.ToPort -eq 443) {
                foreach ($ip in @($p.IpRanges)) { if ($ip.CidrIp -eq "0.0.0.0/0") { $has443 = $true; break } }
            }
        }
        if (-not $has80) {
            Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--protocol", "tcp", "--port", "80", "--cidr", "0.0.0.0/0", "--region", $script:Region) -ErrorMessage "ALB SG 80" | Out-Null
            Write-Ok "ALB SG $sgId: added 80 from 0.0.0.0/0"
            $script:ChangesMade = $true
        }
        if (-not $has443) {
            Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--protocol", "tcp", "--port", "443", "--cidr", "0.0.0.0/0", "--region", $script:Region) -ErrorMessage "ALB SG 443" | Out-Null
            Write-Ok "ALB SG $sgId: added 443 from 0.0.0.0/0"
            $script:ChangesMade = $true
        }
    }
}

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
        Ensure-ALBSecurityGroup
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
        $currentPath = $r.TargetGroups[0].HealthCheckPath
        $wantedPath = $script:ApiHealthPath.TrimStart('/')
        if ($currentPath -and $currentPath.TrimStart('/') -ne $wantedPath) {
            Invoke-AwsJson @("elbv2", "modify-target-group", "--target-group-arn", $tgArn, "--health-check-path", $script:ApiHealthPath, "--region", $script:Region, "--output", "json") | Out-Null
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
