# 백엔드 재배포 명령어

---

# 원테이크 풀셋팅 (빌드 서버까지 한 번에)

**최초 1회**: 아래 블록 순서대로 복붙. 루트 키로 빌드 EC2 생성 → 빌드 → ECR 푸시 → 전체 배포 → 빌드 인스턴스 중지.  
이후 버그 수정 등은 **재배포** 블록만 쓰면 됨.

## 1) 루트 액세스 키 (초기 셋팅용, 한 번만)

실제 값은 로컬에서만 설정. 저장소에 커밋하지 말 것.

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_ACCESS_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

## 2) 풀셋팅 실행 (빌드 서버 생성 + 빌드 + 전부 배포 + 빌드 서버 중지)

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git"
```

## 3) admin97 액세스 키 (일상 재배포용)

실제 값은 로컬에서만 설정. 저장소에 커밋하지 말 것.

```powershell
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
$env:AWS_ACCESS_KEY_ID = "YOUR_ADMIN97_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ADMIN97_SECRET_ACCESS_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

## 4) 재배포

- **빌드 미포함** (이미지만 배포):
```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -SkipBuild
```

- **빌드 포함** (코드 푸시 후 이미지 다시 빌드):
```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git"
```

## 5) 배포 확인 (원테이크)

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

---

# ① 캐시 기반 빠른 리빌드 (코드 수정 후 즉시 반영)

아래 **AWS 환경 변수**를 한 번 실행한 뒤, 수정한 대상에 맞는 **명령어 블록 하나만** 복붙해서 실행.

## AWS 환경 변수 (먼저 한 번만)

실제 값은 로컬에서만 설정.

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_SECRET_ACCESS_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

## 1) API만

```powershell
cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget api
```

## 2) Video만

```powershell
cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget video
```

## 3) AI만

```powershell
cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget ai
```

## 4) Messaging만

```powershell
cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget messaging
```

## 5) 전부 (API + Video + AI + Messaging)

```powershell
cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget all
```

## 6) 워커만 (Video + AI + Messaging)

```powershell
cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget workers
```

---

# ② 배포만 (ECR 이미지 이미 있을 때)

빌드 없이 **배포만** 할 때.  
**AWS 환경 변수**는 위 ①과 동일하게 한 번 설정한 뒤, 아래 중 하나만 복붙.

## 1) API만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget api
```

## 2) Video만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget video
```

## 3) AI만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget ai
```

## 4) Messaging만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget messaging
```

## 5) 전부

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -SkipBuild
```

## 6) 워커만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget workers
```

## 워커만 ASG 리프레시

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget workers -WorkersViaASG
```

---

# ③ 임시 빌드 인스턴스로 빌드 후 배포

로컬 Docker 없이 할 때. **임시 EC2 한 대**를 띄워서 그 위에서 빌드 → ECR 푸시 → 인스턴스 종료 → **기존** API/워커 EC2에만 배포.  
`guswls3028-art` 를 실제 GitHub 조직/계정으로 바꾸고, **AWS 환경 변수**는 ①과 동일하게 설정한 뒤 아래 중 하나만 복붙.

## 1) API만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget api
```

## 2) Video만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget video
```

## 3) AI만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget ai
```

## 4) Messaging만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget messaging
```

## 5) 전부

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git"
```

## 6) 워커만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy.git" -DeployTarget workers
```
