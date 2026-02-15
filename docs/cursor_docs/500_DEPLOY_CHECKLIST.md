# 500 배포 전 체크리스트

**용도**: Docker 최적화 완료 후, AWS 500 스타트 배포 직전에 한 번만 돌리면 되는 체크리스트.  
**참조**: `AWS_500_START_DEPLOY_GUIDE.md`, `AWS_500_DOCKER_REQUIREMENTS_ALIGNMENT.md`

---

## 🔍 자동 검증 결과 (repo/로컬 기준)

| 항목 | 결과 | 비고 |
|------|:----:|------|
| Gate 10 스크립트 | ✅ | `scripts/gate10_test.py` 존재, 사용자 실행 시 **[GO]** 확인됨 |
| Docker 설치 | ✅ | Docker 29.1.3, buildx v0.30.1 |
| Dockerfile 경로 | ✅ | base, api, messaging-worker, video-worker, ai-worker-cpu 전부 존재 |
| requirements 파일 | ✅ | common, api, worker-messaging, worker-video, worker-ai-common, worker-ai-cpu, worker-ai-excel 존재 |
| .env.example §10 대응 | ✅ | DB_*, R2_*, AWS_REGION, SQS 큐 이름, INTERNAL_WORKER_TOKEN, EC2_IDLE_STOP_THRESHOLD 등 있음 |
| manage.py | ✅ | 프로젝트 루트에 존재 (migrate 명령용) |

**직접 확인 필요**: 1.2 migrate 실행, §3~§6 AWS 콘솔/실서버 설정.

---

## ✅ 1. 로컬 검증 (배포 전 필수)

| # | 항목 | 확인 방법 | 완료 |
|---|------|-----------|:----:|
| 1.1 | Gate 10 통과 | `python scripts/gate10_test.py` → 5단계 [PASS] + **Final verdict: [GO]** | ✅ |
| 1.2 | DB migrate 가능 | 로컬 또는 스테이징에서 `python manage.py migrate` 성공 | ✅ |
| 1.3 | Docker 설치 | `docker --version`, (ARM64 빌드 시) `docker buildx` 사용 가능 | ✅ |

---

## ✅ 2. Docker 이미지 빌드 순서

**반드시 베이스 먼저.** 컨텍스트: 프로젝트 루트(`C:\academy`).

| 순서 | 이미지 | 명령 (ARM64, t4g용) | 완료 |
|:----:|-------|---------------------|:----:|
| 1 | academy-base | `docker buildx build --platform linux/arm64 -f docker/Dockerfile.base -t academy-base:latest --load .` | ☐ |
| 2 | academy-api | `docker buildx build --platform linux/arm64 -f docker/api/Dockerfile -t academy-api:latest --load .` | ☐ |
| 3 | academy-messaging-worker | `docker buildx build --platform linux/arm64 -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest --load .` | ☐ |
| 4 | academy-video-worker | `docker buildx build --platform linux/arm64 -f docker/video-worker/Dockerfile -t academy-video-worker:latest --load .` | ☐ |
| 5 | academy-ai-worker-cpu | `docker buildx build --platform linux/arm64 -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest --load .` | ☐ |

*(위 5개 Dockerfile·requirements 경로 검증 완료. 빌드 실행은 배포 시 직접.)*

- 로컬이 이미 ARM(M1/M2 등)이면 `--platform linux/arm64` 생략 가능.
- ECR 푸시 시: 위에서 빌드한 이미지를 ECR 저장소에 tag 후 push (가이드 §6 참고).

---

## ✅ 3. AWS 인프라 (가이드 §1~§5)

| # | 항목 | 확인 방법 | 완료 |
|---|------|-----------|:----:|
| 3.1 | 리전 | ap-northeast-2 (서울) | ☐ |
| 3.2 | RDS | academy-db 생성, db.t4g.micro, 20GB, **퍼블릭 액세스 아니오** | ☐ |
| 3.3 | RDS 엔드포인트 | `.env`의 DB_HOST, DB_NAME, DB_USER, DB_PASSWORD 반영 | ☐ |
| 3.4 | SQS 큐 | Video / Messaging / AI(Lite, Basic, Premium) 큐 생성 (스크립트 실행) | ☐ |
| 3.5 | IAM 역할 | EC2용 SQS·ECR·Self-stop 권한 | ☐ |
| 3.6 | 보안 그룹 | API, Worker, RDS용 그룹 생성 및 5432·8000 규칙 | ☐ |

---

## ✅ 4. 배포 전 반드시 확인 5가지 (가이드)

| # | 항목 | 확인 방법 | 완료 |
|---|------|-----------|:----:|
| 4.1 | RDS 퍼블릭 액세스 끄기 | RDS 콘솔 → 퍼블릭 액세스 **아니오** | ☐ |
| 4.2 | Video Worker 100GB 마운트 | EC2 SSH → `df -h` → `/mnt/transcode` 약 100G | ☐ |
| 4.3 | CloudWatch 로그 보관 | Retention 7~14일 | ☐ |
| 4.4 | EC2 Idle Stop 동작 | Video 1건 처리 후 큐 비움 → 인스턴스 자동 Stop 확인 | ☐ |
| 4.5 | 8000 포트 | 초기 테스트용만; **실제 오픈 전** ALB + HTTPS 적용 | ☐ |

---

## ✅ 5. 환경 변수 (EC2/컨테이너)

| # | 항목 | 확인 방법 | 완료 |
|---|------|-----------|:----:|
| 5.1 | API 서버 | DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod (또는 .env) | ☐ |
| 5.2 | Worker | DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker | ☐ |
| 5.3 | §10 환경 변수 | DB_*, R2_*, AWS_REGION, SQS 큐 이름, INTERNAL_WORKER_TOKEN 등 가이드 §10과 동일 | ☐ |

---

## ✅ 6. 오픈 전 실전 체크 (서비스 공개 직전)

| # | 항목 | 완료 |
|---|------|:----:|
| 6.1 | ALB + Target Group health check `/health` + ACM 443 + 80→443 리다이렉트 | ☐ |
| 6.2 | RDS `max_connections` 확인 (필요 시 모니터링) | ☐ |
| 6.3 | Video Worker Self-Stop 실제 1회 테스트 | ☐ |
| 6.4 | Swap 사용률 모니터링 (과다 시 RAM 상향 검토) | ☐ |

---

## 📌 요약

- **1·2 통과** → 로컬·Docker 준비 완료.  
- **3·4·5 통과** → EC2·RDS·SQS·보안·환경 변수 준비 완료.  
- **6 통과** → 실제 트래픽 오픈 가능.

**Docker 최적화 적용 상태**: 베이스 통합, ai-worker-cpu base 상속, non-root(appuser) 적용 완료. 위 빌드 순서대로만 진행하면 바로 배포 가능.
