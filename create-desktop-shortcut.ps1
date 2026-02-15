# deploy-run.bat 바탕화면 바로가기 생성 (한 번만 실행)
$bat = "C:\academy\deploy-run.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("$desktop\EC2배포.lnk")
$sc.TargetPath = $bat
$sc.WorkingDirectory = "C:\academy"
$sc.Description = "EC2 4대 docker compose 배포"
$sc.Save()
Write-Host "바탕화면에 'EC2배포' 바로가기가 생성되었습니다." -ForegroundColor Green
