# 배포 진행 체크 (문서 vs 실제 코드 기준)

**기준 문서**: `AWS_500_START_DEPLOY_GUIDE.md`, `HEXAGONAL_10K_EXECUTION_PLAN_v1.5.md`  
**작성**: 프로젝트 스캔 결과만 반영. 추측 없음.

---

## 1. 프로젝트 vs 문서 — 사실만 정리

### 1.1 Docker·빌드

| 항목 | 문서 | 실제 코드 | 일치 |
|------|------|-----------|------|
| API Dockerfile | GUNICORN_WORKERS:-4, gevent, timeout 120 | `docker/api/Dockerfile` 36–40행 동일 | ✅ |
| Base 이미지 | academy-base → api/workers 순서 | `docker/build.ps1` 56–75행 순서 동일 | ✅ |
| ARM64 빌드 | EC2 t4g = ARM64 → 로컬 x86이면 `--platform linux/arm64` 필수 (§6.2) | `build.ps1`에는 `--platform` 없음. **로컬이 x86이면** 가이드 §6.2대로 수동으로 `docker buildx build --platform linux/arm64 ...` 필요 | ⚠️ |
| 워커 이미지 | messaging, video, ai-worker-cpu ECR 푸시 | `docker/messaging-worker/`, `docker/video-worker/`, `docker/ai-worker-cpu/` Dockerfile 존재. build.ps1에 3종 포함 | ✅ |

### 1.2 AI Worker·Legacy (10K SSOT)

| 항목 | 문서 | 실제 코드 | 일치 |
|------|------|-----------|------|
| USE_LEGACY_AI_WORKER | 제거 시 grep 0건 | `grep -r "USE_LEGACY_AI_WORKER" --include="*.py" .` → **0건** (문서에만 있음) | ✅ |
| AI Worker 엔트리 | academy 전용, `run_ai_sqs_worker()` | `apps/worker/ai_worker/sqs_main_cpu.py` 17–18행: `run_ai_sqs_worker()` 만 호출, legacy 분기 없음 | ✅ |
| AI 큐 Visibility | 3600 | `scripts/create_ai_sqs_resources.py` 29·35·41행 `visibility_timeout`: "3600" | ✅ |

### 1.3 배포용 .env

| 항목 | 문서 | 실제 코드 | 일치 |
|------|------|-----------|------|
| 배포 .env 생성 | `python scripts/prepare_deploy_env.py -o .env.deploy` | `scripts/prepare_deploy_env.py` 존재. **입력**: `ROOT/.env.admin97` (RDS용 `DB_*_RDS` → `DB_*` 매핑) | ✅ |
| .env.admin97 | RDS 연결값 있으면 해당 값으로 채움 | 프로젝트 루트에 `.env.admin97` 파일 존재 여부는 로컬만 확인 가능. 없으면 스크립트가 ERROR 출력 | 확인 필요 |

### 1.4 SQS 스크립트

| 항목 | 문서 | 실제 코드 | 일치 |
|------|------|-----------|------|
| Video/Messaging 큐 | `python scripts/create_sqs_resources.py ap-northeast-2` | 스크립트 존재. Video VisibilityTimeout 10800 | ✅ |
| AI 큐 | `python scripts/create_ai_sqs_resources.py ap-northeast-2` | 스크립트 존재. Lite/Basic/Premium 각 3600 | ✅ |

### 1.5 DB 설정 (10K §3.1)

| 항목 | 문서 | 실제 코드 | 일치 |
|------|------|-----------|------|
| CONN_MAX_AGE | 60 | `apps/api/config/settings/base.py` 186행, `worker.py` 85행: `DB_CONN_MAX_AGE` env, 기본 60 | ✅ |

---

## 2. “API 이미지 올렸음” 기준 — 지금 할 일 (가이드 §6 → §6.5 → §7)

**전제**: “API 이미지 올렸음” = ECR에 `academy-api:latest` 푸시까지 완료.

### 2.1 §6.3 EC2 API 서버 (아직 안 했다면)

1. EC2(t4g.small) SSH 접속 후 Docker 설치·ECR 로그인.
2. **배포용 .env**  
   - 로컬: `python scripts/prepare_deploy_env.py -o .env.deploy`  
   - `.env.admin97`에 RDS용 `DB_HOST_RDS`, `DB_NAME_RDS`, `DB_USER_RDS`, `DB_PASSWORD_RDS`, `DB_PORT_RDS` 있으면 `.env.deploy`에 `DB_*`로 채워짐.  
   - 생성된 `.env.deploy`를 EC2에 복사 후 해당 디렉터리에서 `--env-file .env` 로 사용 (파일명을 `.env`로 바꿔도 됨).
3. EC2에서:  
   `docker pull <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest`  
   `docker run -d --name academy-api --restart unless-stopped --env-file .env -p 8000:8000 <ECR_URL>/academy-api:latest`
4. **migrate**:  
   `docker exec academy-api python manage.py migrate --no-input`
5. **헬스 확인**:  
   `curl http://localhost:8000/health` → `{"status":"healthy",...}` 기대.
6. 재시작 정책:  
   `docker update --restart unless-stopped academy-api`

### 2.2 §6.5 배포용 .env·API 주소 고정

- API 퍼블릭 IP(또는 도메인) 확정 후 `.env`에 `API_BASE_URL=http://<API-IP>:8000` 설정.
- 워커를 띄울 EC2에는 이 값이 반영된 `.env` 복사 후 사용.

### 2.3 §7 이후 순서 (문서 그대로)

| 순서 | 할 일 | 가이드 |
|------|-------|--------|
| 7 | Messaging Worker EC2: academy-messaging-worker pull·실행, `docker update --restart unless-stopped academy-messaging-worker` | §7 |
| 8 | Video Worker EC2: 100GB `/mnt/transcode` 확인(`df -h`) → academy-video-worker 실행 시 `-v /mnt/transcode:/tmp`, `--memory 4g` | §8 |
| 9 | AI Worker CPU: academy-ai-worker-cpu pull·실행 (별도 EC2 또는 Video 호스트) | §9 |
| 10~11 | 환경 변수 정리(§10), 검증(§11) | §10, §11 |

---

## 3. API 이미지를 “EC2에서 이미 실행 중”인 경우

- **migrate** 한 번 더 실행해도 됨 (이미 적용된 migration은 스킵).
- **/health** 200이면 §6.5 → §7로 진행: `API_BASE_URL` 고정 → 워커 3종 순서대로 배포.

---

## 4. ARM64 빌드 여부 (로컬이 x86일 때)

- 가이드 §6.2: EC2가 t4g(ARM64)이면 **로컬이 x86일 때** `docker buildx build --platform linux/arm64 ...` 로 빌드 후 푸시해야 함.
- **이미 EC2에서 API 컨테이너가 정상 동작 중이면** 현재 사용한 이미지는 ARM64가 맞음 (추가 조치 불필요).
- **아직 EC2에서 실행 전**이고 로컬이 x86이면, API만 올렸다 해도 나중에 워커 이미지 푸시 시 **워커 3종도 ARM64로 빌드**해야 함.

---

## 5. 500 가이드 “필수 완료 목록” 중 현재 상태로 확인 가능한 것

| # | 항목 | 확인 방법 |
|---|------|-----------|
| 2 | 베이스·API·Messaging·Video·AI 워커 이미지 **ARM64** 빌드 + ECR 푸시 | API 푸시 완료. 나머지 3종은 §7·§8·§9 전에 동일 계정/리전에 푸시 필요. |
| 3 | 배포용 .env, 각 EC2에 복사, API_BASE_URL | `prepare_deploy_env.py` 실행 → `.env.deploy` → EC2에 복사. API 주소 확정 후 API_BASE_URL 설정. |
| 4 | API EC2: migrate, /health 200, restart 정책 | §2.1 순서대로 수행 후 확인. |

---

## 6. 배포 정보 한눈에 (프로젝트 스캔)

아래는 **프로젝트 내 파일에서 추출한 값**만 정리. 추측 없음.

### 6.1 ECR (가이드·진행도우미 기준)

| 항목 | 값 |
|------|-----|
| 리전 | ap-northeast-2 |
| 레지스트리 | `809466760795.dkr.ecr.ap-northeast-2.amazonaws.com` |
| 로그인 | `aws ecr get-login-password --region ap-northeast-2 \| docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com` |
| 이미지 이름 | academy-api, academy-messaging-worker, academy-video-worker, academy-ai-worker-cpu |

### 6.2 SQS 큐 이름 (코드·스크립트 기준)

| 용도 | 큐 이름 |
|------|---------|
| Video | academy-video-jobs |
| Messaging | academy-messaging-jobs |
| AI Lite | academy-ai-jobs-lite |
| AI Basic | academy-ai-jobs-basic |
| AI Premium | academy-ai-jobs-premium |

출처: `.env.example`, `scripts/create_sqs_resources.py`, `scripts/create_ai_sqs_resources.py`.

### 6.3 배포용 .env

- **생성**: `python scripts/prepare_deploy_env.py -o .env.deploy`  
- **입력 파일**: `ROOT/.env.admin97`. RDS용 키 `DB_HOST_RDS`, `DB_NAME_RDS`, `DB_USER_RDS`, `DB_PASSWORD_RDS`, `DB_PORT_RDS` 있으면 출력에 `DB_HOST`, `DB_NAME` 등으로 매핑됨 (`scripts/prepare_deploy_env.py` 19–25행).
- **워커용**: API 주소 확정 후 `API_BASE_URL=http://<API-퍼블릭IP>:8000` (또는 HTTPS) 설정. `.env.example` 73행 예시: `API_BASE_URL=https://api.hakwonplus.com`.

### 6.4 .env 필수 항목 (문서·코드 기준)

- **공통**: SECRET_KEY, DEBUG, DJANGO_SETTINGS_MODULE(워커는 worker), DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT, DB_CONN_MAX_AGE  
- **R2**: R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_PUBLIC_BASE_URL, R2_AI_BUCKET, R2_VIDEO_BUCKET, R2_EXCEL_BUCKET  
- **API/워커 연동**: INTERNAL_WORKER_TOKEN, API_BASE_URL  
- **SQS**: AWS_REGION=ap-northeast-2, VIDEO_SQS_QUEUE_NAME, MESSAGING_SQS_QUEUE_NAME, AI_SQS_QUEUE_NAME_LITE/BASIC/PREMIUM  
- **워커 전용**: VIDEO_WORKER_ID, AI_WORKER_ID_CPU, EC2_IDLE_STOP_THRESHOLD(Video/AI), SOLAPI_*(Messaging)

출처: `docs/SSOT_0215/AWS_500_START_DEPLOY_GUIDE.md` §10, `docs/OPERATIONS.md`, `.env.example`.

### 6.5 EC2·보안 그룹·IAM (가이드 요약)

| 구분 | 내용 |
|------|------|
| API | t4g.small, 30GB, 보안그룹 academy-api-sg (8000, 22) |
| Messaging | t4g.micro, academy-worker-sg (22) |
| Video | t4g.medium, 4GB, 100GB EBS → /mnt/transcode, academy-worker-sg |
| AI Worker | t4g.micro 또는 t4g.small, academy-worker-sg |
| RDS | rds-academy-sg, 5432 from academy-api-sg, academy-worker-sg |
| IAM | EC2 역할: SQS(academy-*), ECR pull, EC2 Self-stop(Video/AI) — 가이드 §4 |

### 6.6 워커 docker run (복붙용, ECR 계정 809466760795)

**Messaging** (EC2 SSH 후 .env 있는 디렉터리에서):

```bash
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker run -d --name academy-messaging-worker --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
docker update --restart unless-stopped academy-messaging-worker
```

**Video** (`df -h`로 /mnt/transcode 약 100G 확인 후):

```bash
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker run -d --name academy-video-worker --restart unless-stopped --memory 4g --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 -v /mnt/transcode:/tmp 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
docker update --restart unless-stopped academy-video-worker
```

**AI Worker CPU**:

```bash
docker pull 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
docker run -d --name academy-ai-worker-cpu --restart unless-stopped --env-file .env -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker -e EC2_IDLE_STOP_THRESHOLD=5 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
docker update --restart unless-stopped academy-ai-worker-cpu
```

출처: `docs/cursor_docs/500_배포_진행도우미.md` Step 7·8·9.

---

이 파일은 프로젝트 스캔 결과와 가이드 문구만 반영했습니다.  
EC2·RDS·SQS 등 인프라 상태는 직접 확인이 필요합니다.
