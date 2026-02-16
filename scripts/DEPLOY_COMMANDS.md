# 백엔드 재배포 명령어 (Git 푸시 이후)

AWS 액세스 키 설정 후 `cd C:\academy` 에서 실행.  
`YOUR_ORG` 는 실제 GitHub 조직/유저로 바꾸세요.

---

## 1) API 서버만

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget api
```

---

## 2) Video 워커만

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget video
```

---

## 3) AI 워커만

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget ai
```

---

## 4) Messaging 워커만

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget messaging
```

---

## 5) 전부 (API + Video + AI + Messaging)

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git"
```

---

## 6) 워커만 (Video + AI + Messaging, API 제외)

```powershell
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget workers
```

---

## 빌드 생략 (이미 ECR에 최신 이미지 있을 때)

위 명령 끝에 **`-SkipBuild`** 추가. 예:

```powershell
.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget api
.\scripts\full_redeploy.ps1 -SkipBuild -DeployTarget workers
```

## 워커를 ASG 리프레시로만 배포

고정 EC2 SSH 대신 ASG 인스턴스 리프레시만 하려면 **`-WorkersViaASG`** 추가.

```powershell
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/YOUR_ORG/academy.git" -DeployTarget workers -WorkersViaASG
```
