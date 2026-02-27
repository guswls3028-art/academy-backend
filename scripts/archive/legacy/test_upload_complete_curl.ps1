# ==============================================================================
# POST /media/videos/{id}/upload/complete/ 로컬 재현 테스트
# ==============================================================================
# 사용:
#   .\scripts\test_upload_complete_curl.ps1 -VideoId 123 -BaseUrl "http://localhost:8000"
#   .\scripts\test_upload_complete_curl.ps1 -VideoId 123 -BaseUrl "https://api.hakwonplus.com" -Token "Bearer <JWT>"
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][int]$VideoId,
    [string]$BaseUrl = "http://localhost:8000",
    [string]$Token = ""
)

$uri = "$BaseUrl/api/v1/media/videos/$VideoId/upload/complete/"
$headers = @{
    "Content-Type" = "application/json"
}
if ($Token) { $headers["Authorization"] = $Token }

Write-Host "POST $uri" -ForegroundColor Cyan
Write-Host "Body: { `"ok`": true }" -ForegroundColor Gray

try {
    $body = '{"ok":true}'
    $response = Invoke-WebRequest -Uri $uri -Method POST -Headers $headers -Body $body -UseBasicParsing -TimeoutSec 60
    Write-Host "Status: $($response.StatusCode)" -ForegroundColor Green
    Write-Host $response.Content
} catch {
    Write-Host "Error: $_" -ForegroundColor Red
    if ($_.Exception.Response) {
        $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream())
        $reader.BaseStream.Position = 0
        Write-Host $reader.ReadToEnd()
    }
}
