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

# file:// with temp copy: UTF8 no BOM (AWS CLI fails on BOM)
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tempFile = Join-Path $repoRoot "temp_google_vision_upload.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($tempFile, $content, $utf8NoBom)
try {
    Push-Location $repoRoot
    $tier = if ($content.Length -gt 4096) { "Advanced" } else { "Standard" }
    aws ssm put-parameter --name $ParameterName --type SecureString --value "file://temp_google_vision_upload.json" --overwrite --tier $tier --region $Region
} finally {
    Pop-Location
    Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
}
if ($LASTEXITCODE -eq 0) {
    Write-Host "SSM $ParameterName updated. AI Worker user_data will fetch this on boot." -ForegroundColor Green
    exit 0
} else {
    Write-Host "Failed to upload to SSM." -ForegroundColor Red
    exit 1
}
