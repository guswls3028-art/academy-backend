# 배포 명령어 모음 (로컬/터미널용)

**주의: 실제 액세스 키는 이 파일에 넣지 말 것. 로컬에서만 `$env:...` 로 넣거나 별도 메모에 보관.**

---

## 1. env 넣는 법 (PowerShell)

**루트 액세스 키** (풀배포/ECR/빌드 인스턴스 제어용):

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

**admin97 액세스 키** (빌드 서버 중지 등, 권한 다를 수 있음):

```powershell
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
$env:AWS_ACCESS_KEY_ID = "YOUR_ADMIN97_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ADMIN97_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

---

## 2. API 서버에서 (SSH 접속 후)

```bash
cd /home/ec2-user/academy && bash scripts/deploy_api_on_server.sh
```

---

## 3. 코드 수정 후 캐시 기반 풀배포 (빌드 서버 사용)

```powershell
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG
```

---

## 4. 노캐시 풀배포 (빌드 서버 사용)

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG -NoCache
```

---

## 5. 빌드용 서버 수동 중지 (admin97 키로)

```powershell
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
$env:AWS_ACCESS_KEY_ID = "YOUR_ADMIN97_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ADMIN97_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
$id = aws ec2 describe-instances --region ap-northeast-2 --filters "Name=tag:Name,Values=academy-build-arm64" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].InstanceId" --output text
if ($id -and $id -ne "None") { aws ec2 stop-instances --instance-ids $id --region ap-northeast-2; Write-Host "중지 요청함: $id" } else { Write-Host "실행 중인 academy-build-arm64 없음" }
```

---

## 6. 배포 확인 명령어

**먼저 루트 액세스 키 env 설정** (1번 블록) 한 뒤 실행.

```powershell
$region = "ap-northeast-2"
Write-Host "`n=== Worker deploy final check ===`n" -ForegroundColor Cyan
Write-Host "[1] Lambda" -ForegroundColor White
$lambda = aws lambda get-function --function-name academy-worker-queue-depth-metric --region $region --query "Configuration.FunctionName" --output text 2>$null
if ($lambda) { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  Missing" -ForegroundColor Red }
Write-Host "`n[2] Launch Template" -ForegroundColor White
$lt = aws ec2 describe-launch-templates --launch-template-names academy-ai-worker-asg academy-video-worker-asg academy-messaging-worker-asg --region $region 2>$null
if ($lt -match "LaunchTemplateName") { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  Missing" -ForegroundColor Red }
Write-Host "`n[3] ASG" -ForegroundColor White
$asg = aws autoscaling describe-auto-scaling-groups --region $region --output json 2>$null | ConvertFrom-Json
if ($asg.AutoScalingGroups.Count -gt 0) { $asg.AutoScalingGroups | ForEach-Object { Write-Host "  $($_.AutoScalingGroupName) Desired=$($_.DesiredCapacity) Min=$($_.MinSize) Max=$($_.MaxSize)" -ForegroundColor Green } } else { Write-Host "  (0 - check console)" -ForegroundColor Yellow }
Write-Host "`n[4] ECR" -ForegroundColor White
@("academy-messaging-worker","academy-video-worker","academy-ai-worker-cpu") | ForEach-Object { $t = aws ecr list-images --repository-name $_ --region $region --query "imageIds[*].imageTag" --output text 2>$null; if ($t -match "latest") { Write-Host "  $_`:latest OK" -ForegroundColor Green } else { Write-Host "  $_ -" -ForegroundColor Yellow } }
Write-Host "`n[5] SSM" -ForegroundColor White
$ssm = aws ssm get-parameter --name /academy/workers/env --region $region --query "Parameter.Name" --output text 2>$null
if ($ssm) { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  Missing" -ForegroundColor Red }
Write-Host "`n[6] API SG <- Worker" -ForegroundColor White
$ing = aws ec2 describe-security-groups --group-ids sg-0051cc8f79c04b058 --region $region --query "SecurityGroups[0].IpPermissions" --output json 2>$null
if ($ing -match "sg-02692600fbf8e26f7") { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  Check" -ForegroundColor Yellow }
Write-Host "`n=== Done ===`n" -ForegroundColor Cyan
```
