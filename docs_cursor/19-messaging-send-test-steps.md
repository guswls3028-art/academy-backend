# 메시지 발송 테스트 절차 (명령어·확인만, 추측 없음)

**목적**: 메시지를 제대로 발송 테스트하기 위해 **어디서 뭘 하면 되는지**를 명령어로만 안내.  
설정/값은 **반드시 실제로 확인**한다.

---

## 사전 확인 — 사용하는 설정이 어디서 오는지

- **Django(API)**  
  `apps/api/config/settings/base.py` 에서 다음을 **환경변수**로 읽음:  
  `SOLAPI_API_KEY`, `SOLAPI_API_SECRET`, `SOLAPI_SENDER`, `MESSAGING_SQS_QUEUE_NAME`(기본 `academy-messaging-jobs`), `AWS_REGION`(boto3용).  
  `manage.py` 는 프로젝트 루트의 `.env` → `.env.local` 순으로 `load_dotenv` 로드.
- **Messaging Worker**  
  `apps/worker/messaging_worker/config.py` 의 `load_config()` 가 **환경변수만** 사용.  
  필수: `SOLAPI_API_KEY`, `SOLAPI_API_SECRET`, `SOLAPI_SENDER`.  
  선택: `MESSAGING_SQS_QUEUE_NAME`(기본 `academy-messaging-jobs`), `AWS_REGION`(기본 `ap-northeast-2`).

**값을 추측하지 말고, 아래 명령으로 실제 값을 확인할 것.**

---

## 1단계: 환경·설정 확인

### 1-1. API에서 쓰는 값 확인 (로컬이면 .env 기준)

프로젝트 루트(`C:\academy`)에서:

```powershell
cd C:\academy
.\venv\Scripts\activate
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.dev"
python -c "import os; from pathlib import Path; from dotenv import load_dotenv; load_dotenv(Path('.') / '.env'); load_dotenv(Path('.') / '.env.local'); [print(k + '=' + ('(set)' if os.getenv(k,'') else '(empty)')) for k in ['SOLAPI_API_KEY','SOLAPI_API_SECRET','SOLAPI_SENDER','MESSAGING_SQS_QUEUE_NAME','AWS_REGION']]"
```

(PowerShell에서 `&&` 대신 한 줄에 `;` 로 이어서 실행하거나, 위처럼 한 줄로 넣어도 됨.)

- `(empty)` 인 항목이 있으면 **그 환경에서 API/워커가 동작하지 않거나 기본값으로 동작**함.  
  실제로 쓰는 값은 `.env` / `.env.local` / 배포 환경의 env를 직접 열어서 확인.

### 1-2. 테넌트 DB 값 확인 (잔액·발신번호·활성화)

```powershell
cd C:\academy
.\venv\Scripts\activate
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.dev"
python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'apps.api.config.settings.dev')
django.setup()
from apps.core.models import Tenant
for t in Tenant.objects.filter(is_active=True).values('id','code','credit_balance','messaging_sender','messaging_is_active'):
    print(t)
"
```

- 여기서 **실제** `credit_balance`, `messaging_sender`, `messaging_is_active` 를 확인.  
  발송 테스트할 테넌트의 `code`(예: hakwonplus)를 기억할 것.

### 1-3. SQS 큐 존재 여부 확인 (같은 리전·자격증명)

```powershell
cd C:\academy
.\venv\Scripts\activate
# .env 로드 후 boto3 호출 (AWS 자격증명이 env에 있으면)
python -c "import os; from pathlib import Path; from dotenv import load_dotenv; load_dotenv(Path('.')/'.env'); load_dotenv(Path('.')/'.env.local'); import boto3; region=os.environ.get('AWS_REGION','ap-northeast-2'); name=os.environ.get('MESSAGING_SQS_QUEUE_NAME','academy-messaging-jobs'); sqs=boto3.client('sqs',region_name=region); r=sqs.get_queue_url(QueueName=name); print('Queue URL:', r.get('QueueUrl'))"
```

- 실패하면: AWS 자격증명(프로필/환경변수), 리전, 큐 이름이 실제와 일치하는지 확인.  
  큐가 없으면 `scripts/create_sqs_resources.py` 로 생성(코드 기준: `create_messaging_sqs_resources()` 가 `academy-messaging-jobs`, `academy-messaging-jobs-dlq` 생성).

---

## 2단계: API 서버 기동 (로컬 발송 테스트 시)

```powershell
cd C:\academy
.\venv\Scripts\activate
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.dev"
python manage.py runserver
```

- 다른 터미널에서 아래 단계 진행.  
  배포 환경에서 테스트할 경우에는 이미 떠 있는 API 기준으로 하면 됨.

---

## 3단계: JWT 발급 (send API 호출용)

- **엔드포인트**: `POST /api/v1/token/`  
- **필요**: `username`, `password`, **테넌트 식별** (`X-Tenant-Code` 헤더 또는 body `tenant_code`).  
- **응답**: `{ "access": "...", "refresh": "..." }` → `access` 를 Bearer 로 씀.

**실제 호출 예 (PowerShell, 로컬 API 기준):**

```powershell
# BASE_URL 은 실제 사용하는 API 주소로 바꿀 것 (로컬이면 http://127.0.0.1:8000)
$BASE_URL = "http://127.0.0.1:8000"
$TENANT_CODE = "hakwonplus"
$USERNAME = "실제_로그인_아이디"
$PASSWORD = "실제_비밀번호"

$body = @{ username = $USERNAME; password = $PASSWORD } | ConvertTo-Json
$headers = @{
  "Content-Type" = "application/json"
  "X-Tenant-Code" = $TENANT_CODE
}
$r = Invoke-RestMethod -Method POST -Uri "$BASE_URL/api/v1/token/" -Body $body -Headers $headers
$TOKEN = $r.access
Write-Host "Access token (앞 20자): $($TOKEN.Substring(0, [Math]::Min(20, $TOKEN.Length)))..."
```

- `$USERNAME` / `$PASSWORD` / `$TENANT_CODE` 는 **실제 DB·테넌트에 맞게** 넣을 것.  
  실패 시 응답 body 로 "테넌트 정보가 필요합니다" / "로그인 아이디 또는 비밀번호" 등 확인.

---

## 4단계: send API 호출 (실제 발송 요청)

- **엔드포인트**: `POST /api/v1/messaging/send/`  
- **헤더**: `Authorization: Bearer <access>`, `X-Tenant-Code: <테넌트코드>`, `Content-Type: application/json`  
- **Body**: `student_ids`, `send_to`, `message_mode`, `raw_body` 등 (코드 기준: `SendMessageRequestSerializer`).

**실제 호출 예:**

```powershell
$BASE_URL = "http://127.0.0.1:8000"
$TENANT_CODE = "hakwonplus"
# 3단계에서 받은 토큰
$TOKEN = "여기에_access_토큰_전체_붙여넣기"

$body = @{
  student_ids = @(1)
  send_to = "parent"
  message_mode = "sms"
  raw_body = "발송 테스트 메시지"
} | ConvertTo-Json

$headers = @{
  "Authorization" = "Bearer $TOKEN"
  "Content-Type"  = "application/json"
  "X-Tenant-Code" = $TENANT_CODE
}

Invoke-RestMethod -Method POST -Uri "$BASE_URL/api/v1/messaging/send/" -Body $body -Headers $headers
```

- **응답**에 `enqueued` 가 1 이상이면 API는 정상적으로 SQS에 넣은 것.  
- 400/403/500 이면 응답 body 그대로 확인 (발신번호 미등록, 권한, 학생 없음 등).

---

## 5단계: 워커 기동 (SQS → Solapi 실제 발송)

워커는 **환경변수**만 사용한다. 위 1-1에서 확인한 것과 동일한 큐 이름·리전·Solapi 값이 들어가 있어야 함.

### 5-1. 로컬에서 워커 실행 (실제 Solapi 호출)

```powershell
cd C:\academy
.\venv\Scripts\activate
# .env / .env.local 에 SOLAPI_*, MESSAGING_SQS_QUEUE_NAME, AWS_REGION 등이 있어야 함
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.worker"
python -m apps.worker.messaging_worker.sqs_main
```

- `load_config()` 실패(필수 env 없음)면 즉시 종료.  
- 정상이면 SQS Long Polling 시작. 4단계에서 넣은 메시지가 있으면 곧 처리되고, Solapi로 실제 발송 시도.

### 5-2. Mock으로만 테스트 (실제 문자 안 나가게)

```powershell
$env:DEBUG = "true"
# 또는
$env:SOLAPI_MOCK = "true"
# 그 다음 5-1과 동일하게
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.worker"
python -m apps.worker.messaging_worker.sqs_main
```

- 코드 기준: `DEBUG=True` 또는 `SOLAPI_MOCK=true` 이면 `MockSolapiMessageService` 사용 → 로그만 남고 실제 API 호출 없음.

---

## 6단계: 발송 결과 확인

### 6-1. NotificationLog (워커가 Solapi 호출 후 기록)

```powershell
cd C:\academy
.\venv\Scripts\activate
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.dev"
python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'apps.api.config.settings.dev')
django.setup()
from apps.support.messaging.models import NotificationLog
for log in NotificationLog.objects.order_by('-id')[:5]:
    print(log.id, log.tenant_id, log.success, log.recipient_summary, log.failure_reason)
"
```

- send API 호출 후 워커가 돌았으면 여기에 기록이 생김.  
  실패 시 `failure_reason`(예: insufficient_balance, sender_required 등)으로 원인 확인.

### 6-2. 워커 로그 (배포 시 Docker)

배포 환경에서 워커가 Docker로 도는 경우:

```powershell
docker logs academy-messaging-worker --tail 100
```

- 컨테이너 이름이 다르면 해당 이름으로 교체.  
  로그에 `send_sms ok` / `insufficient_balance` / `sender_required` 등이 찍힌다.

---

## 요약 체크리스트

| 순서 | 할 일 | 확인 방법 |
|------|--------|------------|
| 1 | API/워커가 쓰는 env 확인 | 1-1 스크립트 (값은 .env 등에서 직접 확인) |
| 2 | 테넌트 잔액·발신번호·활성화 확인 | 1-2 스크립트 |
| 3 | SQS 큐 존재·리전 확인 | 1-3 스크립트 |
| 4 | API 서버 기동 (로컬 시) | runserver |
| 5 | JWT 발급 | POST /api/v1/token/ + X-Tenant-Code |
| 6 | send 호출 | POST /api/v1/messaging/send/ → enqueued 확인 |
| 7 | 워커 기동 | sqs_main (env 준비 후) |
| 8 | 결과 확인 | NotificationLog + 워커 로그 |

**설정/값은 위 명령으로 “있는지·어디서 오는지”만 확인하고, 실제 값은 반드시 프로젝트의 .env·배포 env에서 직접 확인할 것.**
