# Network: VPC/Subnets existence check. Verify match with SSOT only. Changes manual.
function Ensure-NetworkVpc {
    Write-Step "Network VPC"
    $vpc = Invoke-AwsJson @("ec2", "describe-vpcs", "--vpc-ids", $script:VpcId, "--region", $script:Region, "--output", "json")
    if (-not $vpc -or -not $vpc.Vpcs -or $vpc.Vpcs.Count -eq 0) {
        throw "Preflight FAIL: VPC $($script:VpcId) not found"
    }
    Write-Ok "VPC $($script:VpcId)"
}

function Confirm-SubnetsMatchSSOT {
    $subnets = Invoke-AwsJson @("ec2", "describe-subnets", "--subnet-ids", $script:PublicSubnets, "--region", $script:Region, "--output", "json")
    if (-not $subnets -or -not $subnets.Subnets -or $subnets.Subnets.Count -ne $script:PublicSubnets.Count) {
        Write-Warn "Public subnets count or IDs mismatch SSOT"
        return
    }
    $igw = Invoke-AwsJson @("ec2", "describe-internet-gateways", "--filters", "Name=attachment.vpc-id,Values=$($script:VpcId)", "--region", $script:Region, "--output", "json")
    if ($igw -and $igw.InternetGateways -and $igw.InternetGateways.Count -gt 0) {
        Write-Ok "Subnets + IGW for $($script:VpcId)"
    } else {
        Write-Warn "No IGW attached to VPC"
    }
}
