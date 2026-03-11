# ==============================================================================
# POST /media/videos/{id}/upload/complete/ 운영 검증
# ==============================================================================
# 사용: pwsh scripts/v1/test-upload-complete.ps1 -VideoId 187 -Token "Bearer <JWT>"
# JWT: hakwonplus.com 로그인 후 localStorage.getItem("access") 또는 DevTools Application
# ==============================================================================
param(
    [Parameter(Mandatory=$true)][int]$VideoId,
    [string]$BaseUrl = "https://api.hakwonplus.com",
    [string]$Token = "",
    [string]$TenantCode = "hakwonplus"
)

$uri = "$BaseUrl/api/v1/media/videos/$VideoId/upload/complete/"
$headers = @{
    "Content-Type"     = "application/json"
    "X-Tenant-Code"    = $TenantCode
}
if ($Token) { $headers["Authorization"] = $Token }

Write-Host "POST $uri" -ForegroundColor Cyan
Write-Host "X-Tenant-Code: $TenantCode" -ForegroundColor Gray
if (-not $Token) { Write-Host "WARN: Token 없음. 401 예상." -ForegroundColor Yellow }

try {
    $body = '{"ok":true}'
    $response = Invoke-WebRequest -Uri $uri -Method POST -Headers $headers -Body $body -UseBasicParsing -TimeoutSec 60
    Write-Host "Status: $($response.StatusCode)" -ForegroundColor Green
    Write-Host $response.Content
} catch {
    Write-Host "Error: $_" -ForegroundColor Red
    if ($_.Exception.Response) {
        $stream = $_.Exception.Response.GetResponseStream()
        if ($stream) {
            $reader = [System.IO.StreamReader]::new($stream)
            $reader.BaseStream.Position = 0
            Write-Host $reader.ReadToEnd()
        }
    }
}
