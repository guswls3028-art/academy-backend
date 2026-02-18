# ==============================================================================
# ElastiCache Redis 생성 (Single-AZ, API/워커와 독립)
# - Cache subnet group, Security group, Replication group 1 node
# - 완료 후 .env REDIS_HOST 갱신, SSM 업로드 안내
# Usage: .\scripts\setup_elasticache_redis.ps1 [-Region ap-northeast-2]
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$SubnetId1 = "subnet-07a8427d3306ce910",
    [string]$SubnetId2 = "subnet-09231ed7ecf59cfa4",
    [string]$ClientSecurityGroupId = "sg-02692600fbf8e26f7"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

$SubnetGroupName = "academy-redis-subnets"
$ReplicationGroupId = "academy-redis"
$RedisSgName = "academy-redis-sg"

Write-Host "`n=== ElastiCache Redis 생성 (Single-AZ) ===" -ForegroundColor Cyan
Write-Host "  Region: $Region" -ForegroundColor Gray
Write-Host "  Subnets: $SubnetId1, $SubnetId2" -ForegroundColor Gray
Write-Host "  Client SG (API/Worker): $ClientSecurityGroupId`n" -ForegroundColor Gray

# 1) VPC from subnet
$VpcId = aws ec2 describe-subnets --subnet-ids $SubnetId1 --region $Region --query "Subnets[0].VpcId" --output text 2>&1
if (-not $VpcId -or $VpcId -eq "None") {
    Write-Host "FAIL: VPC not found for subnet $SubnetId1" -ForegroundColor Red
    exit 1
}
Write-Host "[1/5] VPC: $VpcId" -ForegroundColor Green

# 2) Security group for ElastiCache (inbound 6379 from API/Worker SG or VPC CIDR)
$RedisSgId = aws ec2 describe-security-groups --region $Region --filters "Name=group-name,Values=$RedisSgName" "Name=vpc-id,Values=$VpcId" --query "SecurityGroups[0].GroupId" --output text 2>$null
if (-not $RedisSgId -or $RedisSgId -eq "None") {
    Write-Host "[2/5] Creating security group $RedisSgName ..." -ForegroundColor Cyan
    $RedisSgId = aws ec2 create-security-group --group-name $RedisSgName --description "ElastiCache Redis - allow 6379 from API/Worker" --vpc-id $VpcId --region $Region --output text 2>$null
    # VPC CIDR 사용 (source-group는 같은 VPC 필요, CIDR이 더 안정적)
    $VpcCidr = aws ec2 describe-vpcs --vpc-ids $VpcId --region $Region --query "Vpcs[0].CidrBlock" --output text 2>$null
    if (-not $VpcCidr -or $VpcCidr -eq "None") { $VpcCidr = "10.0.0.0/8" }
    $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    aws ec2 authorize-security-group-ingress --group-id $RedisSgId --protocol tcp --port 6379 --cidr $VpcCidr --region $Region 2>$null
    if ($LASTEXITCODE -ne 0) {
        # source-group 재시도
        aws ec2 authorize-security-group-ingress --group-id $RedisSgId --protocol tcp --port 6379 --source-group $ClientSecurityGroupId --region $Region 2>$null
    }
    $ErrorActionPreference = $ea
    Write-Host "      SG: $RedisSgId" -ForegroundColor Green
} else {
    Write-Host "[2/5] Security group exists: $RedisSgId" -ForegroundColor Green
}

# 3) Cache subnet group
$subnetGroupExists = aws elasticache describe-cache-subnet-groups --cache-subnet-group-name $SubnetGroupName --region $Region 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[3/5] Creating cache subnet group $SubnetGroupName ..." -ForegroundColor Cyan
    aws elasticache create-cache-subnet-group `
        --cache-subnet-group-name $SubnetGroupName `
        --cache-subnet-group-description "Academy Redis - same VPC as API/Worker" `
        --subnet-ids $SubnetId1 $SubnetId2 `
        --region $Region 2>&1
    Write-Host "      OK" -ForegroundColor Green
} else {
    Write-Host "[3/5] Cache subnet group exists: $SubnetGroupName" -ForegroundColor Green
}

# 4) Replication group (single node, no failover)
$rgExists = aws elasticache describe-replication-groups --replication-group-id $ReplicationGroupId --region $Region 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[4/5] Creating replication group $ReplicationGroupId (single node, cache.t4g.micro) ..." -ForegroundColor Cyan
    aws elasticache create-replication-group `
        --replication-group-id $ReplicationGroupId `
        --replication-group-description "Academy Redis Single-AZ" `
        --engine redis `
        --engine-version 7.0 `
        --cache-node-type cache.t4g.micro `
        --num-cache-clusters 1 `
        --cache-subnet-group-name $SubnetGroupName `
        --security-group-ids $RedisSgId `
        --no-automatic-failover-enabled `
        --no-multi-az-enabled `
        --region $Region 2>&1
    Write-Host "      Creating (takes 2~5 min) ..." -ForegroundColor Yellow
} else {
    Write-Host "[4/5] Replication group exists: $ReplicationGroupId" -ForegroundColor Green
}

# 5) Wait for available and get endpoint
Write-Host "[5/5] Waiting for replication group available ..." -ForegroundColor Cyan
aws elasticache wait replication-group-available --replication-group-id $ReplicationGroupId --region $Region 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: replication group not available (check AWS console)" -ForegroundColor Red
    exit 1
}

$primaryEndpoint = aws elasticache describe-replication-groups --replication-group-id $ReplicationGroupId --region $Region `
    --query "ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address" --output text 2>&1
if (-not $primaryEndpoint -or $primaryEndpoint -eq "None") {
    Write-Host "FAIL: could not get primary endpoint" -ForegroundColor Red
    exit 1
}
Write-Host "      Primary endpoint: $primaryEndpoint" -ForegroundColor Green

# Update .env REDIS_HOST
$envPath = Join-Path $RepoRoot ".env"
if (Test-Path $envPath) {
    $content = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8
    $content = $content -replace "REDIS_HOST=.*", "REDIS_HOST=$primaryEndpoint"
    [System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
    Write-Host "`n.env REDIS_HOST updated to: $primaryEndpoint" -ForegroundColor Green
} else {
    Write-Host "`nWARN: .env not found, set REDIS_HOST=$primaryEndpoint manually" -ForegroundColor Yellow
}

Write-Host "`n=== Done ===" -ForegroundColor Cyan
Write-Host "Next:" -ForegroundColor White
Write-Host "  1) API 서버에서 기존 Redis 컨테이너 중지: docker stop academy-redis 2>/dev/null || true" -ForegroundColor Gray
Write-Host "  2) SSM 반영 + 재배포: .\scripts\redeploy_worker_asg.ps1" -ForegroundColor Gray
Write-Host "  3) full_redeploy: .\scripts\full_redeploy.ps1 -GitRepoUrl \"...\" -WorkersViaASG -SkipBuild" -ForegroundColor Gray
Write-Host ""
