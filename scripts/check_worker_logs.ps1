# ==============================================================================
# 워커 로그 확인 스크립트
# Usage: .\scripts\check_worker_logs.ps1 [video|ai|messaging] [-Tail 100]
# ==============================================================================

param(
    [ValidateSet("video", "ai", "messaging", "all")]
    [string]$WorkerType = "all",
    [int]$Tail = 50,
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptRoot "_config_instance_keys.ps1")

# 워커 타입별 이름 매핑
$workerMap = @{
    "video" = "academy-video-worker"
    "ai" = "academy-ai-worker-cpu"
    "messaging" = "academy-messaging-worker"
}

# ASG 인스턴스 조회
function Get-ASGInstances {
    param([string]$AsgName)
    
    $instances = aws autoscaling describe-auto-scaling-groups `
        --region $Region `
        --auto-scaling-group-names $AsgName `
        --query "AutoScalingGroups[0].Instances[?HealthStatus=='Healthy'].[InstanceId]" `
        --output text 2>&1
    
    if ($LASTEXITCODE -eq 0 -and $instances) {
        return $instances -split "\s+" | Where-Object { $_ }
    }
    return @()
}

# EC2 인스턴스 IP 조회
function Get-InstanceIP {
    param([string]$InstanceId)
    
    $ip = aws ec2 describe-instances `
        --region $Region `
        --instance-ids $InstanceId `
        --query "Reservations[0].Instances[0].PublicIpAddress" `
        --output text 2>&1
    
    if ($LASTEXITCODE -eq 0 -and $ip -and $ip -ne "None") {
        return $ip
    }
    return $null
}

# 워커 로그 확인
function Show-WorkerLogs {
    param(
        [string]$WorkerName,
        [string]$InstanceId,
        [string]$IP
    )
    
    Write-Host "`n=== $WorkerName (Instance: $InstanceId, IP: $IP) ===" -ForegroundColor Cyan
    
    if (-not $IP) {
        Write-Host "  [SKIP] No public IP (ASG instance may be in private subnet)" -ForegroundColor Yellow
        Write-Host "  Use AWS Systems Manager Session Manager instead:" -ForegroundColor Gray
        Write-Host "    aws ssm start-session --target $InstanceId --region $Region" -ForegroundColor Gray
        return
    }
    
    $keyFile = Join-Path $KeyDir $INSTANCE_KEY_FILES[$WorkerName]
    if (-not (Test-Path $keyFile)) {
        Write-Host "  [FAIL] Key file not found: $keyFile" -ForegroundColor Red
        return
    }
    
    $containerName = $WorkerName.Replace("academy-", "").Replace("-worker", "-worker").Replace("-cpu", "")
    if ($WorkerName -eq "academy-ai-worker-cpu") {
        $containerName = "academy-ai-worker-cpu"
    }
    
    Write-Host "  Checking Docker container: $containerName" -ForegroundColor Gray
    
    # Docker 로그 확인 (sudo 필요)
    $logCmd = "sudo docker logs --tail $Tail $containerName 2>&1"
    $sshCmd = "ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -i `"$keyFile`" ec2-user@$IP `"$logCmd`""
    
    Write-Host ""
    Invoke-Expression $sshCmd
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] SSH or Docker command failed" -ForegroundColor Red
    }
}

# 메인 로직
Write-Host "`n=== 워커 로그 확인 ===" -ForegroundColor Cyan
Write-Host "WorkerType: $WorkerType, Tail: $Tail lines`n" -ForegroundColor Gray

if ($WorkerType -eq "all") {
    $workers = @("video", "ai", "messaging")
} else {
    $workers = @($WorkerType)
}

foreach ($wt in $workers) {
    $workerName = $workerMap[$wt]
    $asgName = switch ($wt) {
        "video" { "academy-video-worker-asg" }
        "ai" { "academy-ai-worker-asg" }
        "messaging" { "academy-messaging-worker-asg" }
    }
    
    Write-Host "`n[$wt] Finding instances in ASG: $asgName" -ForegroundColor Yellow
    
    $instanceIds = Get-ASGInstances -AsgName $asgName
    
    if ($instanceIds.Count -eq 0) {
        Write-Host "  [WARN] No healthy instances found in ASG" -ForegroundColor Yellow
        continue
    }
    
    foreach ($instanceId in $instanceIds) {
        $ip = Get-InstanceIP -InstanceId $instanceId
        Show-WorkerLogs -WorkerName $workerName -InstanceId $instanceId -IP $ip
    }
}

Write-Host "`n=== 완료 ===" -ForegroundColor Green
Write-Host "`n실시간 로그 보기:" -ForegroundColor Gray
Write-Host "  .\scripts\check_worker_logs.ps1 video -Tail 100" -ForegroundColor Gray
Write-Host "`n또는 SSH로 직접 접속:" -ForegroundColor Gray
Write-Host "  ssh -i C:\key\<key-file> ec2-user@<IP>" -ForegroundColor Gray
Write-Host "  sudo docker logs -f academy-video-worker" -ForegroundColor Gray
