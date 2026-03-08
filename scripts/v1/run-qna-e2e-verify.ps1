# 배포 API에서 질문 등록·목록 E2E 검증 (학생 455 JWT 사용).
# ALB 내부 URL로 호출(공개 URL은 Cloudflare에서 403 가능). X-Tenant-Code로 테넌트 해석.
# 사용: pwsh -File scripts/v1/run-qna-e2e-verify.ps1 [-AwsProfile default]
param([string]$AwsProfile = "default")
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
. (Join-Path $PSScriptRoot "resources\api.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") { $env:AWS_PROFILE = $AwsProfile.Trim() }
$null = Load-SSOT -Env "prod"
$ids = @(Get-APIASGInstanceIds)
if (-not $ids -or $ids.Count -eq 0) { Write-Host "No API instance"; exit 1 }
# ALB 내부 URL로 호출 후 Host 헤더로 테넌트 해석 (Cloudflare 403 회피)
$albDns = ""
if ($script:ApiAlbName) {
    try {
        $alb = Invoke-AwsJson @("elbv2", "describe-load-balancers", "--names", $script:ApiAlbName, "--region", $script:Region, "--output", "json")
        if ($alb -and $alb.LoadBalancers -and $alb.LoadBalancers.Count -gt 0) {
            $albDns = $alb.LoadBalancers[0].DNSName
        }
    } catch { }
}
$apiBase = if ($albDns) { "http://$albDns" } else { "https://api.hakwonplus.com" }
$hostHdr = if ($albDns) { "api.hakwonplus.com" } else { "" }
# 서버 env: Launch Template userdata가 SSM /academy/api/env → /opt/api.env 에 씀 (docs DEPLOY-API-ON-SERVER-FIX-REPORT)
$envFile = "/opt/api.env"
$ecrImg = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest"
$bashCmd = "source /etc/profile 2>/dev/null; export PATH=/usr/local/bin:/usr/bin:`$PATH; docker run --rm -e API_BASE_URL=$apiBase -e API_HOST_HEADER=$hostHdr --env-file $envFile $ecrImg python manage.py verify_qna_e2e 2>&1"
$params = @{ commands = @($bashCmd) } | ConvertTo-Json -Compress
$send = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $ids[0], "--document-name", "AWS-RunShellScript", "--parameters", $params, "--region", $script:Region, "--output", "json")
$cid = $send.Command.CommandId
Write-Host "QnA E2E 검증 실행 중 (최대 60초)..."
Start-Sleep -Seconds 12
$wait = 0
while ($wait -lt 60) {
    $inv = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cid, "--instance-id", $ids[0], "--region", $script:Region, "--output", "json")
    if ($inv.Status -eq "Success") {
        Write-Host "Status: $($inv.Status)"
        Write-Host $inv.StandardOutputContent
        if ($inv.StandardErrorContent) { Write-Host $inv.StandardErrorContent -ForegroundColor Yellow }
        if ($inv.StandardOutputContent -match "OK: 질문 등록 및 목록 노출 정상") { exit 0 }
        Write-Host "E2E 검증 실패: 예상 메시지 없음" -ForegroundColor Red
        exit 1
    }
    if ($inv.Status -eq "Failed" -or $inv.Status -eq "Cancelled") {
        Write-Host "Status: $($inv.Status)" -ForegroundColor Red
        Write-Host $inv.StandardOutputContent
        Write-Host $inv.StandardErrorContent -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Seconds 5
    $wait += 5
}
Write-Host "Timeout waiting for command"
exit 1
