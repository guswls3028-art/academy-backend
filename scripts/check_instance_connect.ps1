# EC2 Instance Connect failure checker (queries actual AWS state, no guesswork)
# Run: .\scripts\check_instance_connect.ps1 [-InstanceId i-xxx] [-Region ap-northeast-2]
# Requires AWS CLI configured.
param(
    [string]$InstanceId = "i-0f4d0ac79f281ee9c",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
Write-Host ""
Write-Host "=== EC2 Instance Connect check (actual config) ===" -ForegroundColor Cyan
Write-Host "  InstanceId: $InstanceId , Region: $Region" -ForegroundColor Gray
Write-Host ""

# 1) Instance
Write-Host "[1] Instance" -ForegroundColor White
$inst = aws ec2 describe-instances --instance-ids $InstanceId --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Error: $inst" -ForegroundColor Red
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
if ($publicIp) {
    Write-Host "  PublicIP:    $publicIp" -ForegroundColor Green
} else {
    Write-Host "  PublicIP:    (none - browser Instance Connect needs public IP)" -ForegroundColor Red
}
Write-Host "  PrivateIP:   $privateIp" -ForegroundColor Gray
Write-Host "  SubnetId:    $subnetId" -ForegroundColor Gray
Write-Host "  LaunchTime:  $launchTime" -ForegroundColor Gray
Write-Host "  SecurityGroups: $($sgIds -join ', ')" -ForegroundColor Gray

if (-not $publicIp) {
    Write-Host ""
    Write-Host "  Cause: No public IP. Enable auto-assign public IP on subnet or attach Elastic IP." -ForegroundColor Red
}

# 2) Security group port 22
Write-Host ""
Write-Host "[2] Security group inbound (SSH 22)" -ForegroundColor White
$eicPrefixListName = "${Region}.ec2-instance-connect"

foreach ($sgId in $sgIds) {
    $sgOut = aws ec2 describe-security-groups --group-ids $sgId --region $Region --output json 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "  SG $sgId : describe failed" -ForegroundColor Red; continue }
    $sg = ($sgOut | ConvertFrom-Json).SecurityGroups[0]
    $sgName = $sg.GroupName
    $port22 = $sg.IpPermissions | Where-Object { $_.FromPort -eq 22 -and $_.ToPort -eq 22 }
    if (-not $port22) {
        Write-Host "  SG $sgId ($sgName): no port 22 inbound -> SSH/Instance Connect blocked" -ForegroundColor Red
        continue
    }
    Write-Host "  SG $sgId ($sgName):" -ForegroundColor Gray
    $hasEic = $false
    $hasOpen = $false
    foreach ($perm in $port22) {
        foreach ($ip in ($perm.IpRanges)) {
            $cidr = $ip.CidrIp
            Write-Host "    Source: $cidr" -ForegroundColor Gray
            if ($cidr -eq "0.0.0.0/0") { $hasOpen = $true }
        }
        foreach ($pv4 in ($perm.UserIdGroupPairs)) { Write-Host "    Source: sg $($pv4.GroupId)" -ForegroundColor Gray }
        if ($perm.PrefixListIds) {
            foreach ($pl in $perm.PrefixListIds) {
                $plId = $pl.PrefixListId
                $plName = (aws ec2 describe-managed-prefix-lists --prefix-list-ids $plId --region $Region --query "PrefixLists[0].PrefixListName" --output text 2>$null)
                Write-Host "    Source: PrefixList $plId ($plName)" -ForegroundColor Gray
                if ($plName -eq $eicPrefixListName) { $hasEic = $true }
            }
        }
    }
    if ($hasEic -or $hasOpen) {
        Write-Host "    -> SSH source OK (Instance Connect should work)" -ForegroundColor Green
    } else {
        Write-Host "    -> Warning: no 0.0.0.0/0 or prefix list $eicPrefixListName; console connect may fail." -ForegroundColor Yellow
    }
}

# 3) Prefix list
Write-Host ""
Write-Host "[3] EC2 Instance Connect prefix list" -ForegroundColor White
$plList = aws ec2 describe-managed-prefix-lists --region $Region --query "PrefixLists[?PrefixListName=='$eicPrefixListName'].[PrefixListId,PrefixListName]" --output text 2>$null
if ($plList) {
    Write-Host "  $eicPrefixListName : $plList" -ForegroundColor Green
} else {
    Write-Host "  (not found or no permission)" -ForegroundColor Gray
}

# 4) Subnet NACL
Write-Host ""
Write-Host "[4] Subnet NACL (inbound port 22)" -ForegroundColor White
$subnetOut = aws ec2 describe-subnets --subnet-ids $subnetId --region $Region --query "Subnets[0].VpcId" --output text 2>$null
$vpcId = $subnetOut
$naclOut = aws ec2 describe-network-acls --region $Region --filters "Name=association.subnet-id,Values=$subnetId" --output json 2>$null
if ($naclOut) {
    $nacl = ($naclOut | ConvertFrom-Json).NetworkAcls[0]
    $inbound = $nacl.Entries | Where-Object { $_.Egress -eq $false -and (($_.RuleNumber -ge 1 -and $_.RuleNumber -le 32766)) }
    $port22Allow = $inbound | Where-Object { ($_.PortRange -and $_.PortRange.From -le 22 -and $_.PortRange.To -ge 22) -or $_.RuleNumber -eq 100 } | Where-Object { $_.RuleAction -eq "allow" }
    $port22Deny = $inbound | Where-Object { ($_.PortRange -and $_.PortRange.From -le 22 -and $_.PortRange.To -ge 22) } | Where-Object { $_.RuleAction -eq "deny" }
    $minAllowNum = ($port22Allow | ForEach-Object { $_.RuleNumber } | Sort-Object | Select-Object -First 1)
    if ($port22Deny -and $minAllowNum -and $port22Deny.RuleNumber -lt $minAllowNum) {
        Write-Host "  NACL $($nacl.NetworkAclId): port 22 deny before allow -> SSH may be blocked" -ForegroundColor Red
    } elseif ($port22Allow) {
        Write-Host "  NACL $($nacl.NetworkAclId): port 22 allow present" -ForegroundColor Green
    } else {
        Write-Host "  NACL $($nacl.NetworkAclId): no port 22 rule (default deny may block SSH)" -ForegroundColor Yellow
    }
} else {
    Write-Host "  (NACL skip or default)" -ForegroundColor Gray
}

# 5) Summary
Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Cyan
if (-not $publicIp) {
    Write-Host "  Likely cause: No public IP. Assign public IP on subnet or instance." -ForegroundColor Red
} else {
    Write-Host "  Public IP present. Check [2] and [4] for SG/NACL." -ForegroundColor Green
    Write-Host "  If still failing: SSHD, ec2-instance-connect on instance; IAM SendSSHPublicKey for console user." -ForegroundColor Gray
}
Write-Host ""
