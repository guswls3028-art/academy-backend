# Network: 2-tier VPC Ensure (Step C). Create if missing; converge routes and SG rules.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
# VPC 10.0.0.0/16, Public 2 + Private 2, IGW, NAT 1, RTs, SG (app/batch/data).
$ErrorActionPreference = "Stop"

function Get-FirstTwoAzs {
    $r = Invoke-AwsJson @("ec2", "describe-availability-zones", "--region", $script:Region, "--output", "json")
    $zones = @($r.AvailabilityZones | Where-Object { $_.State -eq "available" } | Select-Object -First 2 | ForEach-Object { $_.ZoneName })
    if (-not $zones -or $zones.Count -lt 2) { throw "Need at least 2 AZs in $($script:Region)" }
    return $zones
}

function Get-VpcByTagOrId {
    if ($script:VpcId) {
        $v = Invoke-AwsJson @("ec2", "describe-vpcs", "--vpc-ids", $script:VpcId, "--region", $script:Region, "--output", "json")
        if ($v -and $v.Vpcs -and $v.Vpcs.Count -gt 0) { return $v.Vpcs[0] }
    }
    $r = Invoke-AwsJson @("ec2", "describe-vpcs", "--filters", "Name=tag:Name,Values=$($script:VpcName)", "Name=tag:Project,Values=academy", "--region", $script:Region, "--output", "json")
    if ($r -and $r.Vpcs -and $r.Vpcs.Count -gt 0) { return $r.Vpcs[0] }
    return $null
}

function Ensure-Vpc {
    $vpc = Get-VpcByTagOrId
    if ($vpc) {
        $script:VpcId = $vpc.VpcId
        Write-Ok "VPC $($script:VpcId) exists"
        return
    }
    if ($script:PlanMode) { Write-Ok "VPC would be created"; return }
    $create = Invoke-AwsJson @("ec2", "create-vpc", "--cidr-block", $script:VpcCidr, "--tag-specifications", "ResourceType=vpc,Tags=[{Key=Name,Value=$($script:VpcName)},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
    $script:VpcId = $create.Vpc.VpcId
    Invoke-Aws @("ec2", "modify-vpc-attribute", "--vpc-id", $script:VpcId, "--enable-dns-hostnames", "--region", $script:Region) -ErrorMessage "enable-dns-hostnames" | Out-Null
    Invoke-Aws @("ec2", "modify-vpc-attribute", "--vpc-id", $script:VpcId, "--enable-dns-support", "--region", $script:Region) -ErrorMessage "enable-dns-support" | Out-Null
    Write-Ok "VPC $($script:VpcId) created"
    $script:ChangesMade = $true
}

function Get-SubnetsByNames { param([string[]]$Names)
    $r = Invoke-AwsJson @("ec2", "describe-subnets", "--filters", "Name=vpc-id,Values=$($script:VpcId)", "--region", $script:Region, "--output", "json")
    $subnets = @($r.Subnets | Where-Object { $_.Tags | Where-Object { $_.Key -eq "Name" -and $Names -contains $_.Value } })
    return $subnets
}

function Ensure-Subnets {
    param([string[]]$Azs)
    $pub1 = "academy-v1-public-a"; $pub2 = "academy-v1-public-b"; $prv1 = "academy-v1-private-a"; $prv2 = "academy-v1-private-b"
    $existing = Get-SubnetsByNames -Names @($pub1,$pub2,$prv1,$prv2)
    $byName = @{}
    foreach ($s in $existing) {
        $name = ($s.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
        if ($name) { $byName[$name] = $s }
    }
    $publicIds = @(); $privateIds = @()
    foreach ($i in 0..1) {
        $name = @($pub1,$pub2)[$i]; $cidr = @($script:PublicSubnetCidr1, $script:PublicSubnetCidr2)[$i]; $az = $Azs[$i]
        if ($byName[$name]) {
            $publicIds += $byName[$name].SubnetId
            continue
        }
        if ($script:PlanMode) { $publicIds += "(would create $name)"; continue }
        $c = Invoke-AwsJson @("ec2", "create-subnet", "--vpc-id", $script:VpcId, "--cidr-block", $cidr, "--availability-zone", $az, "--tag-specifications", "ResourceType=subnet,Tags=[{Key=Name,Value=$name},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
        $publicIds += $c.Subnet.SubnetId
        Invoke-Aws @("ec2", "modify-subnet-attribute", "--subnet-id", $c.Subnet.SubnetId, "--map-public-ip-on-launch", "--region", $script:Region) -ErrorMessage "map-public-ip" | Out-Null
        $script:ChangesMade = $true
    }
    foreach ($i in 0..1) {
        $name = @($prv1,$prv2)[$i]; $cidr = @($script:PrivateSubnetCidr1, $script:PrivateSubnetCidr2)[$i]; $az = $Azs[$i]
        if ($byName[$name]) {
            $privateIds += $byName[$name].SubnetId
            continue
        }
        if ($script:PlanMode) { $privateIds += "(would create $name)"; continue }
        $c = Invoke-AwsJson @("ec2", "create-subnet", "--vpc-id", $script:VpcId, "--cidr-block", $cidr, "--availability-zone", $az, "--tag-specifications", "ResourceType=subnet,Tags=[{Key=Name,Value=$name},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
        $privateIds += $c.Subnet.SubnetId
        $script:ChangesMade = $true
    }
    $script:PublicSubnets = @($publicIds | Where-Object { $_ -match '^subnet-' })
    $script:PrivateSubnets = @($privateIds | Where-Object { $_ -match '^subnet-' })
    if ($script:PublicSubnets.Count -gt 0) { Write-Ok "Public subnets: $($script:PublicSubnets -join ', ')" }
    if ($script:PrivateSubnets.Count -gt 0) { Write-Ok "Private subnets: $($script:PrivateSubnets -join ', ')" }
}

function Ensure-InternetGateway {
    $r = Invoke-AwsJson @("ec2", "describe-internet-gateways", "--filters", "Name=attachment.vpc-id,Values=$($script:VpcId)", "--region", $script:Region, "--output", "json")
    if ($r -and $r.InternetGateways -and $r.InternetGateways.Count -gt 0) {
        Write-Ok "IGW attached"
        return $r.InternetGateways[0].InternetGatewayId
    }
    if ($script:PlanMode) { return "igw-plan" }
    $c = Invoke-AwsJson @("ec2", "create-internet-gateway", "--tag-specifications", "ResourceType=internet-gateway,Tags=[{Key=Name,Value=academy-v1-igw},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
    $igwId = $c.InternetGateway.InternetGatewayId
    Invoke-Aws @("ec2", "attach-internet-gateway", "--vpc-id", $script:VpcId, "--internet-gateway-id", $igwId, "--region", $script:Region) -ErrorMessage "attach-igw" | Out-Null
    Write-Ok "IGW $igwId created and attached"
    $script:ChangesMade = $true
    return $igwId
}

function Ensure-NatGateway {
    $r = Invoke-AwsJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$($script:VpcId)", "Name=state,Values=available", "--region", $script:Region, "--output", "json")
    if ($r -and $r.NatGateways -and $r.NatGateways.Count -gt 0) {
        $script:NatGatewayId = $r.NatGateways[0].NatGatewayId
        Write-Ok "NAT Gateway $($script:NatGatewayId) available"
        return $r.NatGateways[0].NatGatewayAddresses[0].AllocationId
    }
    if ($script:PlanMode) { return "eipalloc-plan" }
    $eip = Invoke-AwsJson @("ec2", "allocate-address", "--domain", "vpc", "--tag-specifications", "ResourceType=elastic-ip,Tags=[{Key=Name,Value=academy-v1-nat-eip},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
    $allocId = $eip.AllocationId
    $pubSubnetId = $script:PublicSubnets[0]
    $nat = Invoke-AwsJson @("ec2", "create-nat-gateway", "--subnet-id", $pubSubnetId, "--allocation-id", $allocId, "--tag-specifications", "ResourceType=natgateway,Tags=[{Key=Name,Value=academy-v1-nat},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
    $script:NatGatewayId = $nat.NatGateway.NatGatewayId
    Write-Ok "NAT Gateway $($script:NatGatewayId) created; waiting available..."
    $elapsed = 0
    while ($elapsed -lt 300) {
        $d = Invoke-AwsJson @("ec2", "describe-nat-gateways", "--nat-gateway-ids", $script:NatGatewayId, "--region", $script:Region, "--output", "json")
        $state = $d.NatGateways[0].State
        if ($state -eq "available") { Write-Ok "NAT available"; break }
        Start-Sleep -Seconds 15; $elapsed += 15
    }
    if ($state -ne "available") { throw "NAT Gateway did not become available in time" }
    $script:ChangesMade = $true
    return $allocId
}

function Ensure-RouteTables { param([string]$IgwId, [string]$NatAllocId)
    $r = Invoke-AwsJson @("ec2", "describe-route-tables", "--filters", "Name=vpc-id,Values=$($script:VpcId)", "--region", $script:Region, "--output", "json")
    $rts = @($r.RouteTables)
    $publicRt = $rts | Where-Object { $_.Tags | Where-Object { $_.Key -eq "Name" -and $_.Value -eq "academy-v1-public-rt" } } | Select-Object -First 1
    $privateRt = $rts | Where-Object { $_.Tags | Where-Object { $_.Key -eq "Name" -and $_.Value -eq "academy-v1-private-rt" } } | Select-Object -First 1
    if (-not $publicRt -and -not $script:PlanMode) {
        $c = Invoke-AwsJson @("ec2", "create-route-table", "--vpc-id", $script:VpcId, "--tag-specifications", "ResourceType=route-table,Tags=[{Key=Name,Value=academy-v1-public-rt},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
        $publicRt = $c.RouteTable
        Invoke-Aws @("ec2", "create-route", "--route-table-id", $publicRt.RouteTableId, "--destination-cidr-block", "0.0.0.0/0", "--gateway-id", $IgwId, "--region", $script:Region) -ErrorMessage "public route" | Out-Null
        foreach ($subId in $script:PublicSubnets) {
            if ($subId -notmatch '^subnet-') { continue }
            $assoc = Invoke-AwsJson @("ec2", "describe-route-tables", "--filters", "Name=association.subnet-id,Values=$subId", "--region", $script:Region, "--output", "json")
            $assocId = $assoc.RouteTables | ForEach-Object { $_.Associations } | Where-Object { $_.SubnetId -eq $subId -and $_.Main -eq $false } | Select-Object -ExpandProperty RouteTableAssociationId -First 1
            if ($assocId) {
                Invoke-Aws @("ec2", "disassociate-route-table", "--association-id", $assocId, "--region", $script:Region) -ErrorMessage "disassociate-public" 2>$null | Out-Null
            }
            Invoke-Aws @("ec2", "associate-route-table", "--route-table-id", $publicRt.RouteTableId, "--subnet-id", $subId, "--region", $script:Region) -ErrorMessage "associate-public" | Out-Null
        }
        Write-Ok "Public RT created, 0.0.0.0/0 -> IGW"
        $script:ChangesMade = $true
    } elseif ($publicRt) { Write-Ok "Public RT exists" }
    if (-not $privateRt -and -not $script:PlanMode -and $script:NatGatewayId) {
        $c = Invoke-AwsJson @("ec2", "create-route-table", "--vpc-id", $script:VpcId, "--tag-specifications", "ResourceType=route-table,Tags=[{Key=Name,Value=academy-v1-private-rt},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
        $privateRt = $c.RouteTable
        Invoke-Aws @("ec2", "create-route", "--route-table-id", $privateRt.RouteTableId, "--destination-cidr-block", "0.0.0.0/0", "--nat-gateway-id", $script:NatGatewayId, "--region", $script:Region) -ErrorMessage "private route" | Out-Null
        foreach ($subId in $script:PrivateSubnets) {
            if ($subId -notmatch '^subnet-') { continue }
            $assoc = Invoke-AwsJson @("ec2", "describe-route-tables", "--filters", "Name=association.subnet-id,Values=$subId", "--region", $script:Region, "--output", "json")
            $assocId = $assoc.RouteTables | ForEach-Object { $_.Associations } | Where-Object { $_.SubnetId -eq $subId -and $_.Main -eq $false } | Select-Object -ExpandProperty RouteTableAssociationId -First 1
            if ($assocId) {
                Invoke-Aws @("ec2", "disassociate-route-table", "--association-id", $assocId, "--region", $script:Region) -ErrorMessage "disassociate-private" 2>$null | Out-Null
            }
            Invoke-Aws @("ec2", "associate-route-table", "--route-table-id", $privateRt.RouteTableId, "--subnet-id", $subId, "--region", $script:Region) -ErrorMessage "associate-private" | Out-Null
        }
        Write-Ok "Private RT created, 0.0.0.0/0 -> NAT"
        $script:ChangesMade = $true
    } elseif ($privateRt) { Write-Ok "Private RT exists" }
}

function Ensure-SecurityGroups {
    $r = Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$($script:VpcId)", "--region", $script:Region, "--output", "json")
    $byName = @{}
    foreach ($sg in $r.SecurityGroups) {
        if ($sg.GroupName) { $byName[$sg.GroupName] = $sg }
    }

    # 0.0.0.0/0 ALL egress가 이미 있으면 authorize 호출 스킵 (Duplicate 방지, 완전 멱등)
    function Test-SgHasDefaultEgress {
        param([string]$GroupId)
        if (-not $GroupId) { return $false }
        $d = Invoke-AwsJson @("ec2", "describe-security-groups", "--group-ids", $GroupId, "--region", $script:Region, "--output", "json")
        if (-not $d -or -not $d.SecurityGroups -or $d.SecurityGroups.Count -eq 0) { return $false }
        $egress = $d.SecurityGroups[0].IpPermissionsEgress
        if (-not $egress) { return $false }
        foreach ($perm in $egress) {
            $proto = $perm.IpProtocol
            if ($proto -ne "all" -and $proto -ne "-1") { continue }
            $ranges = $perm.IpRanges
            if (-not $ranges) { continue }
            foreach ($ir in $ranges) {
                if ($ir.CidrIp -eq "0.0.0.0/0") { return $true }
            }
        }
        return $false
    }

    # sg-app: inbound 80,443 from 0.0.0.0/0 (ALB in public will hit this); outbound 0.0.0.0/0
    if (-not $byName[$script:SgAppName] -and -not $script:PlanMode) {
        $c = Invoke-AwsJson @("ec2", "create-security-group", "--group-name", $script:SgAppName, "--description", "Academy app (API, workers)", "--vpc-id", $script:VpcId, "--tag-specifications", "ResourceType=security-group,Tags=[{Key=Name,Value=$($script:SgAppName)},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
        $script:SecurityGroupApp = $c.GroupId
        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupApp, "--protocol", "tcp", "--port", "80", "--cidr", "0.0.0.0/0", "--region", $script:Region) -ErrorMessage "sg-app 80" | Out-Null
        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupApp, "--protocol", "tcp", "--port", "443", "--cidr", "0.0.0.0/0", "--region", $script:Region) -ErrorMessage "sg-app 443" | Out-Null
        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupApp, "--protocol", "tcp", "--port", "8000", "--cidr", $script:VpcCidr, "--region", $script:Region) -ErrorMessage "sg-app 8000" | Out-Null
        if (-not (Test-SgHasDefaultEgress -GroupId $script:SecurityGroupApp)) {
            Invoke-Aws @("ec2", "authorize-security-group-egress", "--group-id", $script:SecurityGroupApp, "--protocol", "all", "--cidr", "0.0.0.0/0", "--region", $script:Region) -ErrorMessage "sg-app egress" | Out-Null
        }
        Write-Ok "SG $script:SgAppName created"
        $script:ChangesMade = $true
    } else {
        $script:SecurityGroupApp = $byName[$script:SgAppName].GroupId
        # 기존 sg-app에 8000 from VpcCidr(SSOT) 규칙 보장 — ALB→EC2 헬스체크 허용
        if (-not $script:PlanMode -and $script:VpcCidr) {
            $desc = Invoke-AwsJson @("ec2", "describe-security-groups", "--group-ids", $script:SecurityGroupApp, "--region", $script:Region, "--output", "json")
            $has8000FromVpc = $false
            foreach ($perm in $desc.SecurityGroups[0].IpPermissions) {
                if ($perm.FromPort -eq 8000 -and $perm.ToPort -eq 8000) {
                    foreach ($ir in $perm.IpRanges) {
                        if ($ir.CidrIp -eq $script:VpcCidr) { $has8000FromVpc = $true; break }
                    }
                }
            }
            if (-not $has8000FromVpc) {
                Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupApp, "--protocol", "tcp", "--port", "8000", "--cidr", $script:VpcCidr, "--region", $script:Region) -ErrorMessage "sg-app 8000 from VpcCidr" | Out-Null
                Write-Ok "SG $script:SgAppName: added 8000 from $($script:VpcCidr) (SSOT)"
                $script:ChangesMade = $true
            }
        }
    }
    # sg-batch: outbound 0.0.0.0/0
    if (-not $byName[$script:SgBatchName] -and -not $script:PlanMode) {
        $c = Invoke-AwsJson @("ec2", "create-security-group", "--group-name", $script:SgBatchName, "--description", "Academy Batch CE", "--vpc-id", $script:VpcId, "--tag-specifications", "ResourceType=security-group,Tags=[{Key=Name,Value=$($script:SgBatchName)},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
        $script:BatchSecurityGroupId = $c.GroupId
        if (-not (Test-SgHasDefaultEgress -GroupId $script:BatchSecurityGroupId)) {
            Invoke-Aws @("ec2", "authorize-security-group-egress", "--group-id", $script:BatchSecurityGroupId, "--protocol", "all", "--cidr", "0.0.0.0/0", "--region", $script:Region) -ErrorMessage "sg-batch egress" | Out-Null
        }
        Write-Ok "SG $script:SgBatchName created"
        $script:ChangesMade = $true
    } else {
        $script:BatchSecurityGroupId = $byName[$script:SgBatchName].GroupId
    }
    # sg-data: inbound from sg-app, sg-batch (e.g. 5432, 6379); outbound minimal
    if (-not $byName[$script:SgDataName] -and -not $script:PlanMode) {
        $c = Invoke-AwsJson @("ec2", "create-security-group", "--group-name", $script:SgDataName, "--description", "Academy data (RDS/Redis)", "--vpc-id", $script:VpcId, "--tag-specifications", "ResourceType=security-group,Tags=[{Key=Name,Value=$($script:SgDataName)},{Key=Project,Value=academy}]", "--region", $script:Region, "--output", "json")
        $script:SecurityGroupData = $c.GroupId
        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupData, "--protocol", "tcp", "--port", "5432", "--source-group", $script:SecurityGroupApp, "--region", $script:Region) -ErrorMessage "sg-data 5432 app" | Out-Null
        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupData, "--protocol", "tcp", "--port", "5432", "--source-group", $script:BatchSecurityGroupId, "--region", $script:Region) -ErrorMessage "sg-data 5432 batch" | Out-Null
        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupData, "--protocol", "tcp", "--port", "6379", "--source-group", $script:SecurityGroupApp, "--region", $script:Region) -ErrorMessage "sg-data 6379 app" | Out-Null
        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupData, "--protocol", "tcp", "--port", "6379", "--source-group", $script:BatchSecurityGroupId, "--region", $script:Region) -ErrorMessage "sg-data 6379 batch" | Out-Null
        if (-not (Test-SgHasDefaultEgress -GroupId $script:SecurityGroupData)) {
            Invoke-Aws @("ec2", "authorize-security-group-egress", "--group-id", $script:SecurityGroupData, "--protocol", "all", "--cidr", "0.0.0.0/0", "--region", $script:Region) -ErrorMessage "sg-data egress" | Out-Null
        }
        Write-Ok "SG $script:SgDataName created"
        $script:ChangesMade = $true
    } else {
        $script:SecurityGroupData = $byName[$script:SgDataName].GroupId
        # 기존 sg-data에 sg-app/sg-batch에서 5432,6379 허용 규칙 보장 (인프라 리셋 후 재배포 시)
        if (-not $script:PlanMode -and $script:SecurityGroupApp -and $script:BatchSecurityGroupId) {
            foreach ($port in @(5432, 6379)) {
                foreach ($srcSg in @($script:SecurityGroupApp, $script:BatchSecurityGroupId)) {
                    try {
                        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $script:SecurityGroupData, "--protocol", "tcp", "--port", $port, "--source-group", $srcSg, "--region", $script:Region) -ErrorMessage "sg-data $port from $srcSg" 2>&1 | Out-Null
                        Write-Ok "SG $script:SgDataName: added $port from $srcSg"
                        $script:ChangesMade = $true
                    } catch { if ($_.Exception.Message -notmatch "Duplicate|InvalidPermission.Duplicate") { throw } }
                }
            }
        }
    }
}

# ECR/S3 VPC Endpoints so API instances (public or private) can pull images without internet path (avoids ECR connect timeout).
function Ensure-ECR-VpcEndpoints {
    $vpcId = $script:VpcId
    $region = $script:Region
    $vpcCidr = $script:VpcCidr
    if (-not $vpcId -or -not $region -or $script:PlanMode) { return }
    $svcPrefix = "com.amazonaws.$region"
    $ecrApiSvc = "$svcPrefix.ecr.api"
    $ecrDkrSvc = "$svcPrefix.ecr.dkr"
    $s3Svc = "$svcPrefix.s3"

    # SG for interface endpoints: allow 443 from VPC
    $sgName = "academy-v1-vpce-sg"
    $r = Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$vpcId", "Name=group-name,Values=$sgName", "--region", $region, "--output", "json")
    $vpceSg = $r.SecurityGroups | Where-Object { $_.GroupName -eq $sgName } | Select-Object -First 1
    if (-not $vpceSg -and -not $script:PlanMode) {
        $c = Invoke-AwsJson @("ec2", "create-security-group", "--group-name", $sgName, "--description", "VPC endpoints (ECR etc.)", "--vpc-id", $vpcId, "--tag-specifications", "ResourceType=security-group,Tags=[{Key=Name,Value=$sgName},{Key=Project,Value=academy}]", "--region", $region, "--output", "json")
        $vpceSgId = $c.GroupId
        Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $vpceSgId, "--protocol", "tcp", "--port", "443", "--cidr", $vpcCidr, "--region", $region) -ErrorMessage "vpce-sg 443" | Out-Null
        Write-Ok "SG $sgName created for VPC endpoints"
        $script:ChangesMade = $true
    } else {
        $vpceSgId = $vpceSg.GroupId
    }

    # Route table IDs used by API subnets (public + private) for S3 gateway endpoint
    $allSubnets = @($script:PublicSubnets) + @($script:PrivateSubnets) | Where-Object { $_ -match '^subnet-' }
    $rtIds = @()
    foreach ($subId in $allSubnets) {
        $a = Invoke-AwsJson @("ec2", "describe-route-tables", "--filters", "Name=association.subnet-id,Values=$subId", "Name=vpc-id,Values=$vpcId", "--region", $region, "--output", "json")
        foreach ($rt in $a.RouteTables) {
            $rtId = $rt.RouteTableId
            if ($rtId -and $rtIds -notcontains $rtId) { $rtIds += $rtId }
        }
    }
    $rtIds = @($rtIds | Where-Object { $_ -and $_ -match '^rtb-' })

    # ECR API interface endpoint
    $existing = Invoke-AwsJson @("ec2", "describe-vpc-endpoints", "--filters", "Name=vpc-id,Values=$vpcId", "Name=service-name,Values=$ecrApiSvc", "--region", $region, "--output", "json") 2>$null
    if (-not $existing -or -not $existing.VpcEndpoints -or $existing.VpcEndpoints.Count -eq 0) {
        if (-not $script:PlanMode) {
            $subIds = @($script:PublicSubnets + $script:PrivateSubnets) | Where-Object { $_ -match '^subnet-' } | Select-Object -First 2
            if ($subIds.Count -lt 1) { Write-Warn "No subnets for ECR endpoint"; return }
            $argsEcrApi = @("ec2", "create-vpc-endpoint", "--vpc-id", $vpcId, "--vpc-endpoint-type", "Interface", "--service-name", $ecrApiSvc, "--subnet-ids") + @($subIds) + @("--security-group-ids", $vpceSgId, "--private-dns-enabled", "--region", $region)
            Invoke-Aws $argsEcrApi -ErrorMessage "create-vpc-endpoint ecr.api" | Out-Null
            Write-Ok "VPC endpoint $ecrApiSvc created"
            $script:ChangesMade = $true
        }
    } else { Write-Ok "VPC endpoint $ecrApiSvc exists" }
    # 기존 ECR 엔드포인트가 다른 SG를 쓰는 경우 443 허용 보장(Connect timeout 방지)
    if ($existing -and $existing.VpcEndpoints -and $existing.VpcEndpoints.Count -gt 0 -and -not $script:PlanMode) {
        foreach ($ep in $existing.VpcEndpoints) {
            $epSgs = $ep.Groups | Where-Object { $_.GroupId } | ForEach-Object { $_.GroupId }
            foreach ($sgId in $epSgs) {
                $sgDesc = Invoke-AwsJson @("ec2", "describe-security-groups", "--group-ids", $sgId, "--region", $region, "--output", "json") 2>$null
                $has443 = $false
                if ($sgDesc -and $sgDesc.SecurityGroups -and $sgDesc.SecurityGroups[0].IpPermissions) {
                    foreach ($perm in $sgDesc.SecurityGroups[0].IpPermissions) {
                        if ($perm.FromPort -eq 443 -and $perm.ToPort -eq 443) {
                            foreach ($r in $perm.IpRanges) { if ($r.CidrIp -eq $vpcCidr) { $has443 = $true; break } }
                        }
                    }
                }
                if (-not $has443) {
                    Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--protocol", "tcp", "--port", "443", "--cidr", $vpcCidr, "--region", $region) -ErrorMessage "vpce-sg 443 from VpcCidr" | Out-Null
                    Write-Ok "ECR endpoint SG ${sgId} added 443 from $vpcCidr"
                    $script:ChangesMade = $true
                }
            }
        }
    }

    # ECR DKR interface endpoint
    $existingDkr = Invoke-AwsJson @("ec2", "describe-vpc-endpoints", "--filters", "Name=vpc-id,Values=$vpcId", "Name=service-name,Values=$ecrDkrSvc", "--region", $region, "--output", "json") 2>$null
    if (-not $existingDkr -or -not $existingDkr.VpcEndpoints -or $existingDkr.VpcEndpoints.Count -eq 0) {
        if (-not $script:PlanMode) {
            $subIdsDkr = @($script:PublicSubnets + $script:PrivateSubnets) | Where-Object { $_ -match '^subnet-' } | Select-Object -First 2
            if ($subIdsDkr.Count -lt 1) { return }
            $argsEcrDkr = @("ec2", "create-vpc-endpoint", "--vpc-id", $vpcId, "--vpc-endpoint-type", "Interface", "--service-name", $ecrDkrSvc, "--subnet-ids") + @($subIdsDkr) + @("--security-group-ids", $vpceSgId, "--private-dns-enabled", "--region", $region)
            Invoke-Aws $argsEcrDkr -ErrorMessage "create-vpc-endpoint ecr.dkr" | Out-Null
            Write-Ok "VPC endpoint $ecrDkrSvc created"
            $script:ChangesMade = $true
        }
    } else { Write-Ok "VPC endpoint $ecrDkrSvc exists" }
    # 기존 ECR DKR 엔드포인트 SG에 443 허용 보장
    if ($existingDkr -and $existingDkr.VpcEndpoints -and $existingDkr.VpcEndpoints.Count -gt 0 -and -not $script:PlanMode) {
        foreach ($ep in $existingDkr.VpcEndpoints) {
            $epSgs = $ep.Groups | Where-Object { $_.GroupId } | ForEach-Object { $_.GroupId }
            foreach ($sgId in $epSgs) {
                $sgDesc = Invoke-AwsJson @("ec2", "describe-security-groups", "--group-ids", $sgId, "--region", $region, "--output", "json") 2>$null
                $has443 = $false
                if ($sgDesc -and $sgDesc.SecurityGroups -and $sgDesc.SecurityGroups[0].IpPermissions) {
                    foreach ($perm in $sgDesc.SecurityGroups[0].IpPermissions) {
                        if ($perm.FromPort -eq 443 -and $perm.ToPort -eq 443) {
                            foreach ($r in $perm.IpRanges) { if ($r.CidrIp -eq $vpcCidr) { $has443 = $true; break } }
                        }
                    }
                }
                if (-not $has443) {
                    Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--protocol", "tcp", "--port", "443", "--cidr", $vpcCidr, "--region", $region) -ErrorMessage "vpce-sg 443 from VpcCidr (dkr)" | Out-Null
                    Write-Ok "ECR DKR endpoint SG ${sgId} added 443 from $vpcCidr"
                    $script:ChangesMade = $true
                }
            }
        }
    }

    # S3 gateway endpoint (ECR image layers)
    $existingS3 = Invoke-AwsJson @("ec2", "describe-vpc-endpoints", "--filters", "Name=vpc-id,Values=$vpcId", "Name=service-name,Values=$s3Svc", "--region", $region, "--output", "json") 2>$null
    if (-not $existingS3 -or -not $existingS3.VpcEndpoints -or $existingS3.VpcEndpoints.Count -eq 0) {
        if (-not $script:PlanMode -and $rtIds.Count -gt 0) {
            $argsS3 = @("ec2", "create-vpc-endpoint", "--vpc-id", $vpcId, "--vpc-endpoint-type", "Gateway", "--service-name", $s3Svc, "--route-table-ids") + @($rtIds) + @("--region", $region)
            Invoke-Aws $argsS3 -ErrorMessage "create-vpc-endpoint s3" | Out-Null
            Write-Ok "VPC endpoint $s3Svc (gateway) created"
            $script:ChangesMade = $true
        }
    } else { Write-Ok "VPC endpoint $s3Svc exists" }
}

function Ensure-Network {
    Write-Step "Ensure Network (2-tier VPC, NAT, SG)"
    if ($script:PlanMode) {
        Write-Ok "Network Ensure skipped (Plan)"
        return
    }
    Ensure-Vpc
    $azs = Get-FirstTwoAzs
    Ensure-Subnets -Azs $azs
    $igwId = Ensure-InternetGateway
    $natAllocId = $null
    if ($script:NatEnabled) {
        $natAllocId = Ensure-NatGateway
    }
    Ensure-RouteTables -IgwId $igwId -NatAllocId $natAllocId
    Ensure-SecurityGroups
    Ensure-ECR-VpcEndpoints
    Write-Ok "Network ready: VpcId=$($script:VpcId) PublicSubnets=$($script:PublicSubnets -join ',') PrivateSubnets=$($script:PrivateSubnets -join ',')"
}

# Legacy: now no-op when Ensure-Network has run; keep for callers that expect these names.
function Ensure-NetworkVpc {
    if ($script:VpcId) {
        $vpc = Invoke-AwsJson @("ec2", "describe-vpcs", "--vpc-ids", $script:VpcId, "--region", $script:Region, "--output", "json")
        if ($vpc -and $vpc.Vpcs -and $vpc.Vpcs.Count -gt 0) { Write-Ok "VPC $($script:VpcId)" }
    }
}

function Confirm-SubnetsMatchSSOT {
    if ($script:PublicSubnets.Count -gt 0 -or $script:PrivateSubnets.Count -gt 0) {
        Write-Ok "Subnets set (Public: $($script:PublicSubnets.Count) Private: $($script:PrivateSubnets.Count))"
    }
}
