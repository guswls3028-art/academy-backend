# 백엔드 재배포 — ECR 이미지 있을 때 (코드 수정용만)

ECR에 이미지가 이미 있을 때, **빌드 없이 배포만** 할 때 사용.  
먼저 AWS 액세스 키 설정 후 `cd C:\academy` 에서 아래 명령 실행.

---

## AWS 환경 변수 (루트 액세스 키)

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_ACCESS_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

---

## 1) API 서버만

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget api
```

---

## 2) Video 워커만

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget video
```

---

## 3) AI 워커만

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget ai
```

---

## 4) Messaging 워커만

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget messaging
```

---

## 5) 전부 (API + Video + AI + Messaging)

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -SkipBuild
```

---

## 6) 워커만 (Video + AI + Messaging, API 제외)

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget workers
```

---

## 워커만 ASG 리프레시로 배포

고정 EC2 SSH 대신 ASG 인스턴스 리프레시만 할 때:

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget workers -WorkersViaASG
```
