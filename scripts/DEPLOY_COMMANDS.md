# 백엔드 재배포 명령어

---

# ① 캐시 기반 빠른 리빌드 (코드 수정 후 즉시 반영)

아래 **AWS 환경 변수**를 한 번 실행한 뒤, 수정한 대상에 맞는 **명령어 블록 하나만** 복붙해서 실행.

## AWS 환경 변수 (먼저 한 번만)

```powershell
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_ACCESS_KEY"
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
`YOUR_ORG` 를 실제 GitHub 조직/계정으로 바꾸고, **AWS 환경 변수**는 ①과 동일하게 설정한 뒤 아래 중 하나만 복붙.

## 1) API만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget api
```

## 2) Video만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget video
```

## 3) AI만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget ai
```

## 4) Messaging만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget messaging
```

## 5) 전부

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git"
```

## 6) 워커만

```powershell
cd C:\academy; .\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget workers
```
