# 비디오 빌드 동안 할 일 (복붙만 따라가기)

Video 이미지 빌드 돌아가는 동안 아래만 **순서대로** 복붙.

---

## 인스턴스·키 요약 (복붙 시 참고)

| 이름 | 퍼블릭 IP | SSH 키 (C:\key\) |
|------|-----------|------------------|
| academy-api | 15.165.48.212 | backend-api-key.pem |
| academy-messaging-worker | 3.38.143.25 | message-key.pem |
| academy-ai-worker-cpu | 3.37.175.245 | ai-worker-key.pem |
| academy-video-worker | 54.116.39.84 | video-worker-key.pem |

API 주소: `http://15.165.48.212:8000` (이미 .env.admin97 반영됨)

---

## A. 로컬 PowerShell (C:\academy에서)

### A1. ECR 로그인

```powershell
cd C:\academy
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
```

### A2. 태그 (video 제외 — 아직 빌드 중)

```powershell
docker tag academy-api:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
docker tag academy-messaging-worker:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker tag academy-ai-worker-cpu:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
```

### A3. 푸시 (video 제외)

```powershell
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
```

### A4. .env — API 주소 넣기

**반영 완료**: `.env.admin97` 에 `API_BASE_URL=http://15.165.48.212:8000` (academy-api 퍼블릭 IP) 넣어 둠. 변경할 일 없으면 A5로.

### A5. 배포용 .env 생성

```powershell
python scripts/prepare_deploy_env.py -o .env.deploy
```

### A6. 워커 3대에 .env 복사 (키: C:\key\)

```powershell
scp -i C:\key\message-key.pem C:\academy\.env.deploy ec2-user@3.38.143.25:~/.env
scp -i C:\key\ai-worker-key.pem C:\academy\.env.deploy ec2-user@3.37.175.245:~/.env
scp -i C:\key\video-worker-key.pem C:\academy\.env.deploy ec2-user@54.116.39.84:~/.env
```

---

## B. Messaging Worker (3.38.143.25)

### B1. SSH 접속

```powershell
ssh -i C:\key\message-key.pem ec2-user@3.38.143.25
```

### B2. Docker 설치 (최초 1회만)

```bash
sudo yum install -y docker && sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker ec2-user
exit
```

### B3. 다시 SSH 접속 후 아래 전부 복붙

```powershell
ssh -i C:\key\message-key.pem ec2-user@3.38.143.25
```

```bash
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker run -d --name academy-messaging-worker --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker update --restart unless-stopped academy-messaging-worker
```

---

## C. AI Worker (3.37.175.245)

### C1. SSH 접속

```powershell
ssh -i C:\key\ai-worker-key.pem ec2-user@3.37.175.245
```

### C2. Docker 설치 (최초 1회만)

```bash
sudo yum install -y docker && sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker ec2-user
exit
```

### C3. 다시 SSH 접속 후 아래 전부 복붙

```powershell
ssh -i C:\key\ai-worker-key.pem ec2-user@3.37.175.245
```

```bash
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
docker run -d --name academy-ai-worker-cpu --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 809466760795.d   kr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
docker update --restart unless-stopped academy-ai-worker-cpu
```

---

## D. Video Worker (54.116.39.84) — 이미지 빌드 끝나기 전까지 여기까지

### D1. SSH 접속

```powershell
ssh -i C:\key\video-worker-key.pem ec2-user@54.116.39.84
```

### D2. Docker 설치 (최초 1회만)

```bash
sudo yum install -y docker && sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker ec2-user
exit
```

### D3. 다시 SSH 접속 → 100GB 마운트

```powershell
ssh -i C:\key\video-worker-key.pem ec2-user@54.116.39.84
```

```bash
lsblk
```

`nvme1n1` 이면 (파티션 없음):

```bash
sudo mkfs -t ext4 /dev/nvme1n1
sudo mkdir -p /mnt/transcode
sudo mount /dev/nvme1n1 /mnt/transcode
echo '/dev/nvme1n1 /mnt/transcode ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
df -h
```

`nvme1n1p1` 이면:

```bash
sudo mkfs -t ext4 /dev/nvme1n1p1
sudo mkdir -p /mnt/transcode
sudo mount /dev/nvme1n1p1 /mnt/transcode
echo '/dev/nvme1n1p1 /mnt/transcode ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
df -h
```

`.env` 있는지 확인:

```bash
ls -la .env
```

---

## E. Video 이미지 빌드 끝난 뒤 (로컬 → EC2)

### E1. 로컬 PowerShell — video 태그 & 푸시

```powershell
cd C:\academy
docker tag academy-video-worker:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
```

### E2. Video EC2 SSH 접속 후 컨테이너 실행

```bash
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker run -d --name academy-video-worker --restart unless-stopped --memory 4g --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 -v /mnt/transcode:/tmp 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker update --restart unless-stopped academy-video-worker
```

---

**정리**: A → B → C → D 까지 하면 비디오 빌드 동안 할 수 있는 건 끝. E는 비디오 이미지 빌드 완료 후에.
