# ==============================================================================
# VPC Peering: Lambda VPC (new) -> API VPC (old) for private backlog API access
#
# Requester: vpc-009e3ea6265c7a203 (Lambda)
# Accepter:  vpc-0831a2484f9b114c2 (academy-api)
# Then: routes both ways + academy-api SG allows tcp 8000 from 10.1.0.0/16
#
# Usage: .\infra\worker_asg\setup_vpc_peering_lambda_to_api.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$NewVpcId = "vpc-009e3ea6265c7a203",
    [string]$OldVpcId = "vpc-0831a2484f9b114c2",
    [string]$OldVpcCidr = "172.30.0.0/16",
    [string]$NewVpcCidr = "10.1.0.0/16"
)

$ErrorActionPreference = "Stop"

Write-Host "`n=== VPC Peering: Lambda -> API (private 172.30.3.142:8000) ===`n" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# 1) Create VPC Peering (Requester = New/Lambda, Accepter = Old/API)
# ------------------------------------------------------------------------------
Write-Host "[1/5] Create VPC Peering Connection..." -ForegroundColor Cyan
$peerOut = aws ec2 create-vpc-peering-connection --vpc-id $NewVpcId --peer-vpc-id $OldVpcId --region $Region --output json | ConvertFrom-Json
$PeeringConnectionId = $peerOut.VpcPeeringConnection.VpcPeeringConnectionId
Write-Host "      PeeringConnectionId: $PeeringConnectionId" -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 2) Accept the peering connection (same-account: status becomes active)
# ------------------------------------------------------------------------------
Write-Host "[2/5] Accept peering connection..." -ForegroundColor Cyan
aws ec2 accept-vpc-peering-connection --vpc-peering-connection-id $PeeringConnectionId --region $Region | Out-Null
$status = aws ec2 describe-vpc-peering-connections --vpc-peering-connection-ids $PeeringConnectionId --region $Region --query "VpcPeeringConnections[0].Status.Code" --output text
Write-Host "      Status: $status" -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 3) Get route table IDs (main RT for each VPC; Lambda/API subnets use them unless custom)
# ------------------------------------------------------------------------------
Write-Host "[3/5] Get route tables and add routes..." -ForegroundColor Cyan
$newVpcRt = aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$NewVpcId" "Name=association.main,Values=true" --region $Region --query "RouteTables[0].RouteTableId" --output text
$oldVpcRt = aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$OldVpcId" "Name=association.main,Values=true" --region $Region --query "RouteTables[0].RouteTableId" --output text
if (-not $newVpcRt -or $newVpcRt -eq "None") {
    Write-Host "      FAIL: No main route table for New VPC $NewVpcId" -ForegroundColor Red
    exit 1
}
if (-not $oldVpcRt -or $oldVpcRt -eq "None") {
    Write-Host "      FAIL: No main route table for Old VPC $OldVpcId" -ForegroundColor Red
    exit 1
}
Write-Host "      New VPC main RouteTableId: $newVpcRt" -ForegroundColor Gray
Write-Host "      Old VPC main RouteTableId: $oldVpcRt" -ForegroundColor Gray

# New VPC: route to Old VPC CIDR via peering
aws ec2 create-route --route-table-id $newVpcRt --destination-cidr-block $OldVpcCidr --vpc-peering-connection-id $PeeringConnectionId --region $Region
Write-Host "      Route added (New VPC): $OldVpcCidr -> $PeeringConnectionId" -ForegroundColor Gray

# Old VPC: route to New VPC CIDR via peering
aws ec2 create-route --route-table-id $oldVpcRt --destination-cidr-block $NewVpcCidr --vpc-peering-connection-id $PeeringConnectionId --region $Region
Write-Host "      Route added (Old VPC): $NewVpcCidr -> $PeeringConnectionId" -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 4) Get academy-api security group (from instance or by name in Old VPC)
# ------------------------------------------------------------------------------
Write-Host "[4/5] Find academy-api security group..." -ForegroundColor Cyan
$apiSgId = aws ec2 describe-instances --filters "Name=tag:Name,Values=academy-api" "Name=instance-state-name,Values=running" --region $Region --query "Reservations[0].Instances[0].SecurityGroups[0].GroupId" --output text 2>$null
if (-not $apiSgId -or $apiSgId -eq "None") {
    $apiSgId = aws ec2 describe-security-groups --filters "Name=group-name,Values=academy-api-sg" "Name=vpc-id,Values=$OldVpcId" --region $Region --query "SecurityGroups[0].GroupId" --output text 2>$null
}
if (-not $apiSgId -or $apiSgId -eq "None") {
    Write-Host "      WARN: academy-api SG not found. Add manually: tcp 8000 from $NewVpcCidr to API SG." -ForegroundColor Yellow
} else {
    Write-Host "      ApiSecurityGroupId: $apiSgId" -ForegroundColor Gray

    # ------------------------------------------------------------------------------
    # 5) Allow inbound tcp 8000 from New VPC CIDR to academy-api SG
    # ------------------------------------------------------------------------------
    Write-Host "[5/5] Add inbound rule (tcp 8000 from $NewVpcCidr) to academy-api SG..." -ForegroundColor Cyan
    $ea = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    aws ec2 authorize-security-group-ingress --group-id $apiSgId --protocol tcp --port 8000 --cidr $NewVpcCidr --region $Region 2>$null
    $ErrorActionPreference = $ea
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      Inbound rule added." -ForegroundColor Green
    } else {
        Write-Host "      Rule may already exist (duplicate); check SG if needed." -ForegroundColor Gray
    }
}

Write-Host "`n=== Done ===`n" -ForegroundColor Green
Write-Host "PeeringConnectionId:  $PeeringConnectionId"
Write-Host "RouteTableIds updated: $newVpcRt (New VPC), $oldVpcRt (Old VPC)"
if ($apiSgId -and $apiSgId -ne "None") { Write-Host "SG rule added: $apiSgId inbound tcp 8000 from $NewVpcCidr" }
Write-Host "`nLambda can now call http://172.30.3.142:8000/api/v1/internal/video/backlog-count/ via peering.`n" -ForegroundColor Gray
