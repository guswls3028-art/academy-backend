# ==============================================================================
# Discover RDS instance network: VpcId, VpcSecurityGroups, Endpoint, Port.
# Prefer identifier academy-db; else -DbIdentifier. Saves docs/deploy/actual_state/rds_instance.json
# Usage: .\scripts\infra\discover_rds_network.ps1 -Region ap-northeast-2 [-DbIdentifier my-db]
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$DbIdentifier = "academy-db"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"
$OutFile = Join-Path $OutDir "rds_instance.json"

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$r = ExecJson "aws rds describe-db-instances --region $Region --output json 2>&1"
$db = $r.DBInstances | Where-Object { $_.DBInstanceIdentifier -eq $DbIdentifier } | Select-Object -First 1
if (-not $db) {
    Write-Host "FAIL: RDS instance '$DbIdentifier' not found. Use -DbIdentifier." -ForegroundColor Red
    exit 1
}

$vpcId = $db.DBSubnetGroup.VpcId
$sgIds = @($db.VpcSecurityGroups | ForEach-Object { $_.VpcSecurityGroupId })
$result = @{
    DBInstanceIdentifier = $db.DBInstanceIdentifier
    VpcId                = $vpcId
    VpcSecurityGroups    = $sgIds
    Endpoint             = $db.Endpoint.Address
    Port                 = $db.Endpoint.Port
    DBInstanceStatus     = $db.DBInstanceStatus
}
$json = $result | ConvertTo-Json
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($OutFile, $json, $utf8)
Write-Host "VpcId=$vpcId Endpoint=$($result.Endpoint) Port=$($result.Port) VpcSecurityGroups=$($sgIds -join ',')" -ForegroundColor Cyan
Write-Host "Saved: $OutFile" -ForegroundColor Gray
