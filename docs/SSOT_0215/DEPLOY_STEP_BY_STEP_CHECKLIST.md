# 500 배포 단계별 체크리스트 (복붙용)

**용도**: 한 단계씩 체크하면서 복붙으로 진행. 상세는 `AWS_500_START_DEPLOY_GUIDE.md` 참고.

**진행 반영**: 아래 [x]는 대화에서 실행 확인된 것 + 프로젝트 파일(`.env.deploy`, `.env.admin97` 존재) 기준. 미체크는 프로젝트/대화에서 확인 불가.

---

## 전제: 인프라 준비 (콘솔에서 완료 후 체크)

- [ ] 리전 **ap-northeast-2 (서울)** 선택
- [ ] **RDS**: academy-db, db.t4g.micro, 20GB, **퍼블릭 액세스 아니오**, VPC·보안그룹 연결
- [ ] **SQS**: `python scripts/create_sqs_resources.py ap-northeast-2` → `python scripts/create_ai_sqs_resources.py ap-northeast-2` (로컬 PowerShell, AWS 자격 증명 설정 후)
- [ ] **IAM 역할**: EC2용 SQS·ECR·Self-stop (이름 예: academy-ec2-role)
- [ ] **보안 그룹**: academy-api-sg(8000,22), academy-worker-sg(22), rds-academy-sg(5432 from API·Worker)
- [ ] **EC2 4대** (또는 API+Messaging 동일 1대): API(t4g.small 30GB), Messaging(t4g.micro), Video(t4g.medium 4GB+100GB EBS→/mnt/transcode), AI(t4g.micro 또는 t4g.small). 각 IAM·보안그룹 연결

---

## Step 0. 로컬 터미널 (PowerShell)

- [x] 프로젝트 루트로 이동 (대화에서 `(venv) PS C:\academy>` 확인)

```powershell
cd C:\academy
```

---

## Step 1. Docker 이미지 빌드 (ARM64, 순서대로)

- [x] 베이스

```powershell
docker context use default
docker build --platform linux/arm64 -f docker/Dockerfile.base -t academy-base:latest .
```

- [x] API

```powershell
docker build --platform linux/arm64 -f docker/api/Dockerfile -t academy-api:latest .
```

- [x] Messaging Worker

```powershell
docker build --platform linux/arm64 -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .
```

- [x] Video Worker

```powershell
docker build --platform linux/arm64 -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
```

- [x] AI Worker CPU

```powershell
docker build --platform linux/arm64 -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest .
```

---

## Step 2. ECR 로그인

- [x] 실행 (계정 809466760795 기준) — API 이미지 푸시한 이력으로 로그인 완료 추정

```powershell
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
```

---

## Step 3. ECR 태그 + 푸시

- [x] 태그

```powershell
docker tag academy-api:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
docker tag academy-messaging-worker:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker tag academy-video-worker:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker tag academy-ai-worker-cpu:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
```

- [x] 푸시 — **API만 완료** (대화에서 "api 이미지 올렸음" 확인). 워커 3종은 미확인 시 아래만 실행.

```powershell
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
```

---

## Step 4. 배포용 .env 생성 (로컬)

- [x] `.env.admin97`에 RDS용 `DB_HOST_RDS`, `DB_NAME_RDS`, `DB_USER_RDS`, `DB_PASSWORD_RDS`, `DB_PORT_RDS` 있는지 확인 후 실행 — **프로젝트에 `.env.deploy`, `.env.admin97` 존재**

```powershell
python scripts/prepare_deploy_env.py -o .env.deploy
```

- [ ] 생성된 `.env.deploy`를 각 EC2에 복사. (파일명을 `.env`로 바꿔서 사용해도 됨.) — API EC2에는 복사된 상태(API 동작으로 추정). 워커 EC2 복사 여부는 미확인.

---

## Step 5. EC2 API 서버 (SSH 접속 후)

- [x] Docker 설치 (최초 1회) — API 컨테이너 실행된 것으로 확인

```bash
sudo yum install -y docker && sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker ec2-user
```

→ 로그아웃 후 재접속.

- [x] ECR 로그인 + API 이미지 pull — 대화에서 pull·run 완료

```bash
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
```

- [x] 기존 API 컨테이너가 있으면 제거 후 실행 (재배포 시) — 대화에서 stop/rm 후 run

```bash
docker stop academy-api; docker rm academy-api
```

- [x] .env 있는 디렉터리에서 API 컨테이너 실행

```bash
docker run -d --name academy-api --restart unless-stopped --env-file .env -p 8000:8000 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
```

- [x] 마이그레이션 — 대화에서 "No migrations to apply" 확인

```bash
docker exec academy-api python manage.py migrate --no-input
```

- [x] 헬스 확인 — 대화에서 `{"status":"healthy","service":"academy-api","database":"connected"}` 확인

```bash
sleep 10; curl http://localhost:8000/health
```

→ `{"status":"healthy",...}` 나오면 통과.

- [x] 재시작 정책 — 대화에서 `docker update --restart unless-stopped academy-api` 실행 확인

```bash
docker update --restart unless-stopped academy-api
```

---

## Step 6. API_BASE_URL (워커용 .env)

- [ ] API EC2 **퍼블릭 IP** 확정 후, `.env.admin97`에서 수정:

```
API_BASE_URL=http://<API-퍼블릭IP>:8000
```

- [ ] 배포용 .env 다시 생성 후 워커 EC2에 복사

```powershell
python scripts/prepare_deploy_env.py -o .env.deploy
```

→ 새 `.env.deploy`를 Messaging/Video/AI EC2에 각각 복사.

---

## Step 7. EC2 Messaging Worker (SSH 접속 후)

- [ ] .env 있는 디렉터리에서 (API_BASE_URL 포함된 .env)

```bash
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker run -d --name academy-messaging-worker --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker update --restart unless-stopped academy-messaging-worker
```

---

## Step 8. EC2 Video Worker (SSH 접속 후)

- [ ] 100GB 마운트 확인

```bash
df -h
```

→ `/mnt/transcode` 약 100G 확인 후 진행.

- [ ] 실행

```bash
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker run -d --name academy-video-worker --restart unless-stopped --memory 4g --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 -v /mnt/transcode:/tmp 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker update --restart unless-stopped academy-video-worker
```

---

## Step 9. EC2 AI Worker CPU (SSH 접속 후)

- [ ] 실행

```bash
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
docker run -d --name academy-ai-worker-cpu --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
docker update --restart unless-stopped academy-ai-worker-cpu
```

---

## Step 10. 배포 전 5가지 (체크)

- [ ] **1** RDS 퍼블릭 액세스 **아니오**
- [ ] **2** Video Worker EC2에서 `df -h` → `/mnt/transcode` 약 100G
- [ ] **3** CloudWatch Log groups 보관 **7~14일**
- [ ] **4** EC2 Idle Stop: Video 1건 처리 → 큐 비움 → 5회 empty poll 후 인스턴스 Stop 되는지 1회 확인
- [ ] **5** 8000 포트 0.0.0.0/0는 테스트용만. 오픈 전 ALB+HTTPS 적용

---

## Step 11. 오픈 전 실전 체크 4개

- [ ] **1** ALB + Target Group `/health` + ACM 443 + 80→443 리다이렉트 적용
- [ ] **2** RDS에서 `SHOW max_connections;` 확인, 연결 수 모니터링
- [ ] **3** Self-Stop 실제 동작 1회 확인
- [ ] **4** Video EC2 `free -h`로 Swap 사용률 확인 (과다 시 RAM 증설 검토)

---

**문서 출처**: `docs/cursor_docs/500_배포_진행도우미.md`, `docs/SSOT_0215/AWS_500_START_DEPLOY_GUIDE.md`, `docs/SSOT_0215/DEPLOY_PROGRESS_CHECK.md`.
