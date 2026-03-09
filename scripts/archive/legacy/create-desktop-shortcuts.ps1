# Create desktop shortcuts for local development

$WshShell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath('Desktop')

# Backend shortcut
$BackendShortcut = $WshShell.CreateShortcut("$Desktop\Academy Backend.lnk")
$BackendShortcut.TargetPath = "C:\academy\scripts\run-local-backend.bat"
$BackendShortcut.WorkingDirectory = "C:\academy"
$BackendShortcut.Description = "Local Backend Server"
$BackendShortcut.Save()

# Frontend shortcut (프로젝트 경로: C:\academy\frontend)
$FrontendShortcut = $WshShell.CreateShortcut("$Desktop\Academy Frontend.lnk")
$FrontendShortcut.TargetPath = "C:\academy\frontend\scripts\run-local-frontend.bat"
$FrontendShortcut.WorkingDirectory = "C:\academy\frontend"
$FrontendShortcut.Description = "Local Frontend Server"
$FrontendShortcut.Save()

# Local Dev (Backend + Frontend) shortcut
$DevShortcut = $WshShell.CreateShortcut("$Desktop\Academy Local Dev.lnk")
$DevShortcut.TargetPath = "powershell.exe"
$DevShortcut.Arguments = "-NoExit -ExecutionPolicy Bypass -File `"C:\academy\scripts\run-local-dev.ps1`""
$DevShortcut.WorkingDirectory = "C:\academy"
$DevShortcut.Description = "Local Backend + Frontend Servers"
$DevShortcut.Save()

Write-Host "Desktop shortcuts created:" -ForegroundColor Green
Write-Host "  - Academy Backend.lnk" -ForegroundColor Cyan
Write-Host "  - Academy Frontend.lnk" -ForegroundColor Cyan
Write-Host "  - Academy Local Dev.lnk" -ForegroundColor Cyan
