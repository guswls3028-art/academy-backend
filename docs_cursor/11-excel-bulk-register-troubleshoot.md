# 엑셀 일괄 등록 실패 시 확인

## 흐름

1. 프론트: POST `/api/v1/students/bulk_create_from_excel/` (파일 + initial_password)
2. API: R2에 엑셀 업로드 → excel_parsing job 생성 → 202 { job_id }
3. 프론트: 1초마다 GET `/api/v1/jobs/<job_id>/` 폴링 → DONE/FAILED 시 완료 표시
4. AI 워커: SQS에서 job 가져와 엑셀 파싱 → 학생 생성

## 실패 시 확인 순서

### 1. 업로드 단계에서 실패 (모달에서 바로 에러 메시지)

- **API 서버 .env**에 R2 설정 있는지 확인:
  - `R2_ENDPOINT`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`
  - `R2_EXCEL_BUCKET` 또는 `EXCEL_BUCKET_NAME` (없으면 academy-excel 사용)
- R2 버킷이 실제로 존재하고, 위 키로 접근 가능한지 확인.
- 브라우저 개발자 도구 → Network: `bulk_create_from_excel` 요청이 **400/500** 이면 응답 본문에 `detail` 등 에러 내용 있음.

### 2. 작업 상태 조회 502 (업로드는 됐는데 “실패”로 보임)

- GET `/api/v1/jobs/<job_id>/` 가 502 나오면 폴링이 실패해 완료/실패를 못 보여줌.
- Cloudflare SSL **Flexible**, API 서버 **80/8000** 열림, nginx → 8000 프록시 확인 (이전 502 점검 참고).
- 브라우저 Network에서 `jobs/` 요청이 **502** 인지 확인.

### 3. 워커가 job을 안 가져감 (job은 PENDING 그대로) — **워커 서버가 꺼졌을 때**

엑셀 일괄등록은 **AI 워커(academy-ai-worker-cpu)** 가 `academy-ai-jobs-basic` 큐를 폴링해서 처리합니다. 워커가 꺼져 있으면 메시지는 큐에만 쌓이고 처리되지 않습니다.

#### 3-1. AI 워커 ASG/인스턴스 확인

```powershell
# AI 워커 ASG desired / 인스턴스 수
aws autoscaling describe-auto-scaling-groups --region ap-northeast-2 `
  --auto-scaling-group-names academy-ai-worker-asg `
  --query "AutoScalingGroups[0].{DesiredCapacity:DesiredCapacity,MinSize:MinSize,MaxSize:MaxSize,Instances:length(Instances)}" --output table

# 실행 중인 AI 워커 인스턴스 (이름 + 퍼블릭 IP)
aws ec2 describe-instances --region ap-northeast-2 `
  --filters "Name=tag:Name,Values=academy-ai-worker-cpu" "Name=instance-state-name,Values=running" `
  --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value|[0],PublicIpAddress]" --output text
```

- **DesiredCapacity=0 이거나 Instances가 0개** → 워커가 꺼져 있음. 수동으로 desired 1로 올리거나, 큐에 메시지가 있으면 Lambda(autoscale)가 올려줄 수 있음.
- **인스턴스는 있는데 퍼블릭 IP가 None** → 프라이빗 서브넷이면 정상. SSH는 Bastion 등으로만 가능.

#### 3-2. 수동으로 AI 워커 1대 기동 (당장 처리 필요할 때)

```powershell
aws autoscaling set-desired-capacity --region ap-northeast-2 `
  --auto-scaling-group-name academy-ai-worker-asg --desired-capacity 1
```

인스턴스가 뜨고 user_data로 Docker + `academy-ai-worker-cpu` 컨테이너가 기동될 때까지 2~5분 걸릴 수 있음.

#### 3-3. SQS / 워커 연결 진단 스크립트

API 서버와 동일한 환경에서 SQS 접근·큐 존재 여부 확인:

```powershell
cd C:\academy
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.base"
python scripts/check_sqs_worker_connectivity.py
```

(또는 EC2 API 서버에서 `docker exec -it academy-api python scripts/check_sqs_worker_connectivity.py`)

- **AI(Basic) 큐 FAIL** → 큐 미존재면 `python scripts/create_ai_sqs_resources.py` 실행 후 재확인. 자격 증명 오류면 API/워커의 AWS 설정 확인.

#### 3-4. 워커 로그 확인 (인스턴스에 접속 가능할 때)

AI 워커 인스턴스에 SSH 접속 후:

```bash
sudo docker logs -f academy-ai-worker-cpu
```

- `SQS_MESSAGE_RECEIVED | job_id=... | tier=basic` 후 `EXCEL_PARSING processed_by=worker` 가 나오면 정상 처리.
- 로그가 전혀 안 나오고 큐에 메시지가 쌓여 있으면 → 워커가 해당 큐를 안 받고 있거나, 다른 인스턴스만 돌고 있을 수 있음 (Basic 큐는 `academy-ai-jobs-basic` 한 종류만 있음).

#### 요약

| 상황 | 확인 | 조치 |
|------|------|------|
| job PENDING 그대로 | ASG desired / 인스턴스 수 | desired ≥ 1 로 설정 또는 Lambda 스케일 대기 |
| 큐 접근 실패 | check_sqs_worker_connectivity.py | create_ai_sqs_resources.py 또는 AWS 자격 증명 |
| 워커는 켜져 있는데 안 먹음 | 워커 로그, 큐 URL/이름 | .env 의 AI_SQS_QUEUE_NAME_BASIC=academy-ai-jobs-basic 일치 여부 |

- **AI 워커** 인스턴스가 1대 이상 떠 있는지 (ASG desired >= 1).
- SQS `academy-ai-jobs-basic` 에 메시지가 쌓이는지 (AWS SQS 콘솔에서 확인 가능).
- 워커 EC2/컨테이너 로그에 excel_parsing 처리 로그 또는 에러가 있는지.

### 4. 워커가 처리했는데 FAILED

- GET `/api/v1/jobs/<job_id>/` 응답에 `status: "FAILED"`, `error_message` 있음.
- 워커 로그에서 해당 job_id / excel_parsing 예외 확인.
- 워커 .env에도 R2 설정 필요 (동일 버킷에서 엑셀 다운로드).

## 한 줄 요약

업로드 즉시 에러 → R2 설정/버킷. 업로드 후 진행만 안 보임 → jobs 502 또는 AI 워커 미기동. 완료됐는데 실패 표시 → job 상태가 FAILED면 error_message 확인.
