# 배포 셋팅 총정리 (2025-02-18)

**이 문서 하나로 배포 관련 전부 가능.**  
실제 액세스 키는 문서/채팅에 넣지 말 것. 노출 시 즉시 AWS에서 비활성화·삭제 후 새 키 발급.

---

## 1. 키 규칙 (반드시 지킬 것)

| 용도 | 사용할 키 | 비고 |
|------|------------|------|
| **풀배포, 캐시/노캐시 빌드, 워커 리프레시, API 배포, 배포 확인** | **루트** (또는 ECR+EC2+ASG 권한 있는 계정) | **한 세션에서 끝까지 같은 키**로 실행. 중간에 바꾸면 SSH 실패·워커 잘못 뜸 |
| **빌드 서버(academy-build-arm64) 수동 중지만** | admin97 | 풀배포 도중에 admin97으로 바꾸지 말 것 |

**루트 키 설정 (PowerShell):**
```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

**admin97 (빌드 서버 중지할 때만):**
```powershell
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
$env:AWS_ACCESS_KEY_ID = "YOUR_ADMIN97_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ADMIN97_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

---

## 2. 배포 전 검증 (권장)

```powershell
cd C:\academy
.\scripts\deploy_preflight.ps1
```

- 현재 AWS 계정, SSH 키 존재, 실행 중 인스턴스·ASG 확인.  
- 문제 없으면 **같은 터미널·같은 env**로 아래 풀배포 실행.

---

## 3. 풀배포 (빌드 + ECR 푸시 + API SSH + 워커 ASG 리프레시)

**캐시 사용 (보통):**
```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG
```

**노캐시 (설정/의존성 변경 후):**
```powershell
# 위와 동일 env
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG -NoCache
```

**워커만 리프레시 (빌드 생략, ECR 이미지 그대로):**
```powershell
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG -SkipBuild
```

- `full_redeploy.ps1 -WorkersViaASG`는 **Launch Template을 수정하지 않음**. 인스턴스 새로 고침만 함.  
- 워커가 ECS AMI로 뜨거나 컨테이너/100GB 문제 있으면 → **4. Launch Template 재적용** 후 다시 리프레시.

---

## 4. Launch Template 재적용 (워커가 잘못 뜰 때)

**증상:** Video 워커에 `academy-video-worker` 없고 ecs-agent만 있음 / 100GB 마운트 실패.

**한 번 실행:** LT를 일반 AL2023 AMI + 100GB BlockDevice로 갱신.

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
cd C:\academy
.\scripts\deploy_worker_asg.ps1 -SubnetIds "subnet-07a8427d3306ce910" -SecurityGroupId "sg-02692600fbf8e26f7" -IamInstanceProfileName "academy-ec2-role"
```

이후 워커만 새로 띄우기:
```powershell
aws autoscaling start-instance-refresh --region ap-northeast-2 --auto-scaling-group-name academy-video-worker-asg
# 또는 전체 워커: full_redeploy.ps1 ... -WorkersViaASG -SkipBuild
```

---

## 5. API 서버만 배포 (EC2 SSH)

**API 서버에 SSH 접속한 뒤:**
```bash
cd /home/ec2-user/academy && bash scripts/deploy_api_on_server.sh
```

최초 1회 `.env` 없으면:
```bash
aws ssm get-parameter --name /academy/workers/env --with-decryption --query Parameter.Value --output text --region ap-northeast-2 > /home/ec2-user/.env
```

---

## 6. 빌드 서버 수동 중지 (admin97 키로만)

```powershell
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
$env:AWS_ACCESS_KEY_ID = "YOUR_ADMIN97_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ADMIN97_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
$id = aws ec2 describe-instances --region ap-northeast-2 --filters "Name=tag:Name,Values=academy-build-arm64" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].InstanceId" --output text
if ($id -and $id -ne "None") { aws ec2 stop-instances --instance-ids $id --region ap-northeast-2; Write-Host "중지 요청함: $id" } else { Write-Host "실행 중인 academy-build-arm64 없음" }
```

---

## 7. 배포 종합 확인 (루트 키 설정 후)

```powershell
$region = "ap-northeast-2"
Write-Host "`n=== Worker deploy final check ===`n" -ForegroundColor Cyan
Write-Host "[1] Lambda" -ForegroundColor White
$lambda = aws lambda get-function --function-name academy-worker-queue-depth-metric --region $region --query "Configuration.FunctionName" --output text 2>$null
if ($lambda) { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  Missing" -ForegroundColor Red }
Write-Host "`n[2] Launch Template" -ForegroundColor White
$lt = aws ec2 describe-launch-templates --launch-template-names academy-ai-worker-asg academy-video-worker-asg academy-messaging-worker-asg --region $region 2>$null
if ($lt -match "LaunchTemplateName") { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  Missing" -ForegroundColor Red }
Write-Host "`n[2.5] LT AMI (ECS 아님)" -ForegroundColor White
foreach ($ltName in @("academy-ai-worker-asg","academy-video-worker-asg","academy-messaging-worker-asg")) {
  $amiId = aws ec2 describe-launch-template-versions --launch-template-name $ltName --region $region --query "LaunchTemplateVersions[0].LaunchTemplateData.ImageId" --output text 2>$null
  if (-not $amiId -or $amiId -eq "None") { Write-Host "  $ltName - (no image)" -ForegroundColor Yellow; continue }
  $amiName = aws ec2 describe-images --image-ids $amiId --region $region --query "Images[0].Name" --output text 2>$null
  if ($amiName -match "ecs") { Write-Host "  $ltName ECS AMI -> run deploy_worker_asg" -ForegroundColor Red } else { Write-Host "  $ltName OK" -ForegroundColor Green }
}
Write-Host "`n[3] ASG" -ForegroundColor White
$asg = aws autoscaling describe-auto-scaling-groups --region $region --output json 2>$null | ConvertFrom-Json
if ($asg.AutoScalingGroups.Count -gt 0) { $asg.AutoScalingGroups | ForEach-Object { Write-Host "  $($_.AutoScalingGroupName) Desired=$($_.DesiredCapacity)" -ForegroundColor Green } } else { Write-Host "  (0)" -ForegroundColor Yellow }
Write-Host "`n[4] ECR" -ForegroundColor White
@("academy-messaging-worker","academy-video-worker","academy-ai-worker-cpu") | ForEach-Object { $t = aws ecr list-images --repository-name $_ --region $region --query "imageIds[*].imageTag" --output text 2>$null; if ($t -match "latest") { Write-Host "  $_`:latest OK" -ForegroundColor Green } else { Write-Host "  $_ -" -ForegroundColor Yellow } }
Write-Host "`n[5] SSM" -ForegroundColor White
$ssm = aws ssm get-parameter --name /academy/workers/env --region $region --query "Parameter.Name" --output text 2>$null
if ($ssm) { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  Missing" -ForegroundColor Red }
Write-Host "`n=== Done ===`n" -ForegroundColor Cyan
```

---

## 8. 긴급 — Worker 껐다 켜짐 루프

인스턴스가 반복 종료될 때:
```powershell
cd C:\academy
.\scripts\remove_ec2_stop_from_worker_role.ps1
```

---

## 9. 문제 대응 요약

| 증상 | 원인 | 조치 |
|------|------|------|
| SSH 실패 / 워커 안 보임 | 풀배포 중 다른 키(admin97 등)로 바꿈 | 루트 키만 설정한 뒤 preflight → 풀배포 한 세션으로 다시 실행 |
| Video 워커에 컨테이너 없음, ecs-agent만 | LT가 ECS AMI 사용 | **4. Launch Template 재적용** 후 instance refresh |
| Video 100GB 마운트 실패 | LT/BlockDevice 또는 user_data 실패 | **4. Launch Template 재적용**. 인스턴스에서 `sudo cat /var/log/cloud-init-output.log` 확인 |

---

## 10. 로컬 필수 경로·키

- **키 디렉터리:** `C:\key`
- **필요 PEM:** `backend-api-key.pem`, `message-key.pem`, `ai-worker-key.pem`, `video-worker-key.pem`  
  (preflight에서 없으면 실패로 알려줌)

---

## 11. 상세 문서

- 전체 명령어·옵션: `docs_cursor/10-deploy-commands-cheatsheet.md`
- Video 워커 ASG·AMI·100GB: `docs_cursor/21-video-worker-asg-troubleshooting.md`
- Worker self-stop 원인: `docs_cursor/11-worker-self-stop-root-cause.md`
