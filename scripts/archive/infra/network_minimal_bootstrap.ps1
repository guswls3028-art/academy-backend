# ==============================================================================
# Optional network bootstrap: create VPC, 2 private subnets, NAT gateway, route tables, SG for Batch.
# If VpcId is provided, skip creation and only output existing (caller must provide SubnetIds and SecurityGroupId).
# Usage: .\scripts\infra\network_minimal_bootstrap.ps1 -Region ap-northeast-2 [-VpcId vpc-xxx] [-Cidr 10.0.0.0/16]
# Output: VpcId, SubnetIds, SecurityGroupId
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$VpcId = "",
    [string]$Cidr = "10.0.0.0/16"
)

$ErrorActionPreference = "Stop"

if ($VpcId) {
    Write-Host "Using existing VPC: $VpcId. Provide SubnetIds and SecurityGroupId via your own means." -ForegroundColor Cyan
    Write-Host "VpcId=$VpcId"
    exit 0
}

Write-Host "Creating VPC and minimal Batch network (NAT, 2 private subnets, SG)..." -ForegroundColor Cyan

$vpc = aws ec2 create-vpc --cidr-block $Cidr --region $Region --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=academy-video-batch-vpc}]" --output json | ConvertFrom-Json
$vpcId = $vpc.Vpc.VpcId
aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-hostnames --region $Region | Out-Null

$azs = (aws ec2 describe-availability-zones --region $Region --query "AvailabilityZones[0:2].ZoneName" --output text) -split "\s+"
$subnet1 = aws ec2 create-subnet --vpc-id $vpcId --cidr-block "10.0.1.0/24" --availability-zone $azs[0] --region $Region --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=academy-video-batch-subnet-1}]" --output json | ConvertFrom-Json
$subnet2 = aws ec2 create-subnet --vpc-id $vpcId --cidr-block "10.0.2.0/24" --availability-zone $azs[1] --region $Region --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=academy-video-batch-subnet-2}]" --output json | ConvertFrom-Json
$subnetId1 = $subnet1.Subnet.SubnetId
$subnetId2 = $subnet2.Subnet.SubnetId

$igw = aws ec2 create-internet-gateway --region $Region --output json | ConvertFrom-Json
$igwId = $igw.InternetGateway.InternetGatewayId
aws ec2 attach-internet-gateway --vpc-id $vpcId --internet-gateway-id $igwId --region $Region | Out-Null

$pubSubnet1 = aws ec2 create-subnet --vpc-id $vpcId --cidr-block "10.0.0.0/24" --availability-zone $azs[0] --region $Region --output json | ConvertFrom-Json
$pubSubnetId = $pubSubnet1.Subnet.SubnetId

$eip = aws ec2 allocate-address --domain vpc --region $Region --output json | ConvertFrom-Json
$allocationId = $eip.AllocationId

$nat = aws ec2 create-nat-gateway --subnet-id $pubSubnetId --allocation-id $allocationId --region $Region --output json | ConvertFrom-Json
$natId = $nat.NatGateway.NatGatewayId
Write-Host "Waiting for NAT gateway..." -ForegroundColor Gray
do { Start-Sleep -Seconds 10; $st = (aws ec2 describe-nat-gateways --nat-gateway-ids $natId --region $Region --query "NatGateways[0].State" --output text) } while ($st -eq "pending")

$privRt = aws ec2 create-route-table --vpc-id $vpcId --region $Region --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=academy-video-batch-private-rt}]" --output json | ConvertFrom-Json
$privRtId = $privRt.RouteTable.RouteTableId
aws ec2 create-route --route-table-id $privRtId --destination-cidr-block 0.0.0.0/0 --nat-gateway-id $natId --region $Region | Out-Null
aws ec2 associate-route-table --route-table-id $privRtId --subnet-id $subnetId1 --region $Region | Out-Null
aws ec2 associate-route-table --route-table-id $privRtId --subnet-id $subnetId2 --region $Region | Out-Null

$sg = aws ec2 create-security-group --group-name academy-video-batch-sg --description "Batch compute" --vpc-id $vpcId --region $Region --output json | ConvertFrom-Json
$sgId = $sg.GroupId
aws ec2 authorize-security-group-ingress --group-id $sgId --protocol -1 --source-group $sgId --region $Region 2>$null | Out-Null

Write-Host "VpcId=$vpcId"
Write-Host "SubnetIds=$subnetId1,$subnetId2"
Write-Host "SecurityGroupId=$sgId"
Write-Host "Done." -ForegroundColor Green
