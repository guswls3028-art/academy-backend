# 배포 명령어 모음 (로컬/터미널용)

**주의: 실제 액세스 키는 이 파일에 넣지 말 것. 로컬에서만 `$env:...` 로 넣거나 별도 메모에 보관.**

---

## 0. 긴급 — Worker 껐다 켜짐 루프 차단 (IAM)

Worker self-stop으로 인스턴스가 반복 종료될 때 **즉시 차단**:

```powershell
cd C:\academy
.\scripts\remove_ec2_stop_from_worker_role.ps1
```

- `academy-ec2-role`에 `ec2:StopInstances` Deny 정책 추가
- ASG 스케일 인(TerminateInstances)은 영향 없음
- 상세: `docs_cursor/11-worker-self-stop-root-cause.md`

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

## 1.5 Google Vision OCR 키 → AI Worker (최초 1회)

로컬 JSON을 SSM에 올리면 AI Worker 부팅 시 자동 적용:

```powershell
cd C:\academy
.\scripts\upload_google_vision_to_ssm.ps1 -JsonPath "C:\key\ocrkey\mystic-benefit-480904-h1-93331a58ea78.json"
```

- SSM `/academy/google-vision-credentials`에 JSON 저장
- IAM `academy-ec2-role`에 GetParameter 권한 필요 (`infra/worker_asg/iam_policy_ec2_worker.json` 기준)
- 적용: Launch Template 재배포 + ASG instance refresh 후 새 인스턴스부터 반영

---

## 2. API 서버에서 (SSH 접속 후)

**최초 1회:** 서버에 `.env` 없으면 SSM에서 받기:
```bash
aws ssm get-parameter --name /academy/workers/env --with-decryption --query Parameter.Value --output text --region ap-northeast-2 > /home/ec2-user/.env
```

그 다음 배포:
```bash
cd /home/ec2-user/academy && bash scripts/deploy_api_on_server.sh
```
(스크립트는 `--env-file /home/ec2-user/.env` 사용)

---

## 3. 풀배포 — 빌드 + API + 워커(ASG instance refresh)

**현재 기본 워크플로우.** 빌드 인스턴스에서 이미지 빌드 → ECR 푸시 → API SSH 배포 → 워커 ASG instance refresh.

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG
```

---

## 4. 풀배포 — 노캐시 (설정 변경 후)

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG -NoCache
```

---

## 5. 워커만 리프레시 (빌드 스킵, ECR 이미지 그대로)

이미지가 최신이고 워커 ASG만 새로 띄울 때:

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG -SkipBuild
```

---

## 6. 빌드용 서버 수동 중지 (admin97 키로)

```powershell
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY -ErrorAction SilentlyContinue
$env:AWS_ACCESS_KEY_ID = "YOUR_ADMIN97_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ADMIN97_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
$id = aws ec2 describe-instances --region ap-northeast-2 --filters "Name=tag:Name,Values=academy-build-arm64" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].InstanceId" --output text
if ($id -and $id -ne "None") { aws ec2 stop-instances --instance-ids $id --region ap-northeast-2; Write-Host "중지 요청함: $id" } else { Write-Host "실행 중인 academy-build-arm64 없음" }
```

---

## 7. ASG 워커 상태 확인

```powershell
$region = "ap-northeast-2"
aws autoscaling describe-auto-scaling-groups --region $region --query "AutoScalingGroups[?contains(AutoScalingGroupName,'academy')].{Name:AutoScalingGroupName,Min:MinSize,Desired:DesiredCapacity,Max:MaxSize,Instances:Instances[*].[InstanceId,LifecycleState,HealthStatus]}" --output table
```

특정 ASG 인스턴스만:
```powershell
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-ai-worker-asg --region ap-northeast-2 --query "AutoScalingGroups[0].Instances[*].[InstanceId,LifecycleState,HealthStatus]" --output table
```

---

## 8. 배포 종합 확인

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
Write-Host "`n[6] IAM ec2:StopInstances Deny (Worker self-stop 차단)" -ForegroundColor White
$deny = aws iam get-role-policy --role-name academy-ec2-role --policy-name academy-deny-ec2-stop-instances 2>$null
if ($deny -match "DenyStopInstances") { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  없음 (필요시: .\scripts\remove_ec2_stop_from_worker_role.ps1)" -ForegroundColor Yellow }
Write-Host "`n[7] API SG <- Worker" -ForegroundColor White
$ing = aws ec2 describe-security-groups --group-ids sg-0051cc8f79c04b058 --region $region --query "SecurityGroups[0].IpPermissions" --output json 2>$null
if ($ing -match "sg-02692600fbf8e26f7") { Write-Host "  OK" -ForegroundColor Green } else { Write-Host "  Check" -ForegroundColor Yellow }
Write-Host "`n=== Done ===`n" -ForegroundColor Cyan
```

---

## full_redeploy.ps1 옵션 요약

| 옵션 | 설명 |
|------|------|
| `-GitRepoUrl "URL"` | 빌드 시 clone할 Git URL (SkipBuild 아닐 때 필수) |
| `-WorkersViaASG` | 워커를 고정 EC2 SSH 대신 ASG instance refresh로 배포 |
| `-SkipBuild` | 빌드 생략, ECR 이미지 그대로 워커만 리프레시 |
| `-NoCache` | Docker 빌드 시 `--no-cache` |
| `-DeployTarget api\|video\|ai\|messaging\|workers` | 특정 타겟만 배포 |
