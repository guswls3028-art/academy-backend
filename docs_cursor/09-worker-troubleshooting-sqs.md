# 워커 작업(영상 인코딩, 엑셀 수강등록) 안 될 때 — 원인 규명

인코딩(워커)과 엑셀 수강등록이 안 되는 경우, **설정(AWS/SQS)** 문제인지 **코드** 문제인지 구분하려면 아래 순서로 확인하세요.

---

## 이 프로젝트 기준 복붙용 명령어

아래는 **Windows PowerShell → API 서버 EC2 SSH → 진단/로그** 순서로 그대로 복붙해서 쓰면 됩니다. 키 경로·인스턴스 이름은 이 repo 설정 기준입니다.

### Step 0: PowerShell에서 API 서버 IP 확인 후 SSH

```powershell
cd C:\academy
$env:AWS_ACCESS_KEY_ID = "여기에_액세스키"
$env:AWS_SECRET_ACCESS_KEY = "여기에_시크릿키"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"

# academy-api 퍼블릭 IP만 출력
aws ec2 describe-instances --region ap-northeast-2 --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-api" --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], PublicIpAddress]" --output text
```

출력 예: `academy-api  15.165.147.157` → 아래에서 `15.165.147.157` 자리에 넣고 SSH 접속.

```powershell
ssh -i C:\key\backend-api-key.pem ec2-user@15.165.147.157
```

(IP는 위 `aws ec2 describe-instances` 출력으로 매번 확인해도 됨.)

---

### Step 1: API 서버(ec2-user)에 들어온 뒤 — SQS 진단

**1-1) 컨테이너 안에 스크립트 있는지 확인**

```bash
docker exec academy-api ls -la /app/scripts/check_sqs_worker_connectivity.py
```

- **파일 있음** → 아래 1-2-A 실행  
- **No such file** → 아래 1-2-B 실행  

**1-2-A) 컨테이너 안에서 진단 실행 (스크립트 있을 때)**

```bash
docker exec -it academy-api python scripts/check_sqs_worker_connectivity.py
```

**1-2-B) 스크립트 없을 때 (호스트 academy 디렉터리 마운트해서 실행)**

```bash
cd /home/ec2-user/academy
docker exec academy-api env > /tmp/api_env.txt
IMAGE=$(docker inspect academy-api --format '{{.Config.Image}}')
docker run --rm -v "$(pwd):/app" --env-file /tmp/api_env.txt "$IMAGE" python scripts/check_sqs_worker_connectivity.py
```

(실행 중인 API 컨테이너와 같은 이미지를 자동으로 씁니다.)

**1-2 출력 전체**를 저장해 두고, 문서 상단 "결과 해석" 표와 비교.

---

### Step 2: API 서버에서 컨테이너 상태·API 로그

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

```bash
docker logs academy-api --tail 200 2>&1 | grep -E "enqueue|SQS|503|video|EXCEL|Failed"
```

---

### Step 3: Video / AI 워커 로그 (각각 다른 EC2일 수 있음)

**Video 워커**가 **API와 같은 EC2**에 있으면 (같은 서버에 academy-api, academy-video-worker 둘 다 있으면):

```bash
docker logs academy-video-worker --tail 80
```

```bash
docker logs academy-ai-worker-cpu --tail 80
```

**Video/AI 워커가 별도 EC2**에 있으면, 터미널에서 API 서버 SSH 종료(`exit`) 후 PowerShell에서:

```powershell
# Video 워커 서버 IP
aws ec2 describe-instances --region ap-northeast-2 --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-video-worker" --query "Reservations[].Instances[].PublicIpAddress" --output text
```

나온 IP로 SSH (키: `C:\key\video-worker-key.pem`):

```powershell
ssh -i C:\key\video-worker-key.pem ec2-user@<Video워커IP>
```

들어가서:

```bash
docker logs academy-video-worker --tail 80
```

AI 워커도 별도 EC2면:

```powershell
aws ec2 describe-instances --region ap-northeast-2 --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-ai-worker-cpu" --query "Reservations[].Instances[].PublicIpAddress" --output text
```

```powershell
ssh -i C:\key\ai-worker-key.pem ec2-user@<AI워커IP>
```

```bash
docker logs academy-ai-worker-cpu --tail 80
```

---

### 한 번에 복붙용 — API 서버에서만 돌릴 때 (스크립트 있다고 가정)

API 서버에 이미 SSH 접속한 상태에서 아래 블록 통째로 복붙:

```bash
echo "=== SQS 진단 ==="
docker exec academy-api python scripts/check_sqs_worker_connectivity.py
echo ""
echo "=== 컨테이너 상태 ==="
docker ps --format "table {{.Names}}\t{{.Status}}"
echo ""
echo "=== API 로그 (enqueue/SQS/video/EXCEL) ==="
docker logs academy-api --tail 150 2>&1 | grep -E "enqueue|SQS|503|video|EXCEL|Failed" || true
echo ""
echo "=== Video 워커 로그 (같은 서버일 때) ==="
docker logs academy-video-worker --tail 50 2>/dev/null || echo "(academy-video-worker 없음)"
echo ""
echo "=== AI 워커 로그 (같은 서버일 때) ==="
docker logs academy-ai-worker-cpu --tail 50 2>/dev/null || echo "(academy-ai-worker-cpu 없음)"
```

---

## 1단계: SQS 연결 진단 (필수)

**API 서버와 동일한 환경**에서 아래 스크립트를 실행한 뒤, **전체 출력 결과**를 확인하세요.

**EC2/Linux에서 API가 Docker로 실행 중일 때 (권장):**
```bash
docker exec -it academy-api python scripts/check_sqs_worker_connectivity.py
```

**이미지에 `scripts`가 아직 없어 위 명령이 "No such file"일 때 (재배포 전 임시):**  
호스트의 `academy` 디렉터리를 마운트해 같은 이미지로 스크립트만 실행합니다. API 컨테이너와 동일한 환경 변수를 쓰려면 아래처럼 실행하세요.
```bash
cd /home/ec2-user/academy
docker exec academy-api env > /tmp/api_env.txt
docker run --rm -v "$(pwd):/app" --env-file /tmp/api_env.txt academy-api:latest python scripts/check_sqs_worker_connectivity.py
```
(이미지 태그가 다르면 `docker images`로 확인 후 `academy-api:<태그>`로 바꾸세요.)

**EC2/Linux 호스트에서 직접 실행할 때:**
```bash
cd /home/ec2-user/academy
export DJANGO_SETTINGS_MODULE=apps.api.config.settings.base
python3 scripts/check_sqs_worker_connectivity.py
```

**Windows:** `cd C:\academy`, `set DJANGO_SETTINGS_MODULE=...`, `python scripts/...`

### 결과 해석

| 스크립트 출력 | 의미 | 조치 |
|---------------|------|------|
| `[1] Video 큐: ... get_queue_url: OK`, `[2] AI(Basic) 큐: ... get_queue_url: OK` | SQS 연결 정상 | 2단계(워커 실행/로그) 확인 |
| `FAIL: 큐 접근 불가 (자격 증명/권한 문제)` 또는 `InvalidClientTokenId` / `SignatureDoesNotMatch` | AWS 자격 증명 없음/잘못됨/만료 | API·워커 서버에 올바른 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`(또는 `AWS_DEFAULT_REGION`) 설정. IAM 사용 시 해당 역할에 SQS `GetQueueUrl`, `SendMessage`, `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility` 권한 필요 |
| `FAIL: 큐가 존재하지 않습니다` 또는 `QueueDoesNotExist` / `NonExistentQueue` | 해당 리전에 큐 없음 | 같은 리전(예: ap-northeast-2)에서 `python scripts/create_sqs_resources.py`, `python scripts/create_ai_sqs_resources.py` 실행해 큐 생성 |
| `FAIL: AWS 자격 증명 오류 또는 SQS 권한 없음` | 권한 부족 | IAM 정책에 SQS 위 권한 추가 |

**정리**:  
- **설정 문제** → 위 표의 조치 후 API/워커 재기동  
- **스크립트는 전부 OK인데도 작업이 안 됨** → 2단계(워커 프로세스·로그) 확인

---

## 2단계: 워커 실행 여부 및 로그

SQS 진단이 모두 OK이면, 워커가 실제로 떠 있는지와 에러 로그를 봅니다.

- **Video Worker**  
  - 실행 여부: `docker ps \| findstr video-worker` (또는 `docker ps -a`)  
  - 로그: `docker logs <video_worker_container>`  
  - 기대: `Video Worker (SQS) started`, `SQS_MESSAGE_RECEIVED`, `SQS_JOB_COMPLETED` 등  
  - 에러 예: `SQS unavailable`, `Queue URL unavailable`, `Error enqueuing`(이건 API 로그)

- **AI Worker (엑셀 수강등록)**  
  - 실행 여부: `docker ps \| findstr ai-worker`  
  - 로그: `docker logs <ai_worker_container>`  
  - 기대: `EXCEL_PARSING processed_by=worker` 등

---

## 3단계: API 쪽 로그

- **영상**  
  - 업로드 완료 직후: `Video job enqueued: video_id=...` → SQS 전송 성공  
  - `Failed to enqueue video job` 또는 503 응답 → SQS 전송 실패(1단계 재확인)  
- **엑셀 수강등록**  
  - `EXCEL_PARSING dispatch ... job_id=...` → Job 생성 및 SQS 발행 시도  
  - 그 다음 에러 로그가 있으면 발행 실패(역시 SQS/자격 증명 확인)

---

## 요약

1. **반드시 먼저**: `scripts/check_sqs_worker_connectivity.py`를 **API와 동일한 환경**에서 실행하고, 출력 전체를 확인해 위 표대로 원인 특정.  
2. **스크립트가 모두 OK**이면 워커 프로세스·로그와 API 로그로 진행.  
3. **코드 버그 가능성**은 SQS 진단이 OK이고, 워커도 떠 있는데 특정 job만 반복 실패할 때 함께 확인하면 됩니다.
