# ==============================================================================
# Discover API EC2 instance network: VpcId, SubnetId, SecurityGroupIds, PrivateIp, PublicIp.
# Prefer tag Name=academy-api; else use -InstanceId. Saves docs/deploy/actual_state/api_instance.json
# Usage: .\scripts\infra\discover_api_network.ps1 -Region ap-northeast-2 [-InstanceId i-xxx]
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$InstanceId = "",
    [string]$NameTag = "academy-api"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"
$OutFile = Join-Path $OutDir "api_instance.json"

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$inst = $null
if ($InstanceId) {
    $r = ExecJson "aws ec2 describe-instances --instance-ids $InstanceId --region $Region --output json 2>&1"
    $inst = $r.Reservations | ForEach-Object { $_.Instances } | Where-Object { $_.InstanceId -eq $InstanceId } | Select-Object -First 1
} else {
    $r = ExecJson "aws ec2 describe-instances --filters `"Name=tag:Name,Values=$NameTag`" `"Name=instance-state-name,Values=running`" --region $Region --output json 2>&1"
    $inst = $r.Reservations | ForEach-Object { $_.Instances } | Select-Object -First 1
}

if (-not $inst) {
    Write-Host "FAIL: API instance not found. Use -InstanceId i-xxx or ensure tag Name=$NameTag exists." -ForegroundColor Red
    exit 1
}

$eni = $inst.NetworkInterfaces | Select-Object -First 1
$result = @{
    InstanceId       = $inst.InstanceId
    VpcId            = $inst.VpcId
    SubnetId         = $inst.SubnetId
    SecurityGroupIds = @($inst.SecurityGroups | ForEach-Object { $_.GroupId })
    PrivateIpAddress = $inst.PrivateIpAddress
    PublicIpAddress  = $inst.PublicIpAddress
    State            = $inst.State.Name
}
$json = $result | ConvertTo-Json
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($OutFile, $json, $utf8)
Write-Host "VpcId=$($result.VpcId) SubnetId=$($result.SubnetId) SecurityGroupIds=$($result.SecurityGroupIds -join ',')" -ForegroundColor Cyan
Write-Host "Saved: $OutFile" -ForegroundColor Gray
