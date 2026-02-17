# ==============================================================================
# Google Vision OCR JSON -> SSM /academy/google-vision-credentials
# Usage: .\scripts\upload_google_vision_to_ssm.ps1 [-JsonPath "C:\key\ocrkey\xxx.json"]
# ==============================================================================

param(
    [string]$JsonPath = "C:\key\ocrkey\mystic-benefit-480904-h1-93331a58ea78.json",
    [string]$Region = "ap-northeast-2",
    [string]$ParameterName = "/academy/google-vision-credentials"
)

$ErrorActionPreference = "Stop"
$jsonPath = [System.IO.Path]::GetFullPath($JsonPath)
if (-not (Test-Path -LiteralPath $jsonPath)) {
    Write-Host "ERROR: JSON file not found: $jsonPath" -ForegroundColor Red
    exit 1
}

$content = Get-Content -LiteralPath $jsonPath -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($content)) {
    Write-Host "ERROR: JSON file is empty: $jsonPath" -ForegroundColor Red
    exit 1
}

# SSM SecureString - JSON can be large, use Advanced tier if needed
$tier = if ($content.Length -gt 4096) { "Advanced" } else { "Standard" }
aws ssm put-parameter --name $ParameterName --type SecureString --value $content --overwrite --tier $tier --region $Region
if ($LASTEXITCODE -eq 0) {
    Write-Host "SSM $ParameterName updated. AI Worker user_data will fetch this on boot." -ForegroundColor Green
    exit 0
} else {
    Write-Host "Failed to upload to SSM." -ForegroundColor Red
    exit 1
}
