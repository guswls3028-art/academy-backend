# Create 3 desktop shortcuts (ECR cache / ECR no-cache / EC2 deploy)
$ErrorActionPreference = "Stop"
$baseDir = $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")

$WshShell = New-Object -ComObject WScript.Shell

$items = @(
    @{ Name = "ECR-cache"; Bat = "1-ecr-cache.bat" },
    @{ Name = "ECR-nocache"; Bat = "2-ecr-nocache.bat" },
    @{ Name = "EC2-4-deploy"; Bat = "3-ec2-deploy.bat" }
)
foreach ($item in $items) {
    $lnkPath = Join-Path $desktop ($item.Name + ".lnk")
    $lnk = $WshShell.CreateShortcut($lnkPath)
    $lnk.TargetPath = Join-Path $baseDir $item.Bat
    $lnk.WorkingDirectory = $baseDir
    $lnk.Save()
    Write-Host "OK: $($item.Name)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Desktop: $desktop" -ForegroundColor Cyan
