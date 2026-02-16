# 백엔드 재배포 명령어

---

# ① 캐시 기반 빠른 리빌드 (코드 수정 후 즉시 반영)

**코드를 수정했을 때** — 로컬 Docker 캐시로 빌드 → ECR 푸시 → EC2 배포까지 **한 방**.  
로컬에 Docker 설치되어 있어야 함.

## AWS 환경 변수 (한 번만)

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_ACCESS_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
```

## 원테이크 명령어 6종 (복붙 후 실행)

| 용도 | 명령어 |
|------|--------|
| **1) API만** | `cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget api` |
| **2) Video만** | `cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget video` |
| **3) AI만** | `cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget ai` |
| **4) Messaging만** | `cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget messaging` |
| **5) 전부** | `cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget all` |
| **6) 워커만(3종)** | `cd C:\academy; .\scripts\quick_redeploy.ps1 -DeployTarget workers` |

---

# ② 배포만 (ECR 이미지 이미 있을 때)

이미 ECR에 이미지가 있고, **빌드 없이 배포만** 할 때.

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
