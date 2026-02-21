# ==============================================================================
# CRITICAL INFRA FIX — VPC DNS for Lambda CloudWatch
#
# Creates a NEW VPC with DNS enabled (EnableDnsSupport, EnableDnsHostnames),
# private subnet, Lambda SG, and interface endpoint for CloudWatch monitoring.
# Then moves ONLY academy-worker-queue-depth-metric Lambda into it.
#
# Do NOT move EC2 or ASG. Lambda only.
# Result: cw.put_metric_data() completes; BacklogCount publishes; TargetTracking scales.
#
# Usage: .\infra\worker_asg\create_lambda_vpc_with_dns.ps1
#        .\infra\worker_asg\create_lambda_vpc_with_dns.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$VpcCidr = "10.1.0.0/16",
    [string]$SubnetCidr = "10.1.1.0/24",
    [string]$AvailabilityZone = "ap-northeast-2a",
    [string]$LambdaFunctionName = "academy-worker-queue-depth-metric"
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..\..")).Path

Write-Host "`n=== Lambda VPC (DNS-enabled) + CloudWatch endpoint ===`n" -ForegroundColor Cyan
Write-Host "Region: $Region | VPC: $VpcCidr | Subnet: $SubnetCidr ($AvailabilityZone)`n" -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 1) Create VPC with DNS enabled (new VPCs have DNS on by default; set explicitly)
# ------------------------------------------------------------------------------
Write-Host "[1/6] Create VPC (DNS enabled)..." -ForegroundColor Cyan
$vpcOut = aws ec2 create-vpc --cidr-block $VpcCidr --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=academy-lambda-metric-vpc}]" --region $Region --output json | ConvertFrom-Json
$VpcId = $vpcOut.Vpc.VpcId
aws ec2 modify-vpc-attribute --vpc-id $VpcId --enable-dns-support --region $Region
aws ec2 modify-vpc-attribute --vpc-id $VpcId --enable-dns-hostnames --region $Region
Write-Host "      VpcId: $VpcId" -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 2) Create private subnet (no IGW route; optional NAT later if Lambda needs internet)
# ------------------------------------------------------------------------------
Write-Host "[2/6] Create private subnet..." -ForegroundColor Cyan
$subnetOut = aws ec2 create-subnet --vpc-id $VpcId --cidr-block $SubnetCidr --availability-zone $AvailabilityZone `
    --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=academy-lambda-metric-subnet}]" --region $Region --output json | ConvertFrom-Json
$SubnetId = $subnetOut.Subnet.SubnetId
Write-Host "      SubnetId: $SubnetId" -ForegroundColor Gray

# Enable DNS hostnames on subnet (for PrivateDnsEnabled endpoint)
aws ec2 modify-subnet-attribute --subnet-id $SubnetId --map-public-ip-on-launch --no-map-public-ip-on-launch --region $Region 2>$null
Write-Host "      Subnet ready (private, no auto public IP)." -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 3) Create security group for Lambda + endpoint (egress 443; endpoint will accept from same SG)
# ------------------------------------------------------------------------------
Write-Host "[3/6] Create Lambda security group..." -ForegroundColor Cyan
$sgOut = aws ec2 create-security-group --group-name "academy-lambda-metric-sg" --description "Lambda queue-depth + CloudWatch endpoint" `
    --vpc-id $VpcId --region $Region --output json | ConvertFrom-Json
$LambdaSgId = $sgOut.GroupId
# Egress: 443 for CloudWatch endpoint; 80/443 if Lambda needs to call API via public URL later
aws ec2 authorize-security-group-egress --group-id $LambdaSgId --protocol tcp --port 443 --cidr 0.0.0.0/0 --region $Region 2>$null
aws ec2 authorize-security-group-egress --group-id $LambdaSgId --protocol tcp --port 80 --cidr 0.0.0.0/0 --region $Region 2>$null
# Endpoint ENI will accept 443 from same SG (default); no extra ingress needed if Lambda uses this SG
Write-Host "      SecurityGroupId: $LambdaSgId" -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 4) Create VPC interface endpoint for CloudWatch monitoring (PrivateDnsEnabled)
# ------------------------------------------------------------------------------
Write-Host "[4/6] Create VPC endpoint (com.amazonaws.$Region.monitoring)..." -ForegroundColor Cyan
$epOut = aws ec2 create-vpc-endpoint --vpc-id $VpcId --vpc-endpoint-type Interface `
    --service-name "com.amazonaws.$Region.monitoring" `
    --subnet-ids $SubnetId --security-group-ids $LambdaSgId `
    --private-dns-enabled --region $Region --output json | ConvertFrom-Json
$EndpointId = $epOut.VpcEndpoint.VpcEndpointId
Write-Host "      VpcEndpointId: $EndpointId (PrivateDnsEnabled=true)" -ForegroundColor Gray

# Wait for endpoint to be available (ENI ready)
Write-Host "      Waiting for endpoint to be available..." -ForegroundColor Gray
$maxWait = 60; $waited = 0
do {
    Start-Sleep -Seconds 5; $waited += 5
    $state = aws ec2 describe-vpc-endpoints --vpc-endpoint-ids $EndpointId --region $Region --query "VpcEndpoints[0].State" --output text
    if ($state -eq "available") { break }
    if ($waited -ge $maxWait) { Write-Host "      WARN: Endpoint not yet available. Proceeding anyway." -ForegroundColor Yellow; break }
} while ($true)

# ------------------------------------------------------------------------------
# 5) Update Lambda: VPC config = new subnet + new SG (Lambda only; EC2/ASG unchanged)
# ------------------------------------------------------------------------------
Write-Host "[5/6] Update Lambda $LambdaFunctionName to new VPC (subnet + SG)..." -ForegroundColor Cyan
aws lambda update-function-configuration --function-name $LambdaFunctionName `
    --vpc-config "SubnetIds=$SubnetId,SecurityGroupIds=$LambdaSgId" --region $Region | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      FAIL: Lambda update-function-configuration failed." -ForegroundColor Red
    exit 1
}
Write-Host "      Lambda VPC config updated. Waiting for LastUpdateStatus=Successful..." -ForegroundColor Gray
$maxWait = 90; $waited = 0
do {
    Start-Sleep -Seconds 5; $waited += 5
    $status = aws lambda get-function-configuration --function-name $LambdaFunctionName --region $Region --query "LastUpdateStatus" --output text
    if ($status -eq "Successful") { Write-Host "      Lambda update Successful." -ForegroundColor Green; break }
    if ($status -eq "Failed") { Write-Host "      Lambda update Failed. Check console." -ForegroundColor Red; exit 1 }
    if ($waited -ge $maxWait) { Write-Host "      WARN: Timeout waiting for Lambda update." -ForegroundColor Yellow; break }
} while ($true)

# ------------------------------------------------------------------------------
# 6) Output
# ------------------------------------------------------------------------------
Write-Host "[6/6] Done.`n" -ForegroundColor Green
Write-Host "Created (DNS-enabled VPC):" -ForegroundColor Cyan
Write-Host "  VpcId:              $VpcId"
Write-Host "  SubnetId:           $SubnetId"
Write-Host "  LambdaSgId:         $LambdaSgId"
Write-Host "  MonitoringEndpoint: $EndpointId"
Write-Host "  Lambda:             $LambdaFunctionName now in this VPC.`n" -ForegroundColor Gray
Write-Host "NOTE: Lambda is in a NEW VPC and cannot reach 172.30.3.142 (API in old VPC) without VPC peering." -ForegroundColor Yellow
Write-Host "      Set VIDEO_BACKLOG_API_URL (public API base) and leave VIDEO_BACKLOG_API_INTERNAL empty," -ForegroundColor Yellow
Write-Host "      OR add VPC peering + route so this VPC can reach the API subnet.`n" -ForegroundColor Yellow
