# ==============================================================================
# academy-api EC2: private subnet에서 SQS 접근 불가 해결
#
# 현상: subnet-049e711f41fdff71b (private, no NAT) → curl sqs.ap-northeast-2.amazonaws.com hangs
#      boto3 sqs.send_message() timeout, SQS Visible/NotVisible remain 0
#
# 해결: Option A (권장) VPC Interface Endpoint for SQS - NAT 없이 PrivateLink로 SQS 접근
#      Option B          NAT Gateway - 전체 인터넷 아웃바운드 허용 (월 ~$35)
#
# 사용:
#   Option A: .\infra\worker_asg\fix_academy_api_sqs_access.ps1 -Option SqsEndpoint
#   Option B: .\infra\worker_asg\fix_academy_api_sqs_access.ps1 -Option NatGateway
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$ApiPrivateSubnetId = "subnet-049e711f41fdff71b",
    [string]$VpcId = "vpc-0831a2484f9b114c2",
    [ValidateSet("SqsEndpoint", "NatGateway")]
    [string]$Option = "SqsEndpoint"
)

$ErrorActionPreference = "Stop"

Write-Host "`n=== academy-api SQS 접근 복구 ===" -ForegroundColor Cyan
Write-Host "  Subnet: $ApiPrivateSubnetId | VPC: $VpcId | Option: $Option`n" -ForegroundColor Gray

# academy-api SG (SQS endpoint/NAT 접근용)
$apiSgId = aws ec2 describe-instances --filters "Name=tag:Name,Values=academy-api" "Name=instance-state-name,Values=running" --region $Region --query "Reservations[0].Instances[0].SecurityGroups[0].GroupId" --output text 2>$null
if (-not $apiSgId -or $apiSgId -eq "None") {
    $apiSgId = aws ec2 describe-security-groups --filters "Name=group-name,Values=academy-api-sg" "Name=vpc-id,Values=$VpcId" --region $Region --query "SecurityGroups[0].GroupId" --output text 2>$null
}
if (-not $apiSgId -or $apiSgId -eq "None") {
    Write-Host "academy-api Security Group not found. Specify manually or create one." -ForegroundColor Red
    exit 1
}
Write-Host "  academy-api SG: $apiSgId" -ForegroundColor Gray

if ($Option -eq "SqsEndpoint") {
    # --------------------------------------------------------------------------
    # Option A: VPC Interface Endpoint for SQS (PrivateLink, no NAT cost)
    # --------------------------------------------------------------------------
    Write-Host "`n[Option A] VPC Interface Endpoint for SQS (com.amazonaws.$Region.sqs)..." -ForegroundColor Cyan
    $existingEp = aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$VpcId" "Name=service-name,Values=com.amazonaws.$Region.sqs" --region $Region --query "VpcEndpoints[0].VpcEndpointId" --output text 2>$null
    if ($existingEp -and $existingEp -ne "None") {
        Write-Host "  SQS endpoint already exists: $existingEp" -ForegroundColor Green
        Write-Host "  Check: endpoint must include subnet $ApiPrivateSubnetId in SubnetIds." -ForegroundColor Gray
        $epDetail = aws ec2 describe-vpc-endpoints --vpc-endpoint-ids $existingEp --region $Region --query "VpcEndpoints[0].{State:State,SubnetIds:SubnetIds}" --output json | ConvertFrom-Json
        if ($epDetail.SubnetIds -notcontains $ApiPrivateSubnetId) {
            Write-Host "  WARN: Endpoint does not include api subnet. Create new endpoint or add subnet." -ForegroundColor Yellow
            Write-Host "  Run: aws ec2 create-vpc-endpoint ... manually with SubnetIds including $ApiPrivateSubnetId" -ForegroundColor Gray
        }
    } else {
        $epOut = aws ec2 create-vpc-endpoint --vpc-id $VpcId --vpc-endpoint-type Interface `
            --service-name "com.amazonaws.$Region.sqs" `
            --subnet-ids $ApiPrivateSubnetId --security-group-ids $apiSgId `
            --private-dns-enabled --region $Region --output json 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  FAIL: create-vpc-endpoint: $epOut" -ForegroundColor Red
            exit 1
        }
        $epObj = $epOut | ConvertFrom-Json
        $EpId = $epObj.VpcEndpoint.VpcEndpointId
        Write-Host "  Created: VpcEndpointId=$EpId (PrivateDnsEnabled=true)" -ForegroundColor Green
        Write-Host "  Waiting for endpoint available..." -ForegroundColor Gray
        $waited = 0
        do {
            Start-Sleep -Seconds 5; $waited += 5
            $state = aws ec2 describe-vpc-endpoints --vpc-endpoint-ids $EpId --region $Region --query "VpcEndpoints[0].State" --output text
            if ($state -eq "available") { Write-Host "  Endpoint available." -ForegroundColor Green; break }
            if ($waited -ge 120) { Write-Host "  WARN: Timeout. Check console." -ForegroundColor Yellow; break }
        } while ($true)
    }
    Write-Host "`nDone. academy-api can now reach https://sqs.ap-northeast-2.amazonaws.com via PrivateLink." -ForegroundColor Green
    Write-Host "  Test on EC2: curl -s -o /dev/null -w '%{http_code}' https://sqs.ap-northeast-2.amazonaws.com" -ForegroundColor Gray
}

if ($Option -eq "NatGateway") {
    # --------------------------------------------------------------------------
    # Option B: NAT Gateway (full internet outbound)
    # --------------------------------------------------------------------------
    Write-Host "`n[Option B] NAT Gateway (requires public subnet in same AZ)..." -ForegroundColor Cyan

    # 1) Find or create public subnet (has route to IGW)
    $subnetInfo = aws ec2 describe-subnets --subnet-ids $ApiPrivateSubnetId --region $Region --query "Subnets[0].{Az:AvailabilityZone,Cidr:CidrBlock}" --output json | ConvertFrom-Json
    $az = $subnetInfo.Az
    Write-Host "  API subnet AZ: $az" -ForegroundColor Gray

    $publicSubnets = aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$VpcId" --region $Region --query "RouteTables[].RouteTableId" --output json | ConvertFrom-Json
    $publicSubnetId = $null
    foreach ($rtId in $publicSubnets) {
        $igwRoute = aws ec2 describe-route-tables --route-table-ids $rtId --region $Region --query "RouteTables[0].Routes[?GatewayId!=null && starts_with(GatewayId,'igw-')]" --output json | ConvertFrom-Json
        if ($igwRoute -and $igwRoute.Count -gt 0) {
            $assocs = aws ec2 describe-route-tables --route-table-ids $rtId --region $Region --query "RouteTables[0].Associations[?SubnetId!=null].SubnetId" --output json | ConvertFrom-Json
            foreach ($sid in $assocs) {
                $subAz = aws ec2 describe-subnets --subnet-ids $sid --region $Region --query "Subnets[0].AvailabilityZone" --output text
                if ($subAz -eq $az) {
                    $publicSubnetId = $sid
                    break
                }
            }
            if ($publicSubnetId) { break }
        }
    }

    if (-not $publicSubnetId) {
        Write-Host "  FAIL: No public subnet found in AZ $az. Create a public subnet with IGW route first." -ForegroundColor Red
        Write-Host "  1. Create subnet in $az" -ForegroundColor Gray
        Write-Host "  2. Create IGW, attach to VPC" -ForegroundColor Gray
        Write-Host "  3. Add route 0.0.0.0/0 -> igw-xxx to that subnet's route table" -ForegroundColor Gray
        exit 1
    }
    Write-Host "  Public subnet (same AZ): $publicSubnetId" -ForegroundColor Gray

    # 2) Allocate EIP
    $eipOut = aws ec2 allocate-address --domain vpc --region $Region --output json | ConvertFrom-Json
    $AllocId = $eipOut.AllocationId
    Write-Host "  EIP AllocationId: $AllocId" -ForegroundColor Gray

    # 3) Create NAT Gateway
    $natOut = aws ec2 create-nat-gateway --subnet-id $publicSubnetId --allocation-id $AllocId --region $Region --output json | ConvertFrom-Json
    $NatGwId = $natOut.NatGateway.NatGatewayId
    Write-Host "  NAT Gateway: $NatGwId (waiting available...)" -ForegroundColor Gray
    $waited = 0
    do {
        Start-Sleep -Seconds 10; $waited += 10
        $state = aws ec2 describe-nat-gateways --nat-gateway-ids $NatGwId --region $Region --query "NatGateways[0].State" --output text
        if ($state -eq "available") { Write-Host "  NAT Gateway available." -ForegroundColor Green; break }
        if ($state -eq "failed") { Write-Host "  NAT Gateway failed." -ForegroundColor Red; exit 1 }
        if ($waited -ge 300) { Write-Host "  WARN: Timeout." -ForegroundColor Yellow; break }
    } while ($true)

    # 4) Get route table for API private subnet
    $rtId = aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$VpcId" "Name=association.subnet-id,Values=$ApiPrivateSubnetId" --region $Region --query "RouteTables[0].RouteTableId" --output text 2>$null
    if (-not $rtId -or $rtId -eq "None") {
        $rtId = aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$VpcId" "Name=association.main,Values=true" --region $Region --query "RouteTables[0].RouteTableId" --output text
    }
    if (-not $rtId -or $rtId -eq "None") {
        Write-Host "  FAIL: Could not find route table for subnet $ApiPrivateSubnetId" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Route table: $rtId" -ForegroundColor Gray

    # 5) Add route 0.0.0.0/0 -> NAT Gateway
    aws ec2 create-route --route-table-id $rtId --destination-cidr-block "0.0.0.0/0" --nat-gateway-id $NatGwId --region $Region 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Route added: 0.0.0.0/0 -> $NatGwId" -ForegroundColor Green
    } else {
        Write-Host "  Route may already exist. Check: aws ec2 describe-route-tables --route-table-ids $rtId" -ForegroundColor Yellow
    }

    Write-Host "`nDone. academy-api can now reach internet (including SQS) via NAT Gateway." -ForegroundColor Green
    Write-Host "  Cost: ~\$0.045/hr + data. Consider SqsEndpoint for SQS-only." -ForegroundColor Gray
}

Write-Host "`nVerify on academy-api EC2:" -ForegroundColor Cyan
Write-Host "  curl -m 5 https://sqs.ap-northeast-2.amazonaws.com" -ForegroundColor White
Write-Host "  (Expect HTTP 403 or 405, not timeout.)`n" -ForegroundColor Gray
