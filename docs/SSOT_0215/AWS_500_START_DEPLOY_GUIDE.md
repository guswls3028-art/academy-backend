# 500명 스타트 AWS 배포 가이드 (당장 따라하기)

**대상**: 500명 스타트 권장안(커서안 채택)을 AWS에서 실제 인스턴스·설정으로 바로 적용할 때  
**리전**: ap-northeast-2 (서울)  
**원칙**: 안정 > 최저비용, Video Worker 프로덕션은 **반드시 4GB** (로컬 compose 2GB 아님)

---

## 📌 진행 순서 (바로 따라하기)

| 순서 | 할 일 | 가이드 위치 | 완료 후 다음 |
|------|-------|-------------|--------------|
| 1 | 리전 확인 (서울) | §1 | 2로 |
| 2 | RDS 생성 (academy-db, db.t4g.micro, Single-AZ, 20GB, 퍼블릭 아니오) | §2 | 3으로 |
| 3 | SQS 큐 생성 (로컬에서 스크립트 실행) | §3 | 4로 |
| 4 | IAM 역할 생성 (EC2용, SQS·ECR·Self-stop) | §4 | 5로 |
| 5 | 보안 그룹 생성 (API, Worker, RDS) | §5 | 6으로 |
| 6 | EC2 API 서버 (t4g.small, 30GB) + Docker + ECR 푸시 | §6 | 6.5 → 7 |
| 6.5 | 배포용 .env 생성·EC2 복사, migrate, `/health` 확인 | §6.3 아래 | 7으로 |
| 7 | EC2 Messaging Worker (t4g.micro 상시, 또는 API EC2에 동시 실행) | §7 | 8으로 |
| 8 | EC2 Video Worker (t4g.medium, 4GB, 100GB EBS → `/mnt/transcode`) | §8 | 9으로 |
| 9 | AI Worker CPU (별도 EC2 또는 Video 호스트 공유) | §9 | §10 → §11 검증 |

---

## ✅ 필수 완료 목록 (전부 해야 끝)

아래는 **선택이 아닌 필수** 항목이다. 다 끝내야 500명 스타트 배포가 끝난다.

| # | 구분 | 필수 항목 | 비고 |
|---|------|-----------|------|
| 1 | 인프라 | 리전 ap-northeast-2, RDS(academy-db, 퍼블릭 아니오), SQS(Video/Messaging/AI 큐), IAM 역할, 보안 그룹(API/Worker/RDS) | §1~§5 |
| 2 | 로컬 | 베이스·API·Messaging·Video·AI 워커 이미지 빌드(ARM64) + ECR 푸시 | §6.2 + 워커 3종 |
| 3 | 환경 | 배포용 .env 생성(DB_HOST 등 RDS 반영), 각 EC2에 .env 복사, API_BASE_URL=API 주소 | §10, scripts/prepare_deploy_env.py |
| 4 | API EC2 | Docker 설치, ECR 로그인, academy-api pull·실행, **migrate**, `/health` 200 확인, `docker update --restart unless-stopped academy-api` | §6.3 |
| 5 | Messaging EC2 | academy-messaging-worker pull·실행, `docker update --restart unless-stopped academy-messaging-worker` | §7 (API EC2에 같이 띄워도 됨) |
| 6 | Video EC2 | 100GB EBS `/mnt/transcode` 마운트 확인(`df -h`) → academy-video-worker pull·실행(`-v /mnt/transcode:/tmp`, `--memory 4g`), `docker update --restart unless-stopped academy-video-worker` | §8 |
| 7 | AI Worker | academy-ai-worker-cpu pull·실행(별도 EC2 또는 Video EC2), `docker update --restart unless-stopped academy-ai-worker-cpu` | §9 |
| 8 | 배포 전 5가지 | RDS 퍼블릭 끄기, Video 100GB 확인, CloudWatch 보관 7~14일, Idle Stop 1회 테스트, 8000은 테스트용·오픈 전 ALB+HTTPS | 상단 🔥 |
| 9 | 오픈 전 4개 | ALB+HTTPS 적용, RDS max_connections 확인, Self-Stop 실제 동작 1회, Swap 모니터링 | 상단 🔎 |

---

## 0. 준비물

- AWS 계정, 콘솔 또는 CLI 접근
- 로컬에 academy 레포 클론, Docker 설치
- (선택) ECR에 이미지 푸시 후 EC2에서 pull 하려면: AWS CLI 설정, ECR 로그인

**권장 스펙 요약**

| 역할 | 인스턴스/스펙 | 디스크 | 비고 |
|------|----------------|--------|------|
| API | EC2 t4g.small 또는 Fargate 0.5 vCPU / 2GB | 30GB | 상시 1대 |
| RDS | db.t4g.micro (Single-AZ) | 20~30GB gp3, 오토스케일 ON | |
| Messaging | EC2 t4g.micro 또는 Fargate 0.25 vCPU / 1GB | 기본 | 상시 1대 |
| Video Worker | EC2 t4g.medium (2vCPU / **4GB**) | **EBS gp3 100GB** | Desired 0, 큐 깊이≥1 시 1, Max 2 |
| AI Worker CPU | 0.5 vCPU / 1GB (별도 또는 Video 호스트 공유) | 기본 | Desired 0, Max 1~2 |

---

## 🔥 배포 전 반드시 확인할 5가지 (진짜 실전 체크리스트)

| # | 항목 | 확인 방법 |
|---|------|-----------|
| **1** | **RDS 퍼블릭 액세스 끄기** | RDS → 해당 인스턴스 → 수정 → 퍼블릭 액세스 **아니오**. API·Worker는 같은 VPC 내부 통신만 사용. 500명이라도 DB는 외부에 열어둘 필요 없다. |
| **2** | **Video Worker 100GB 마운트** | EC2 SSH 접속 후 `df -h` 실행. **`/mnt/transcode`가 약 100G로 보여야 정상.** 이거 안 하면 루트 8GB에서 트랜스코딩 → 디스크 Full → 인코딩 실패가 제일 흔한 사고다. |
| **3** | **CloudWatch 로그 보관 기간** | CloudWatch → Log groups (Video Worker 등) → **Retention 7~14일**로 변경. 기본 영구 보관이면 ffmpeg·3시간 인코딩·디버그 로그로 한 달 지나면 비용 올라간다. |
| **4** | **EC2 Idle Stop 실제 동작** | Video Worker: 메시지 1건 처리 후 큐가 비어 있는 상태에서, **인스턴스가 진짜 자동 종료되는지** 확인. 안 확인하면 Self-stop 미동작으로 월 24시간 과금될 수 있다. |
| **5** | **8000 포트 직접 오픈은 임시만** | API 보안 그룹에서 `8000 from 0.0.0.0/0`는 **초기 테스트용**으로만. **실제 오픈 전에는 반드시** 아래 “오픈 전 필수” 적용. |

**오픈 전 필수 (로그인 시스템이면 필수)**  
실제 서비스 오픈 전에 반드시 적용할 것. **로그인 시스템이면 HTTPS 미적용은 바로 리스크다.**

- **ALB** 생성
- **Target Group** health check 경로 **`/health`**
- **ACM 인증서** 연결 (443 리스너)
- **HTTPS 443**으로 서비스 오픈
- **80 → 443 리다이렉트** (HTTP 접속 시 HTTPS로 넘기기)

위 네 가지가 **실제로 설정돼 있어야** 오픈 가능. 8000 직접 노출은 테스트용으로만 두고, 오픈 시점에는 여기까지 하고 나서 열 것.

---

## 🔎 오픈 전 실전 체크 4개 (마지막 점검)

| # | 항목 | 확인 방법 |
|---|------|-----------|
| **1** | **ALB + HTTPS 실제 적용** | ALB 생성됨, Target Group health check `/health`, ACM 연결, 80→443 리다이렉트 **전부 설정됐는지** 확인. 로그인 시스템이면 HTTPS 미적용은 바로 리스크. |
| **2** | **RDS 연결 수** | db.t4g.micro(1GB) + Django Gunicorn 4 worker + 워커들 합치면 동시 커넥션 **20~40개**까지 갈 수 있음. **`SHOW max_connections;`** 로 Postgres 한도 확인. 기본값이면 당장 문제 없음. 나중에 AI/Video 워커 DB 접근 늘면 PgBouncer 검토. **커넥션 폭증만 모니터링** 해두면 됨. |
| **3** | **Self-Stop 진짜 동작** | **반드시 실제로 한 번 해보기**: Video 1건 처리 → 큐 비움 → 5회 empty poll → **EC2가 진짜 Stop 되는지**. 이거 한 번만 확인하면 이후 비용 걱정 거의 사라진다. 안 보면 돈 샌다. |
| **4** | **Swap은 보험일 뿐** | 2GB swap 넣는 건 좋다. 다만 **swap 과다 사용**하면 ffmpeg가 느려질 수 있음. 운영 중 **`free -h`** 로 Swap 사용률 확인. 지속적으로 높으면 → RAM 8GB로 올리는 게 낫다. 500명에서는 **4GB + swap**이면 충분. |

---

## 1. 리전·VPC

- **리전**: **ap-northeast-2 (서울)** 고정 (RDS·EC2·SQS 동일 리전).
- **VPC**: 기본 VPC 사용해도 됨.  
  서브넷: 퍼블릭 2개 이상 있으면 RDS는 private 권장이지만, 500명 스타트에서는 퍼블릭 1개에 RDS·EC2 같이 두고 보안 그룹으로 제한해도 됨.

---

## 2. RDS PostgreSQL 생성

### 2.1 콘솔에서 진행

1. **RDS** → **데이터베이스 생성**.
2. **엔진**: PostgreSQL 15.x (또는 16).
3. **템플릿**: **프리 티어** 또는 **개발/테스트** (비용 최소).
4. **설정**  
   - DB 인스턴스 식별자: `academy-db`  
   - 마스터 사용자명: **본인이 기억하기 쉬운 이름** (예: `admin97`, `academy_user`). 어차피 네이밍은 본인 기억이 중요하므로 자유. 단 **`.env`의 `DB_USER`와 반드시 동일**하게 넣을 것.  
   - 마스터 비밀번호: **강한 비밀번호** 설정 후 `.env`의 `DB_PASSWORD`에 동일하게.  
     - RDS 규칙: 8자 이상, 인쇄 가능 ASCII. **`/` `'` `"` `@` 기호는 사용 불가.**  
     - 추천: 영문 대소문자 + 숫자 + 기호(`!` `#` `$` `%` `*` 등, 위 4개 제외) 조합 **12자 이상**. 예: `AcaD3mY!2025` (직접 정한 뒤 `.env`에 그대로 입력).
5. **인스턴스 구성**  
   - **인스턴스 클래스**: **db.t4g.micro** (1 vCPU, 1GB RAM).  
   - 다중 AZ: **아니오** (Single-AZ).
6. **스토리지**  
   - **스토리지 유형**: gp3  
   - **할당된 스토리지**: **20** GB (또는 30 GB).  
   - **스토리지 자동 조정**: **활성화**, 최대 50~100 GB.
7. **백업**  
   - **자동 백업 보관 기간: 최소 7일 유지** (기본 7일). 운영 중 실수로 줄이지 말 것.
8. **연결**  
   - VPC: 기본 VPC (API·Worker와 **같은 VPC**).  
   - **퍼블릭 액세스: 아니오** 권장. API·Worker는 같은 VPC 내부 통신만 쓰면 되므로 DB를 외부에 열어둘 필요 없다. 500명 스타트라도 보안상 이쪽이 좋다.  
   - 서브넷: 퍼블릭 액세스 아니오면 RDS는 private 서브넷에 두고, API·Worker EC2도 같은 VPC 내에서 5432로 접속.  
   - VPC 보안 그룹: 새로 만들기 예) `rds-academy` → 인바운드 **PostgreSQL(5432)** 소스 = API·워커용 보안 그룹(아래 §5 참고).
9. **생성** 후 엔드포인트 복사 → `.env`의 `DB_HOST`에 넣기.

### 2.2 생성 후 확인

- 엔드포인트: `academy-db.xxxxx.ap-northeast-2.rds.amazonaws.com` → 배포용 `.env`의 `DB_HOST`에 넣기.
- 포트: 5432  
- DB 이름: 지정 안 했으면 **postgres**. 지정했으면 그 이름 → `DB_NAME`.

**✅ RDS 완료 후 다음:** §3 SQS 큐 생성 (로컬에서 스크립트 실행).

---

## 3. SQS 큐 생성

**✅ RDS 완료 후 진행.** 리전 **ap-northeast-2 (서울)** 에서 실행.

### 3.1 AWS CLI 자격 증명 (둘 중 하나)

**방법 A — `aws configure` 사용**

1. 터미널에서 `aws configure` 실행.
2. **AWS Access Key ID**, **AWS Secret Access Key**에는 **값만** 입력 (PowerShell 명령어 `$env:...` 넣지 말 것).
3. **Default region name**: `ap-northeast-2`
4. **Default output format**: 엔터만 치거나 `json` 입력.

**방법 B — 환경 변수로 한 번에 실행 (자격 증명 파일 문제 있을 때)**

Windows PowerShell에서 아래 한 줄씩 실행. `<ACCESS_KEY>`, `<SECRET_KEY>`를 실제 값으로 바꿈.

```powershell
cd C:\academy
$env:AWS_ACCESS_KEY_ID="<ACCESS_KEY>"; $env:AWS_SECRET_ACCESS_KEY="<SECRET_KEY>"; $env:AWS_DEFAULT_REGION="ap-northeast-2"; python scripts/create_sqs_resources.py ap-northeast-2
```

성공하면 이어서 (같은 터미널에서 환경 변수 유지된 상태):

```powershell
python scripts/create_ai_sqs_resources.py ap-northeast-2
```

### 3.2 생성되는 큐

- Video: `academy-video-jobs` (VisibilityTimeout **10800**), DLQ
- Messaging: `academy-messaging-jobs`, DLQ
- AI: `academy-ai-jobs-lite`, `academy-ai-jobs-basic`, `academy-ai-jobs-premium` + 각 DLQ

**✅ SQS 완료 후 다음:** §4 IAM 역할 생성 → §5 보안 그룹.

---

## 4. IAM 역할 (EC2용)

각 EC2에서 SQS·RDS(네트워크만)·EC2 Self-stop(워커만)·ECR pull을 쓰려면 IAM 역할이 필요함.

### 4.1 정책 예시 (이름: `academy-ec2-role`)

**인라인 또는 관리형 정책**에 아래 내용 포함.

- **SQS**: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`, `sqs:ChangeMessageVisibility` (리소스: `academy-*` 큐).
- **EC2 Self-stop** (Video/AI 워커만): `ec2:DescribeInstances`, `ec2:StopInstances` (리소스: 본 인스턴스 또는 `*`).
- **ECR** (이미지 pull): `ecr:GetAuthorizationToken` + `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` (리소스: 사용할 ECR 레포 ARN).

필요 시 **AmazonSQSFullAccess**(개발용), **AmazonEC2ContainerRegistryReadOnly** 조합으로 시작해도 됨.

### 4.2 역할 연결

- EC2 **인스턴스** 생성 시 **IAM 인스턴스 프로필**에 위 역할 연결.

**✅ IAM 역할 완료 후 다음:** §5 보안 그룹 생성.

---

## 5. 보안 그룹

- **이름 예**: `academy-api-sg`, `academy-worker-sg`, `rds-academy-sg`.

| 보안 그룹 | 인바운드 | 비고 |
|-----------|----------|------|
| `academy-api-sg` | TCP 8000 from 0.0.0.0/0 **또는** ALB/CloudFront만, SSH 22 from 본인 IP | API 서버. **8000 포트 0.0.0.0/0 오픈은 초기 테스트용만.** 오픈 직전에는 ALB 붙이거나 Nginx + 443 HTTPS 적용 권장 (로그인 시스템이면 HTTPS 기본). |
| `academy-worker-sg` | SSH 22 from 본인 IP | 워커 EC2 (아웃바운드만 RDS·SQS) |
| `rds-academy-sg` | TCP 5432 from `academy-api-sg`, `academy-worker-sg` | RDS |

RDS 퍼블릭 액세스 **아니오**면 RDS는 private 서브넷에 두고, 워커·API는 같은 VPC 내에서만 5432 접속.

**중요:** RDS 보안 그룹 인바운드에 **API·워커 보안 그룹**을 5432 소스로 추가해야 EC2에서 DB 접속 가능.

**✅ 보안 그룹 완료 후 다음:** §6 EC2 API 서버 생성.

---

## 6. EC2 — API 서버 (t4g.small)

### 6.1 인스턴스 설정

- **AMI**: Amazon Linux 2023.
- **인스턴스 유형**: **t4g.small** (2 vCPU, 2 GB RAM).
- **키 페어**: 기존 또는 새로 생성 (SSH 접속용).
- **스토리지**: **30 GB** gp3 (루트 볼륨).
- **IAM 인스턴스 프로필**: §4 역할.
- **보안 그룹**: `academy-api-sg`.
- **사용자 데이터** (선택 — 아래 수동 절차 대신 사용 가능):

```bash
#!/bin/bash
yum update -y
yum install -y docker
systemctl enable docker && systemctl start docker
# 이미지 pull·실행은 ECR 로그인 후 아래 §6.3 참고
```

### 6.2 ECR에 이미지 푸시 (로컬에서 1회)

**EC2가 t4g(ARM64)이므로** 로컬이 x86이면 반드시 **linux/arm64** 로 빌드해야 한다. 그렇지 않으면 EC2에서 실행 시 "exec format error" 발생.

```powershell
# ECR 로그인 (계정ID 예: 809466760795)
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
aws ecr create-repository --repository-name academy-api --region ap-northeast-2
# 없을 때만; 이미 있으면 "ResourceAlreadyExistsException" 나와도 무시

# ARM64 빌드 (베이스 → API 순서, 프로젝트 루트에서)
docker buildx create --use
docker buildx build --platform linux/arm64 -f docker/Dockerfile.base -t academy-base:latest --load .
docker buildx build --platform linux/arm64 -f docker/api/Dockerfile -t academy-api:latest --load .

# 푸시
docker tag academy-api:latest 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
```

**워커 이미지 (필수)**  
§7·§8·§9 진행 전에 아래 3종도 같은 방식으로 ARM64 빌드 후 ECR 푸시해야 한다.  
- `academy-messaging-worker` (docker/messaging-worker/Dockerfile)  
- `academy-video-worker` (docker/video-worker/Dockerfile)  
- `academy-ai-worker-cpu` (docker/ai-worker-cpu/Dockerfile)  

ECR 레포: `aws ecr create-repository --repository-name academy-messaging-worker --region ap-northeast-2` 등으로 없으면 생성 후 푸시.

- 로컬이 이미 ARM(M1/M2 등)이면 `--platform linux/arm64` 없이 일반 `docker build` 로 빌드 후 tag/push 해도 됨.

### 6.3 EC2 접속 후 API 컨테이너 실행

```bash
ssh -i your-key.pem ec2-user@<API-EC2-퍼블릭IP>
sudo yum install -y docker && sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker ec2-user
# 로그아웃 후 재접속

aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com
docker pull <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
```

**.env 파일**: 로컬 `.env`를 scp로 복사하거나, EC2에서 직접 작성.  
`DB_HOST`, `DB_PASSWORD`, `R2_*`, `SECRET_KEY`, `INTERNAL_WORKER_TOKEN`, `AWS_REGION`, `VIDEO_SQS_QUEUE_NAME` 등 필수.

```bash
docker run -d --name academy-api --restart unless-stopped \
  --env-file .env \
  -p 8000:8000 \
  <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
```

**배포용 .env (필수)**  
로컬에서 RDS 연결값이 반영된 .env를 만들어 EC2에 둬야 한다.  
로컬: `python scripts/prepare_deploy_env.py -o .env.deploy` → 생성된 `.env.deploy`를 scp로 EC2 `~/.env`에 복사.  
(또는 `.env.admin97` 등에 `DB_HOST_RDS` 등이 있으면 위 스크립트가 `DB_*`를 RDS 값으로 채운 .env.deploy를 생성한다.)

마이그레이션 및 헬스 확인:

```bash
docker exec academy-api python manage.py migrate --no-input
curl http://localhost:8000/health
```

→ `{"status":"healthy",...}` 가 나와야 한다.  
API 퍼블릭 IP가 확정되면 `.env`에 `API_BASE_URL=http://<API-IP>:8000` 설정 후, 워커가 있는 EC2에는 갱신된 .env를 다시 복사.

### 6.4 EC2 재시작 시 컨테이너 자동 실행 (재시작 정책)

EC2 인스턴스가 **Stop → Start** 되었을 때도 컨테이너가 자동으로 다시 떠야 한다. `--restart unless-stopped`만으로는 컨테이너 생성 시에만 적용되므로, 이미 떠 있는 컨테이너에는 아래로 한 번 더 적용해 두는 것이 좋다.

```bash
docker update --restart unless-stopped academy-api
```

- **API·Messaging·Video·AI 워커** 모두 동일하게, 해당 컨테이너 이름으로 `docker update --restart unless-stopped <컨테이너이름>` 실행 권장.
- systemd로 Docker 서비스가 `enabled`라면 인스턴스 부팅 시 Docker가 올라오고, 위 정책이 있으면 컨테이너도 자동 재실행된다.

**✅ API EC2 완료 후 다음:** §7 Messaging Worker EC2.

---

## 7. EC2 — Messaging Worker (t4g.micro, 상시)

- **인스턴스 유형**: **t4g.micro** (2 vCPU, 1 GB RAM).
- **스토리지**: 기본 8 GB로 충분 (필요 시 20 GB).
- **IAM·보안 그룹**: §4, §5 (worker용).
- **이미지**: `academy-messaging-worker` ECR 푸시 후 동일 방식으로 pull.

실행 예:

```bash
docker run -d --name academy-messaging-worker --restart unless-stopped \
  --env-file .env \
  -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker \
  <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker:latest
```

- **상시 1대 유지** (Self-stop 사용 안 함).

**✅ Messaging EC2 완료 후 다음:** §8 Video Worker EC2 (100GB EBS + 4GB 필수).

---

## 8. EC2 — Video Worker (t4g.medium, 4GB + 100GB) ★핵심

**프로덕션에서는 반드시 4GB + 디스크 100GB.**  
로컬 `docker-compose`의 `mem_limit: 2048m`은 **로컬용**이며, 3시간 영상은 이 스펙에서 OOM·실패가 나기 쉬우므로 **프로덕션은 4GB 고정**.

### 8.1 인스턴스 설정

- **인스턴스 유형**: **t4g.medium** (2 vCPU, **4 GB RAM**).
- **스토리지**: 루트 8 GB + **두 번째 EBS 볼륨 100 GB gp3** (또는 루트를 100 GB로 생성).
  - 콘솔: 인스턴스 생성 시 **스토리지 추가** → **100 GB gp3**.
  - 또는 인스턴스 생성 후 **볼륨 생성** → 100 GB gp3 → 인스턴스에 연결 → 파티션·포맷 후 해당 경로를 `/mnt/transcode`로 마운트 (예: `VIDEO_WORKER_TEMP_DIR=/tmp`로 쓰면 컨테이너 `/tmp`가 이 볼륨 사용).
  - **⚠️ 100GB 마운트 확인 필수**: 컨테이너 실행 전 EC2에서 `df -h` 실행. **`/mnt/transcode`가 약 100G로 보이면 정상.** 이걸 안 하면 루트 8GB에서 트랜스코딩 → 디스크 Full → 인코딩 실패가 가장 흔한 사고다.
- **Swap 1~2GB (선택·권장)**: 4GB면 충분하지만, 3시간 영상 + ffmpeg + Python heap이 순간 4GB를 넘을 수 있다. OOM Kill 방지용 보험으로 스왑을 두는 것을 권장한다.

  ```bash
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  # 재부팅 후에도 유지하려면 /etc/fstab에 추가: /swapfile none swap sw 0 0
  ```

- **Spot**: 비용 절감 시 **Spot 인스턴스 요청** 사용 가능 (멱등·재시도 구현돼 있음).  
  **Spot 회수 대비**: Spot 사용 시 2분 회수 알림(IMDS)을 받아 graceful shutdown 처리할 수 있다. 현재는 SQS 재처리 구조로 안전하지만, 장기적으로는 SIGTERM 핸들링 구현을 권장한다. (당장 필수는 아님.)
- **IAM**: §4 (SQS + EC2 Self-stop + ECR).

### 8.2 컨테이너 실행 (메모리 4GB 확보)

호스트가 4GB이므로 **Video 워커만** 돌릴 때는 `--memory` 생략해도 되지만, 나중에 같은 호스트에 AI 워커를 올릴 수 있으므로 **명시적으로 4GB 부여** 권장.

```bash
docker run -d --name academy-video-worker --restart unless-stopped \
  --memory 4g \
  --env-file .env \
  -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker \
  -e EC2_IDLE_STOP_THRESHOLD=5 \
  -v /mnt/transcode:/tmp \
  <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
```

- `/mnt/transcode`: 100 GB EBS를 **실제로 마운트한** 경로. 반드시 `df -h`로 해당 경로가 약 100G인지 확인한 뒤 컨테이너 실행.
- **Desired 0, 큐 깊이 ≥1이면 1개 기동, Max 2** 는 오토스케일링(람다+CloudWatch 또는 ASG)으로 구현하거나, 수동으로 필요 시에만 이 EC2 기동.
- **EC2 Idle Stop 테스트**: 메시지 1건 처리 후 큐가 비어 있는 상태에서, 설정한 빈 폴링 횟수(예: 5회) 지나면 **인스턴스가 실제로 자동 종료되는지** 한 번 확인하자. 안 하면 Self-stop이 안 돌아가서 월 24시간 과금될 수 있다.

**Video visibility (500 vs 10K 정렬)**  
500 단계에서는 **change_message_visibility 1회 호출**(VisibilityTimeout 10800)만 사용. 10K 이상으로 확장 시 10K SSOT 문서에 따라 **주기적 visibility extender** 도입 필수(장영상·중복 인코딩 방지).

**✅ Video Worker 완료 후:** §9 (선택) AI Worker CPU, §10 환경 변수 정리, §11 검증.

### 8.3 (선택) 같은 호스트에 AI Worker CPU

- t4g.medium 한 대에 Video 컨테이너(4GB) + AI CPU 컨테이너(1GB) 동시 실행 시, **동시에 부하가 몰리면 느려질 수 있음.**  
  운영 정책: 큐 깊이로 Video·AI를 나눠서 동시 가동을 피하거나, AI는 별도 t4g.small 1대를 두는 편이 안정적.

---

## 9. AI Worker CPU (별도 EC2 또는 Fargate)

- **스펙**: 0.5 vCPU / 1 GB (부하 시 2 GB).
- **Desired 0**, 큐 깊이 ≥1이면 1개, **Max 1~2**.
- EC2로 할 경우: **t4g.small** 1대 또는 t4g.micro 1대 (1 GB는 micro로 가능).  
  Self-stop 적용 시 IAM에 `ec2:StopInstances`, `ec2:DescribeInstances` 포함.

**AI Worker 런타임 정책 (10K SSOT 정렬)**  
AI Worker는 `docs/HEXAGONAL_10K_EXECUTION_PLAN.md`(10K SSOT)의 정책을 따른다.  
- **Visibility 3600** + 주기적 extender (장작업 재노출 방지)  
- **Lease 3540초 고정** (visibility − safety_margin)  
- **Inference 60분 상한** (초과 시 강제 fail + 메시지 delete)

```bash
docker run -d --name academy-ai-worker-cpu --restart unless-stopped \
  --env-file .env \
  -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker \
  -e EC2_IDLE_STOP_THRESHOLD=5 \
  <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu:latest
```

(ECR 레포·이미지 이름: `academy-ai-worker-cpu`, 프로젝트 Dockerfile: `docker/ai-worker-cpu/Dockerfile`.)

---

## 10. 환경 변수 정리 (.env)

배포 시 모든 EC2·컨테이너에 공통으로 필요한 값:

- `SECRET_KEY`, `DEBUG=false`, `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT=5432`, `DB_CONN_MAX_AGE=60`
- `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`, `R2_PUBLIC_BASE_URL`, `R2_AI_BUCKET`, `R2_VIDEO_BUCKET`, `R2_EXCEL_BUCKET`
- `INTERNAL_WORKER_TOKEN`, `API_BASE_URL` (실제 API URL)
- `AWS_REGION=ap-northeast-2`, `VIDEO_SQS_QUEUE_NAME=academy-video-jobs`, `MESSAGING_SQS_QUEUE_NAME=academy-messaging-jobs`, AI 큐 이름들
- `EC2_IDLE_STOP_THRESHOLD=5` (Video, AI 워커만)
- Worker 전용: `DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker`, `VIDEO_WORKER_ID`, `AI_WORKER_ID_CPU`, `MESSAGING_WORKER_ID`

API 서버에는 `DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod` (또는 기본 prod).

---

## 11. 검증

1. **API**: `curl https://<API-도메인 또는 IP>:8000/health` → 200.
2. **RDS**: API에서 로그인·조회 동작 확인.
3. **Video 큐**: 테스트 메시지 넣고 Video Worker 로그에서 처리 확인. 3시간 영상은 한 건 처리 시 메모리 4GB 이내·디스크 100 GB 이내인지 로그·모니터링으로 확인.
4. **Messaging**: 테스트 발송 또는 예약 발송 1건.
5. **DLQ**: SQS 콘솔에서 `academy-*-dlq` 메시지 0인지 주기 확인.

### 11.1 CloudWatch 로그 보관 기간 (비용 방어)

- CloudWatch → **Log groups** → Video Worker(및 기타 워커) 로그 그룹 선택 → **Edit retention setting**.
- **Retention: 7일 또는 14일**로 변경. 기본값은 영구 보관이라 ffmpeg·3시간 인코딩·디버그 로그가 쌓이면 한 달 지나면서 비용이 올라간다.

---

## 12. 안정성 평가 및 첫 달 비용 (500명 기준)

### 12.1 안정성 평가

| 영역 | 상태 |
|------|------|
| DB 부하 | 매우 낮음 |
| API 부하 | 낮음 |
| Video 인코딩 | 안전 (4GB + 100GB면 충분) |
| 메시징 | 여유 |
| SQS 중복 처리 | 안전 (Visibility 10800 + ChangeMessageVisibility) |
| Spot 회수 | 재시도 가능 (멱등·재처리 구현됨) |
| 비용 | 10~15만 원 예상 |

→ **터질 확률 매우 낮음.**

### 12.2 예상 실제 첫 달 비용 (서울 리전 ap-northeast-2)

| 항목 | 예상 (월) |
|------|-----------|
| API t4g.small | 3~4만 원 |
| RDS micro | 2~3만 원 |
| Messaging micro | 1~2만 원 |
| Video (Spot) 가동시간 30~60h | 2~4만 원 |
| 기타 (EBS, R2, CloudWatch 등) | 1~2만 원 |
| **총합** | **9~15만 원 범위** |

예산 30만 원이면 완전 안전권.

---

## 13. 요약 체크리스트

| 항목 | 설정 |
|------|------|
| API | t4g.small 1대, 30GB, 또는 Fargate 0.5 vCPU / 2GB |
| RDS | db.t4g.micro, Single-AZ, 20~30GB gp3, 오토스케일 ON |
| Messaging | t4g.micro 상시, 또는 Fargate 0.25 vCPU / 1GB |
| Video Worker | **t4g.medium (4GB RAM)** + **EBS 100GB gp3**, `--memory 4g`, Desired 0, Max 2 |
| AI Worker CPU | 0.5 vCPU / 1GB, Desired 0, Max 1~2 |
| Video 프로덕션 | **로컬 2GB 아님 → 반드시 4GB** |
| 배포 전 5가지 | RDS 퍼블릭 끄기, 100GB 마운트 확인(`df -h`), CloudWatch 7~14일, Idle Stop 동작 테스트, 8000 포트는 임시·오픈 전 HTTPS |
| 오픈 전 실전 체크 4개 | ALB+HTTPS 실제 적용, RDS `max_connections`·커넥션 모니터링, Self-Stop 진짜 동작 1회 확인, Swap은 보험·`free -h`로 확인 |
| EC2 재시작 정책 | `docker update --restart unless-stopped <컨테이너이름>` (API·워커 공통) |
| RDS 백업 | 자동 백업 보관 기간 최소 7일 유지 |
| Video Swap | 선택·권장: 2GB 스왑 (OOM 방지) |
| Spot 회수 | SQS 재처리로 안전; 장기적으로 SIGTERM 핸들링 권장 |

---

## 14. 최종 평가

### 14.1 진짜 냉정한 평가

| 항목 | 판단 |
|------|------|
| 구조 붕괴 가능성 | 거의 없음 |
| 500명에서 다운 가능성 | 매우 낮음 |
| 비용 폭탄 가능성 | Self-stop 확인하면 없음 |
| 보안 리스크 | HTTPS만 지키면 안정 |
| 운영 난이도 | 낮음 |
| 1천 명 확장 | 무리 없음 |
| 3천 명 확장 | Video max 2→3 조정 필요 |

### 14.2 최종 결론

- 지금 문서 수준이면 **500명 스타트 충분히 안전**하다.
- 예산 **30만 원**이면 **완전 안정권**이다.
- 비용 **9~15만 원** 현실적이다.
- 구조적으로 **10K 대비 일관성**도 유지된다.

(이전 §14 요약: 구조 안정성·비용 예측·실전 사고 방지·운영 가이드 명확성·500명 적합성·1천 명 확장·10K 일관성 모두 충족.)

---

이 문서: `docs/SSOT_0215/AWS_500_START_DEPLOY_GUIDE.md`.  
상세 환경 변수·Dockerfile 경로: `.env.example`, `docker/` 디렉터리. 기계 정렬 참조: `docs/cursor_docs/AWS_500_DOCKER_REQUIREMENTS_ALIGNMENT.md`, `docs/SSOT_0215/CODE_ALIGNED_SSOT.md`.
