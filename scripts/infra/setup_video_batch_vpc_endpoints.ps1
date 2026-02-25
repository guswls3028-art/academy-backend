# ==============================================================================
# academy-video-batch-ce-v2 가 사용하는 VPC에 Batch 실행에 필요한 VPC Endpoint 세트를
# 원테이크로 생성하고, 생성 후 검증까지 수행.
# NAT Gateway 미생성, Private subnet 구조 유지.
#
# Usage: .\scripts\infra\setup_video_batch_vpc_endpoints.ps1 -Region ap-northeast-2 [-CeName academy-video-batch-ce-v2]
# Exit: 0 = success, 1 = error, 3 = root credential
# ==============================================================================

[CmdletBinding()]
param(
    [string]$Region = "ap-northeast-2",
    [string]$CeName = "academy-video-batch-ce-v2"
)

$ErrorActionPreference = "Stop"
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# A. Root credential 감지 시 exit 3
$callerArn = aws sts get-caller-identity --query Arn --output text 2>&1
if ($LASTEXITCODE -eq 0 -and $callerArn -match ":root") {
    Write-Host "BLOCK: root credentials detected. Use IAM user or role. (exit 3)" -ForegroundColor Red
    exit 3
}

function Aws-JsonSafe {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $tempFile = Join-Path ([System.IO.Path]::GetTempPath()) "vpc_ep_$(Get-Date -Format 'yyyyMMddHHmmss').json"
    $utf8 = New-Object System.Text.UTF8Encoding $false
    try {
        $out = & aws @ArgsArray --output json 2>&1
        $exit = $LASTEXITCODE
        if ($exit -ne 0) { return $null }
        $str = ($out | Out-String).Trim()
        if ([string]::IsNullOrWhiteSpace($str)) { return $null }
        [System.IO.File]::WriteAllText($tempFile, $str, $utf8)
        $content = [System.IO.File]::ReadAllText($tempFile, $utf8)
        return $content | ConvertFrom-Json
    } finally {
        if (Test-Path -LiteralPath $tempFile) { Remove-Item $tempFile -Force -ErrorAction SilentlyContinue }
        $ErrorActionPreference = $prev
    }
}

# create-vpc-endpoint 실행, 실패 시 AWS 오류 출력 후 exit 1
function Invoke-CreateVpcEndpoint {
    param([string[]]$CreateArgs, [string]$ServiceName)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @CreateArgs --output json 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    $str = ($out | Out-String).Trim()
    if ($exit -ne 0) {
        Write-Host "FAIL: create-vpc-endpoint $ServiceName failed." -ForegroundColor Red
        Write-Host "AWS output: $str" -ForegroundColor Gray
        exit 1
    }
    $str | ConvertFrom-Json
}

# B. CE describe → subnets 추출
$ceList = Aws-JsonSafe @("batch", "describe-compute-environments", "--compute-environments", $CeName, "--region", $Region)
$ce = $null
if ($ceList -and $ceList.computeEnvironments -and $ceList.computeEnvironments.Count -gt 0) {
    $ce = $ceList.computeEnvironments[0]
}
if (-not $ce) {
    Write-Host "FAIL: Compute environment $CeName not found." -ForegroundColor Red
    exit 1
}
$cr = $ce.computeResources
if (-not $cr) {
    Write-Host "FAIL: CE $CeName has no computeResources." -ForegroundColor Red
    exit 1
}
$ceSubnets = @($cr.subnets)
$ceSecurityGroupIds = @($cr.securityGroupIds)
if (-not $ceSubnets -or $ceSubnets.Count -eq 0) {
    Write-Host "FAIL: CE $CeName has no subnets." -ForegroundColor Red
    exit 1
}
if (-not $ceSecurityGroupIds -or $ceSecurityGroupIds.Count -eq 0) {
    Write-Host "FAIL: CE $CeName has no securityGroupIds." -ForegroundColor Red
    exit 1
}

# C. 첫 subnet → VPC ID 추출
$subResp = Aws-JsonSafe @("ec2", "describe-subnets", "--subnet-ids", $ceSubnets[0], "--region", $Region)
if (-not $subResp -or -not $subResp.Subnets -or $subResp.Subnets.Count -eq 0) {
    Write-Host "FAIL: Could not describe subnet $($ceSubnets[0])." -ForegroundColor Red
    exit 1
}
$vpcId = $subResp.Subnets[0].VpcId
if (-not $vpcId) {
    Write-Host "FAIL: VpcId not found from subnet." -ForegroundColor Red
    exit 1
}

Write-Host "VPC ID: $vpcId | CE subnets: $($ceSubnets.Count) | SG: $($ceSecurityGroupIds -join ',')" -ForegroundColor Gray

# CE 서브넷 ID -> AZ 이름 매핑 (Interface 엔드포인트는 서비스별 지원 AZ만 사용해야 함)
$subnetAzMap = @{}
$subRespAll = Aws-JsonSafe @("ec2", "describe-subnets", "--subnet-ids", $ceSubnets, "--region", $Region)
if ($subRespAll -and $subRespAll.Subnets) {
    foreach ($s in $subRespAll.Subnets) {
        $az = $s.AvailabilityZone
        if ($az) { $subnetAzMap[$s.SubnetId] = $az }
    }
}

# 서비스별 지원 AZ 조회 후, 해당 AZ에 있는 CE 서브넷만 반환
function Get-SubnetsForService {
    param([string]$ServiceName)
    $svcResp = Aws-JsonSafe @("ec2", "describe-vpc-endpoint-services", "--service-names", $ServiceName, "--region", $Region)
    $supportedAzs = @()
    if ($svcResp -and $svcResp.ServiceDetails -and $svcResp.ServiceDetails.Count -gt 0) {
        $detail = $svcResp.ServiceDetails[0]
        $azSet = $detail.AvailabilityZoneSet
        if (-not $azSet) { $azSet = $detail.availabilityZoneSet }
        if ($azSet) { $supportedAzs = @($azSet) }
    }
    if ($supportedAzs.Count -eq 0) {
        return @($ceSubnets)
    }
    $filtered = @()
    foreach ($subId in $ceSubnets) {
        $az = $subnetAzMap[$subId]
        if ($az -and ($supportedAzs -contains $az)) {
            $filtered += $subId
        }
    }
    return $filtered
}

# 필수 서비스 이름 (region 치환)
$InterfaceServices = @(
    "com.amazonaws.$Region.ecr.api",
    "com.amazonaws.$Region.ecr.dkr",
    "com.amazonaws.$Region.logs",
    "com.amazonaws.$Region.ecs",
    "com.amazonaws.$Region.ecs-agent",
    "com.amazonaws.$Region.ecs-telemetry",
    "com.amazonaws.$Region.sts"
)
$GatewayServices = @(
    "com.amazonaws.$Region.s3"
)
$RequiredServiceNames = $InterfaceServices + $GatewayServices

# D. 해당 VPC에 이미 존재하는 endpoint 조회
$epList = Aws-JsonSafe @("ec2", "describe-vpc-endpoints", "--filters", "Name=vpc-id,Values=$vpcId", "--region", $Region)
$existingByService = @{}
if ($epList -and $epList.VpcEndpoints) {
    foreach ($ep in $epList.VpcEndpoints) {
        $svc = $ep.ServiceName
        if (-not $existingByService[$svc]) { $existingByService[$svc] = @() }
        $existingByService[$svc] += $ep
    }
}

# CE 서브넷이 사용하는 라우트 테이블 ID 수집 (Gateway S3용)
$routeTableIds = [System.Collections.Generic.HashSet[string]]::new()
foreach ($subId in $ceSubnets) {
    $rt = Aws-JsonSafe @("ec2", "describe-route-tables", "--filters", "association.subnet-id=$subId", "--region", $Region)
    if ($rt -and $rt.RouteTables -and $rt.RouteTables.Count -gt 0) {
        [void]$routeTableIds.Add($rt.RouteTables[0].RouteTableId)
    }
}
$mainRt = Aws-JsonSafe @("ec2", "describe-route-tables", "--filters", "Name=vpc-id,Values=$vpcId", "Name=association.main,Values=true", "--region", $Region)
if ($mainRt -and $mainRt.RouteTables -and $mainRt.RouteTables.Count -gt 0) {
    [void]$routeTableIds.Add($mainRt.RouteTables[0].RouteTableId)
}
$routeTableIdsArr = @($routeTableIds)

# E. 없으면 생성
$created = @()

foreach ($svc in $InterfaceServices) {
    $existing = $existingByService[$svc]
    if ($existing -and $existing.Count -gt 0) {
        continue
    }
    $subnetsForSvc = Get-SubnetsForService -ServiceName $svc
    if (-not $subnetsForSvc -or $subnetsForSvc.Count -eq 0) {
        Write-Host "FAIL: No CE subnet in an AZ supported by $svc (CE subnets AZs: $($ceSubnets | ForEach-Object { $subnetAzMap[$_] } | Select-Object -Unique) -join ', ')." -ForegroundColor Red
        exit 1
    }
    Write-Host "Creating Interface endpoint: $svc (subnets: $($subnetsForSvc.Count)/$($ceSubnets.Count))" -ForegroundColor Cyan
    $createArgs = @(
        "ec2", "create-vpc-endpoint",
        "--vpc-id", $vpcId,
        "--vpc-endpoint-type", "Interface",
        "--service-name", $svc,
        "--subnet-ids"
    ) + @($subnetsForSvc) + @(
        "--security-group-ids"
    ) + @($ceSecurityGroupIds) + @(
        "--private-dns-enabled",
        "--region", $Region
    )
    $createOut = Invoke-CreateVpcEndpoint -CreateArgs $createArgs -ServiceName $svc
    if (-not $createOut -or -not $createOut.VpcEndpoint -or -not $createOut.VpcEndpoint.VpcEndpointId) {
        Write-Host "FAIL: create-vpc-endpoint $svc failed." -ForegroundColor Red
        exit 1
    }
    $created += $createOut.VpcEndpoint.VpcEndpointId
}

foreach ($svc in $GatewayServices) {
    $existing = $existingByService[$svc]
    if ($existing -and $existing.Count -gt 0) {
        continue
    }
    if ($routeTableIdsArr.Count -eq 0) {
        Write-Host "WARN: No route tables for Gateway endpoint $svc; skipping." -ForegroundColor Yellow
        continue
    }
    Write-Host "Creating Gateway endpoint: $svc" -ForegroundColor Cyan
    $createArgs = @(
        "ec2", "create-vpc-endpoint",
        "--vpc-id", $vpcId,
        "--vpc-endpoint-type", "Gateway",
        "--service-name", $svc,
        "--route-table-ids"
    ) + @($routeTableIdsArr) + @(
        "--region", $Region
    )
    $createOut = Invoke-CreateVpcEndpoint -CreateArgs $createArgs -ServiceName $svc
    if (-not $createOut -or -not $createOut.VpcEndpoint -or -not $createOut.VpcEndpoint.VpcEndpointId) {
        Write-Host "FAIL: create-vpc-endpoint $svc failed." -ForegroundColor Red
        exit 1
    }
    $created += $createOut.VpcEndpoint.VpcEndpointId
}

if ($created.Count -gt 0) {
    Write-Host "Waiting for created endpoints to become available..." -ForegroundColor Gray
    $waited = 0
    do {
        Start-Sleep -Seconds 10
        $waited += 10
        $desc = Aws-JsonSafe (@("ec2", "describe-vpc-endpoints", "--vpc-endpoint-ids") + @($created) + @("--region", $Region))
        $allOk = $true
        if ($desc -and $desc.VpcEndpoints) {
            foreach ($ep in $desc.VpcEndpoints) {
                if ($ep.State -ne "available") { $allOk = $false; break }
            }
        } else { $allOk = $false }
        if ($allOk) { break }
        if ($waited -ge 300) {
            Write-Host "WARN: Timeout waiting for endpoints." -ForegroundColor Yellow
            break
        }
    } while ($true)
}

# G. 생성 후 describe-vpc-endpoints 로 state=available 확인
$epList2 = Aws-JsonSafe @("ec2", "describe-vpc-endpoints", "--filters", "Name=vpc-id,Values=$vpcId", "--region", $Region)
$byService = @{}
if ($epList2 -and $epList2.VpcEndpoints) {
    foreach ($ep in $epList2.VpcEndpoints) {
        $svc = $ep.ServiceName
        if (-not $byService[$svc]) { $byService[$svc] = @() }
        $byService[$svc] += $ep
    }
}

$allAvailable = $true
foreach ($svc in $RequiredServiceNames) {
    $eps = $byService[$svc]
    if (-not $eps -or $eps.Count -eq 0) {
        $allAvailable = $false
        break
    }
    $hasAvailable = $false
    foreach ($e in $eps) { if ($e.State -eq "available") { $hasAvailable = $true; break } }
    if (-not $hasAvailable) { $allAvailable = $false; break }
}

$existingIds = @()
foreach ($key in $existingByService.Keys) {
    foreach ($ep in $existingByService[$key]) {
        $existingIds += $ep.VpcEndpointId
    }
}
$existingIds = $existingIds | Select-Object -Unique
$createdStr = if ($created.Count -gt 0) { $created -join ", " } else { "none" }
$existingStr = if ($existingIds.Count -gt 0) { $existingIds -join ", " } else { "none" }

Write-Host ""
Write-Host "========== RESULT ==========" -ForegroundColor Cyan
Write-Host "VPC ID: $vpcId"
Write-Host "Endpoints created: $createdStr"
Write-Host "Endpoints existing: $existingStr"
Write-Host "All required endpoints available: $(if ($allAvailable) { 'PASS' } else { 'FAIL' })"
Write-Host "===========================" -ForegroundColor Cyan

exit $(if ($allAvailable) { 0 } else { 1 })
