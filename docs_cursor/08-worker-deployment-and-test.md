# 워커 배포 환경 · 테스트 (실제 코드 기준)

**상세 배포**: `docs/배포.md`, `infra/worker_asg/README.md`, `infra/worker_autoscale_lambda/README.md`.  
리전: **ap-northeast-2 (서울)**.

---

## 1. 배포 환경 구조

| 역할 | 인스턴스/이미지 | 비고 |
|------|-----------------|------|
| **API** | EC2 t4g.small 30GB, Docker `academy-api` | 8000 포트, 오픈 전 ALB+HTTPS |
| **RDS** | db.t4g.micro 20GB, 비퍼블릭 | .env `DB_HOST` |
| **Messaging** | EC2 t4g.micro 상시 또는 Docker `academy-messaging-worker` | SQS 수신 → 메시지 발송 |
| **Video** | EC2 t4g.medium 4GB+**100GB EBS** (`/mnt/transcode`) 또는 ASG Min=0 | Docker `academy-video-worker`, `-v /mnt/transcode:/tmp` |
| **AI** | EC2 또는 **ASG Min=0** | Docker `academy-ai-worker-cpu` (GPU는 `academy-ai-worker-gpu`) |

- **워커 공통**: `DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker` (API/Admin 미포함, ORM만).
- **환경 변수**: EC2/ASG는 SSM `/academy/workers/env` (SecureString)에서 `.env` 로드.  
  업로드: `aws ssm put-parameter --name /academy/workers/env --type SecureString --value file://.env --overwrite --region ap-northeast-2`  
  또는 `scripts/upload_env_to_ssm.ps1`, `scripts/setup_worker_iam_and_ssm.ps1`.

---

## 2. 워커 종류 · 큐 · 진입점

| 워커 | SQS 큐 (생성 스크립트) | 앱 진입점 | Dockerfile |
|------|------------------------|-----------|------------|
| **Messaging** | `create_sqs_resources.py` | `apps.worker.messaging_worker.sqs_main` | `docker/messaging-worker/Dockerfile` |
| **Video** | 동일 | `apps.worker.video_worker.sqs_main` | `docker/video-worker/Dockerfile` |
| **AI CPU** | `create_ai_sqs_resources.py` | `apps.worker.ai_worker.sqs_main_cpu` | `docker/ai-worker-cpu/Dockerfile` |
| **AI GPU** | 동일 | `apps.worker.ai_worker.sqs_main_gpu` | `docker/ai-worker-gpu/Dockerfile` |

- **ASG 부팅 스크립트**: `infra/worker_asg/user_data/ai_worker_user_data.sh`, `video_worker_user_data.sh`, `messaging_worker_user_data.sh` — SSM에서 env 로드 → ECR pull → `docker run` (worker 설정 모듈, `EC2_IDLE_STOP_THRESHOLD=0`).

---

## 3. 워커 테스트 (로컬)

- **실제 SQS 연동** (DB/Redis/환경 필요):
  - `run-worker-messaging.ps1`, `run-worker-video.ps1`, `run-worker-ai.ps1`, `run-worker-ai-gpu.ps1`  
  - 루트: `python -m apps.worker.messaging_worker.sqs_main` 등 (각 스크립트 참고).
- **비즈니스 로직만 검증 (Mock, SQS/DB 불필요)**:
  - `python scripts/test_worker_action.py` — Video Handler + AI embedding 경량 검증.  
  - `python scripts/test_worker_action.py --video-only` / `--ai-only` / `--with-django` (선택).
- **배포 전 검사**:
  - `python scripts/check_workers.py` — 금지 패턴·워커 모듈 import.  
  - `python scripts/check_workers.py --docker` — Docker 이미지 기준 검증.  
  - `python scripts/check_worker_pipelines.py` — AI 파이프라인 등.

---

## 4. 배포 관련 스크립트 · 경로

| 목적 | 경로/명령 |
|------|------------|
| Docker 빌드 (linux/arm64) | `docker/build.ps1`, `docs/배포.md` §2 복붙 |
| ECR 푸시 | `scripts/build_and_push_ecr.ps1` 또는 `docs/배포.md` §2 |
| 배포용 .env 생성 | `python scripts/prepare_deploy_env.py -o .env.deploy` |
| 워커 ASG 배포 (Queue Depth → ASG) | `scripts/deploy_worker_asg.ps1` (SubnetIds, SecurityGroupId, IamInstanceProfileName) |
| 워커 Autoscale Lambda (500 스케일) | `scripts/deploy_worker_autoscale.ps1`, zip: `infra/worker_autoscale_lambda/lambda_function.py` |
| API 서버 자동 배포 cron | EC2에서 `scripts/auto_deploy_cron_on.sh` / `auto_deploy_cron_off.sh` |

- **Queue Depth Lambda**: `infra/worker_asg/queue_depth_lambda/lambda_function.py` — 1분마다 SQS visible 수 → CloudWatch `Academy/Workers` 네임스페이스 (ASG Target Tracking용).

---

## 5. 배포 전·오픈 전 체크 (요약)

- **배포 전**: 워커 ASG 구동·테스트, RDS 비퍼블릭, Video EC2 `df -h` → /mnt/transcode 100G, CloudWatch 로그, ASG scale-in 1회, 8000 테스트용만.
- **오픈 전**: ALB + Target Group `/health` + ACM 443, RDS max_connections 모니터링, ASG scale-in 확인, Video EC2 `free -h` Swap 확인.

워커 테스트 시: 로컬은 `test_worker_action.py` + `run-worker-*.ps1`, 배포 후는 EC2/ASG 인스턴스에서 `docker logs academy-*-worker` 및 SQS 큐 적재·소비 확인.
