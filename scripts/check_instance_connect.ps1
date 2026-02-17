# EC2 Instance Connect 실패 원인 확인 (추측 없이 실제 설정 조회)
# 실행: .\scripts\check_instance_connect.ps1 [-InstanceId i-xxx] [-Region ap-northeast-2]
# AWS CLI 자격증명이 설정된 환경에서 실행하세요.
param(
    [string]$InstanceId = "i-0f4d0ac79f281ee9c",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
Write-Host ""
Write-Host "=== EC2 Instance Connect 원인 확인 (실제 설정 조회) ===" -ForegroundColor Cyan
Write-Host "  InstanceId: $InstanceId , Region: $Region" -ForegroundColor Gray
Write-Host ""

# 1) 인스턴스 정보
Write-Host "[1] 인스턴스 상태" -ForegroundColor White
$inst = aws ec2 describe-instances --instance-ids $InstanceId --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  오류: $inst" -ForegroundColor Red
    exit 1
}
$instJson = $inst | ConvertFrom-Json
$i = $instJson.Reservations[0].Instances[0]
$state = $i.State.Name
$publicIp = $i.PublicIpAddress
$privateIp = $i.PrivateIpAddress
$subnetId = $i.SubnetId
$sgIds = $i.SecurityGroups | ForEach-Object { $_.GroupId }
$launchTime = $i.LaunchTime

Write-Host "  State:       $state" -ForegroundColor $(if ($state -eq "running") { "Green" } else { "Red" })
Write-Host "  PublicIP:    $(if ($publicIp) { $publicIp } else { '(없음 - 브라우저 Instance Connect 불가)' })" -ForegroundColor $(if ($publicIp) { "Green" } else { "Red" })
Write-Host "  PrivateIP:  $privateIp" -ForegroundColor Gray
Write-Host "  SubnetId:    $subnetId" -ForegroundColor Gray
Write-Host "  LaunchTime:  $launchTime" -ForegroundColor Gray
Write-Host "  SecurityGroups: $($sgIds -join ', ')" -ForegroundColor Gray

if (-not $publicIp) {
    Write-Host ""
    Write-Host "  원인: 퍼블릭 IP 없음. 브라우저 Instance Connect는 퍼블릭 IP 필요." -ForegroundColor Red
    Write-Host "  서브넷에서 '퍼블릭 IP 자동 할당' 또는 인스턴스에 Elastic IP 필요." -ForegroundColor Yellow
}

# 2) 보안 그룹 포트 22 규칙 (각 SG)
Write-Host ""
Write-Host "[2] 보안 그룹 인바운드 (SSH 22)" -ForegroundColor White
# EC2 Instance Connect용 프리픽스 리스트 (서울)
$eicPrefixListName = "${Region}.ec2-instance-connect"

foreach ($sgId in $sgIds) {
    $sgOut = aws ec2 describe-security-groups --group-ids $sgId --region $Region --output json 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "  SG $sgId : 조회 실패" -ForegroundColor Red; continue }
    $sg = ($sgOut | ConvertFrom-Json).SecurityGroups[0]
    $sgName = $sg.GroupName
    $port22 = $sg.IpPermissions | Where-Object { $_.FromPort -eq 22 -and $_.ToPort -eq 22 }
    if (-not $port22) {
        Write-Host "  SG $sgId ($sgName): 포트 22 인바운드 규칙 없음 -> SSH/Instance Connect 불가" -ForegroundColor Red
        continue
    }
    Write-Host "  SG $sgId ($sgName):" -ForegroundColor Gray
    $hasEic = $false
    $hasOpen = $false
    foreach ($perm in $port22) {
        foreach ($ip in ($perm.IpRanges)) {
            $cidr = $ip.CidrIp
            Write-Host "    허용 소스: $cidr" -ForegroundColor Gray
            if ($cidr -eq "0.0.0.0/0") { $hasOpen = $true }
        }
        foreach ($pv4 in ($perm.UserIdGroupPairs)) { Write-Host "    허용 소스: sg $($pv4.GroupId)" -ForegroundColor Gray }
        if ($perm.PrefixListIds) {
            foreach ($pl in $perm.PrefixListIds) {
                $plId = $pl.PrefixListId
                $plName = (aws ec2 describe-managed-prefix-lists --prefix-list-ids $plId --region $Region --query "PrefixLists[0].PrefixListName" --output text 2>$null)
                Write-Host "    허용 소스: PrefixList $plId ($plName)" -ForegroundColor Gray
                if ($plName -eq $eicPrefixListName) { $hasEic = $true }
            }
        }
    }
    # AWS 문서: 콘솔 Instance Connect 시 트래픽은 EC2 Instance Connect 서비스 IP에서 옴 -> 프리픽스 리스트 또는 0.0.0.0/0 필요
    if ($hasEic -or $hasOpen) {
        Write-Host "    -> SSH 소스 OK (Instance Connect 가능해야 함)" -ForegroundColor Green
    } else {
        Write-Host "    -> 주의: EC2 Instance Connect 서비스(프리픽스 리스트 '$eicPrefixListName') 또는 0.0.0.0/0 없음. 콘솔 연결 실패 가능." -ForegroundColor Yellow
    }
}

# 3) 프리픽스 리스트 존재 여부
Write-Host ""
Write-Host "[3] EC2 Instance Connect 프리픽스 리스트 (리전)" -ForegroundColor White
$plList = aws ec2 describe-managed-prefix-lists --region $Region --query "PrefixLists[?PrefixListName=='$eicPrefixListName'].[PrefixListId,PrefixListName]" --output text 2>$null
if ($plList) {
    Write-Host "  $eicPrefixListName : $plList" -ForegroundColor Green
} else {
    Write-Host "  (조회 실패 또는 해당 리전에 없음)" -ForegroundColor Gray
}

# 4) 서브넷 NACL (인바운드 22)
Write-Host ""
Write-Host "[4] 서브넷 NACL (인바운드 포트 22)" -ForegroundColor White
$subnetOut = aws ec2 describe-subnets --subnet-ids $subnetId --region $Region --query "Subnets[0].VpcId" --output text 2>$null
$vpcId = $subnetOut
$naclOut = aws ec2 describe-network-acls --region $Region --filters "Name=association.subnet-id,Values=$subnetId" --output json 2>$null
if ($naclOut) {
    $nacl = ($naclOut | ConvertFrom-Json).NetworkAcls[0]
    $inbound = $nacl.Entries | Where-Object { $_.Egress -eq $false -and (($_.RuleNumber -ge 1 -and $_.RuleNumber -le 32766)) }
    $port22Allow = $inbound | Where-Object { ($_.PortRange -and $_.PortRange.From -le 22 -and $_.PortRange.To -ge 22) -or $_.RuleNumber -eq 100 } | Where-Object { $_.RuleAction -eq "allow" }
    $port22Deny = $inbound | Where-Object { ($_.PortRange -and $_.PortRange.From -le 22 -and $_.PortRange.To -ge 22) } | Where-Object { $_.RuleAction -eq "deny" }
    if ($port22Deny -and $port22Deny.RuleNumber -lt ($port22Allow | ForEach-Object { $_.RuleNumber } | Sort-Object | Select-Object -First 1)) {
        Write-Host "  NACL $($nacl.NetworkAclId): 포트 22 인바운드 deny 규칙이 allow보다 우선 -> SSH 차단 가능" -ForegroundColor Red
    } elseif ($port22Allow) {
        Write-Host "  NACL $($nacl.NetworkAclId): 포트 22 인바운드 allow 있음" -ForegroundColor Green
    } else {
        Write-Host "  NACL $($nacl.NetworkAclId): 포트 22 인바운드 규칙 없음 (기본 deny면 SSH 차단)" -ForegroundColor Yellow
    }
} else {
    Write-Host "  (NACL 조회 생략 또는 기본 NACL)" -ForegroundColor Gray
}

# 5) 요약 및 권장
Write-Host ""
Write-Host "=== 요약 ===" -ForegroundColor Cyan
if (-not $publicIp) {
    Write-Host "  원인: 퍼블릭 IP 없음. 서브넷/인스턴스에 퍼블릭 IP 할당 필요." -ForegroundColor Red
} else {
    Write-Host "  퍼블릭 IP 있음. 위 [2][4]에서 SG/NACL 확인." -ForegroundColor Green
    Write-Host "  그래도 실패 시: 인스턴스 내 SSHD/ec2-instance-connect 패키지, IAM(콘솔 사용자 SendSSHPublicKey) 확인." -ForegroundColor Gray
}
Write-Host ""
