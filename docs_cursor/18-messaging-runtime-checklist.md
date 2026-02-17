# 메시징 런타임 4계층 진단 체크리스트

**문서가 아니라 실제 런타임 계층**을 봐야 함. 메시지가 안 나갈 때는 아래 4계층 중 하나에서 막힌다.

| 계층 | 내용 | 막히면 |
|------|------|--------|
| **[1] API** | POST send → DB/SQS 전까지 | 200이 안 나오거나, SQS enqueue 실패 |
| **[2] SQS enqueue** | API → boto3 send_message | AccessDenied, NonExistentQueue, region/URL 오류 |
| **[3] Worker** | SQS polling → 메시지 소비 | 컨테이너 죽음, 로그 없음, parsing 실패, self-stop |
| **[4] Solapi** | Worker → Solapi API 호출 | 401, invalid api key, sender not verified, **Tenant.messaging_sender 비어 있음** |

---

## 1단계 — API 레벨 확인

### 1️⃣ send API 직접 호출

```http
POST /api/v1/messaging/send/
Authorization: Bearer <access>
X-Tenant-Code: hakwonplus
Content-Type: application/json

{ "student_ids": [1], "send_to": "parent", "message_mode": "sms", "raw_body": "테스트" }
```

**응답 해석**

| 상태 | 의미 |
|------|------|
| 200/201 | API는 정상. body에 `enqueued` 건수 확인. |
| 400 | body validation (student_ids, send_to, template_id 등) |
| 403 | 권한(테넌트/역할) 문제 |
| 500 | 내부 로직/예외 |

### 2️⃣ DB 로그 (NotificationLog) — 주의

- **API는 NotificationLog를 쓰지 않는다.**  
  **NotificationLog는 워커가 Solapi 호출 후** `create_notification_log()` 로만 생성된다.
- 따라서 **NotificationLog가 없다** = 2/3/4 단계 중 한 곳에서 막힌 것 (SQS 미전달, 워커 미처리, Solapi 실패).
- 확인 예시:

```bash
python manage.py shell
```

```python
from apps.support.messaging.models import NotificationLog
NotificationLog.objects.all().order_by("-id")[:5]
```

- ✔ 최근 발송 후 로그 있음 → 워커까지 도달해 Solapi 시도 후 기록된 것.
- ❌ 로그 없음 → API 200이어도 SQS/Worker/Solapi 중 하나 문제.

---

## 2단계 — SQS enqueue 확인

- API에서 SQS로 넣는 코드: `apps/support/messaging/sqs_queue.py` → `MessagingSQSQueue.enqueue()` → `queue_client.send_message(queue_name=..., message=...)`.
- **설정**: `MESSAGING_SQS_QUEUE_NAME` (기본 `academy-messaging-jobs`).

**점검**

- AWS credentials / IAM role (API 실행 주체에 `sqs:SendMessage` 등).
- Region / Queue URL 일치 여부.
- CloudWatch (academy-api 컨테이너) 로그에서:
  - `AccessDenied`, `InvalidClientTokenId`, `NonExistentQueue` 등.

---

## 3단계 — Worker 동작 확인

- **컨테이너**: `docker ps` → messaging-worker 컨테이너 살아 있는지.
- **로그**: `docker logs -f academy-messaging-worker` (또는 실제 컨테이너 이름).

**흔한 증상**

| 증상 | 원인 |
|------|------|
| 아무 로그 없음 | SQS polling 안 함 (설정/권한/큐 이름) |
| polling은 하는데 처리 안 함 | message body parsing 실패 (형식/필드) |
| 바로 self-stop | queue empty 오판 등 |
| ModuleNotFoundError | 이미지 빌드/의존성 불일치 |

**Worker 필수 ENV (실제 코드 기준)**

- `SOLAPI_API_KEY`, `SOLAPI_API_SECRET`
- `SOLAPI_SENDER` — 발신번호 (테넌트에 없을 때 fallback)
- `MESSAGING_SQS_QUEUE_NAME` (기본 `academy-messaging-jobs`)
- `AWS_REGION` (SQS 리전)
- `DJANGO_SETTINGS_MODULE` (테넌트 잔액/발신번호 조회 시 필요)

---

## 4단계 — Solapi 호출 문제

- Worker가 **발신번호** 결정:  
  **1) SQS payload `sender` → 2) Tenant.messaging_sender (get_tenant_messaging_info) → 3) cfg.SOLAPI_SENDER**
- **SendMessageView는 `sender`를 넘기지 않음** → 워커는 **Tenant.messaging_sender** 또는 **SOLAPI_SENDER** 사용.

**의심 1: Tenant.messaging_sender 비어 있음**

- `messaging_sender=''` 이면 Solapi에서 막히거나, 워커에서 `sender_required` 로 실패할 수 있음.

**즉시 확인**

```python
from apps.core.models import Tenant
t = Tenant.objects.get(code="hakwonplus")
print(t.messaging_sender)   # 비어 있으면 세팅 필요
t.messaging_sender = "01012345678"  # 솔라피 등록 번호
t.save()
```

**의심 2: Solapi 에러**

- 401 Unauthorized, invalid api key, sender number not verified 등 → Solapi 콘솔/키/발신번호 등록 확인.

---

## 진단 시 이 3가지 수집

1. **POST /api/v1/messaging/send/ 응답 JSON** (상태코드 + body)
2. **messaging-worker docker logs** (한 번 발송 시도 직후 구간)
3. **Tenant.messaging_sender 현재 값** (해당 테넌트)

```python
from apps.core.models import Tenant
for t in Tenant.objects.filter(code="hakwonplus").values("id", "code", "messaging_sender"):
    print(t)
```

이 3개가 있으면 90% 이상 원인 구간을 좁힐 수 있다.

---

## 요약 (코드 기준)

- **API**: send 시 NotificationLog 생성 안 함. 200 + `enqueued` 만 반환.
- **발신번호**: API에서 sender 미전달 → 워커가 `get_tenant_messaging_info(tenant_id)["sender"]` → `Tenant.messaging_sender` 사용. 비어 있으면 `SOLAPI_SENDER` 사용.
- **NotificationLog**: 워커가 Solapi 호출 후(성공/실패) `create_notification_log()` 로만 생성.
- **발신번호 비어 있을 때**: SendMessageView에서 `Tenant.messaging_sender` 가 비어 있으면 **400** 반환 (조용히 실패 방지). 설정 > 내 정보에서 등록·인증 후 저장 필요.
