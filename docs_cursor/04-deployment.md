# 배포 (실제 경로 · 순서)

**상세**: `docs/배포.md`. 리전: ap-northeast-2(서울).

---

## 1. 배포 순서 요약

1. 리전 서울  
2. RDS academy-db (db.t4g.micro, 20GB, 비퍼블릭) → .env `DB_HOST`  
3. SQS (`create_sqs_resources.py`, `create_ai_sqs_resources.py`)  
4. IAM EC2용 (SQS, ECR, Self-stop)  
5. 보안 그룹: API(8000, 22), Worker(22), RDS(5432 from API·Worker)  
6. EC2 API t4g.small 30GB → Docker, ECR pull, .env, migrate, `/health`  
7. EC2 Messaging t4g.micro 상시  
8. EC2 Video t4g.medium 4GB+100GB → `/mnt/transcode` 확인 후 컨테이너  
9. Video·AI 워커 ASG(Min=0) 또는 수동 EC2  

---

## 2. Docker 이미지 (실제 경로)

- **베이스**: `docker/Dockerfile.base` → `academy-base:latest`
- **서비스**:
  - `docker/api/Dockerfile` → academy-api
  - `docker/messaging-worker/Dockerfile` → academy-messaging-worker
  - `docker/video-worker/Dockerfile` → academy-video-worker
  - `docker/ai-worker-cpu/Dockerfile` → academy-ai-worker-cpu
  - (있으면) `docker/ai-worker-gpu/Dockerfile` → academy-ai-worker-gpu

빌드 예 (PowerShell, linux/arm64):

```powershell
cd C:\academy
docker buildx build --platform linux/arm64 -f docker/Dockerfile.base -t academy-base:latest --load .
docker buildx build --platform linux/arm64 -f docker/api/Dockerfile -t academy-api:latest --load .
# 나머지 동일 -f docker/<서비스>/Dockerfile
```

---

## 3. ECR 푸시 (예시)

- 리전: ap-northeast-2  
- 계정 ECR 주소 예: `809466760795.dkr.ecr.ap-northeast-2.amazonaws.com`  
- 이미지: academy-api, academy-messaging-worker, academy-video-worker, academy-ai-worker-cpu 등.

---

## 4. 환경 파일

- `scripts/prepare_deploy_env.py -o .env.deploy` → 출력된 .env.deploy를 EC2에 배치 후 `.env` 로 사용.

---

## 5. EC2 API 실행 (SSH 후)

- Docker 설치 → ECR 로그인 → pull → `docker run -d --name academy-api --restart unless-stopped --env-file .env -p 8000:8000 ...`
- `docker exec academy-api python manage.py migrate --no-input`
- `curl http://localhost:8000/health`

---

## 6. 워커 설정

- Messaging: `DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker`
- Video: `-v /mnt/transcode:/tmp`, 메모리 4g, `EC2_IDLE_STOP_THRESHOLD` 등 (docs/배포.md 참고).
- AI: `docker/ai-worker-cpu/Dockerfile`, worker 설정 모듈 동일.

---

## 7. 배포 전·오픈 전 체크

- docs/배포.md §7(배포 전 필수 6개), §8(오픈 전 4개) 참고.
- 8000 포트는 테스트용; 오픈 전 ALB + HTTPS.

---

## 8. EC2 API 자동 배포 (cron ON/OFF)

**위치**: EC2 API 서버(`/home/ec2-user/academy`)에서 실행. 1분마다 `origin/main` 변경 감지 시 `scripts/deploy_api_on_server.sh` 실행.

| 동작 | 명령어 |
|------|--------|
| **ON** | `cd /home/ec2-user/academy && bash scripts/auto_deploy_cron_on.sh` |
| **OFF** | `cd /home/ec2-user/academy && bash scripts/auto_deploy_cron_off.sh` |
| 상태 확인 | `crontab -l` |
| 로그 보기 | `tail -f /home/ec2-user/auto_deploy.log` |

- lock 파일(`/tmp/academy_deploy.lock`)으로 중복 실행 방지.
- 경로 변경 시: `REPO_DIR=/path/to/repo LOG_FILE=/path/to/log bash scripts/auto_deploy_cron_on.sh`
