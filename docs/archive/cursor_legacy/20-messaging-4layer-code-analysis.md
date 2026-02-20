# Messaging 4계층 코드 분석 (SSOT — 코드 근거만)

**분석 기준**: 실제 파일·라인 번호·코드 인용. 추측·문서 기준 없음.

---

## [1] SendMessageView

### 1.1 파일 경로

- `apps/support/messaging/views.py`

### 1.2 해당 코드 (라인 번호)

```python
# L286–294: 클래스 정의
class SendMessageView(APIView):
    """
    POST: 선택 학생(들)에게 메시지 발송 (SQS enqueue → 워커가 Solapi 발송).
    ...
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
```

```python
# L308–316: sender 검증 (발신번호 비어 있으면 400)
        # 발신번호 없으면 워커에서 sender_required 로 조용히 실패함 → API에서 즉시 400
        sender = (tenant.messaging_sender or "").strip()
        if not sender:
            return Response(
                {
                    "detail": "발신번호가 등록되지 않았습니다. 설정 > 내 정보에서 발신번호를 등록·인증한 뒤 저장해 주세요.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
```

```python
# L383–391: enqueue 호출 위치 (sender 인자 없음)
            ok = enqueue_sms(
                tenant_id=tenant.id,
                to=phone,
                text=text,
                message_mode=message_mode,
                template_id=template_id_solapi,
                alimtalk_replacements=alimtalk_replacements,
            )
```

### 1.3 credit_balance / messaging_is_active 체크 여부

- **검색 결과**: `views.py` 내 `credit_balance`, `messaging_is_active`, `credit_balance`, `is_active` 문자열 없음.
- **결론**: SendMessageView에서는 **credit_balance**, **messaging_is_active** 를 전혀 참조하지 않음. 이 뷰에서 잔액/활성화로 막는 로직 없음.

### 1.4 현재 문제에 미치는 영향

- **sender**: L308–316에서 `tenant.messaging_sender` 가 비어 있으면 **즉시 400** 반환. 여기서 통과하면 API 단에서는 발신번호로 막히지 않음.
- **enqueue_sms**: L383–390에서 **sender 인자를 넘기지 않음**. 따라서 SQS payload의 `sender`는 항상 None(또는 enqueue 내부 기본값). 발신번호는 전적으로 워커 측에서 결정됨.

### 1.5 이 계층에서 메시지가 막힐 수 있는 정확한 조건

| 조건 | 코드 위치 | 결과 |
|------|-----------|------|
| `tenant.messaging_sender` 가 비어 있음 | L309–316 | 400, enqueue 호출 안 함 |
| `students` 비어 있음 (해당 ID 없음/삭제됨) | L316–320 | 400 |
| `message_mode in ("alimtalk","both")` 인데 템플릿 미승인/없음 | L346–351 | 400 |
| `body_base` 비어 있음 | L353–357 | 400 |
| 수신자별로 `phone` 없거나 10자 미만 | L368–370 | 해당 건만 스킵, `enqueued` 에 미포함 |

---

## [2] MessagingSQSQueue.enqueue()

### 2.1 파일 경로

- `apps/support/messaging/sqs_queue.py`

### 2.2 SQS 호출 코드

```python
# L41–44: 큐 이름 결정
    def _get_queue_name(self) -> str:
        return getattr(settings, "MESSAGING_SQS_QUEUE_NAME", self.QUEUE_NAME)
# L35: 기본 상수
    QUEUE_NAME = "academy-messaging-jobs"
```

```python
# L99–108: 실제 SQS 전송
        if not message["to"] or not message["text"]:
            logger.warning("enqueue skipped: to or text empty")
            return False
        try:
            ok = self.queue_client.send_message(
                queue_name=self._get_queue_name(),
                message=message,
            )
```

- **queue_name**: `settings.MESSAGING_SQS_QUEUE_NAME` 이 없으면 `"academy-messaging-jobs"` (L35, L42).

### 2.3 queue_client 출처 및 region

- **대입**: L39 `self.queue_client = get_queue_client()`  
- **정의**: `libs/queue/client.py` L173–177:

```python
def get_queue_client() -> QueueClient:
    return SQSQueueClient()
```

- **SQS 클라이언트 생성**: `libs/queue/client.py` L84–88:

```python
    def __init__(self, region_name: Optional[str] = None):
        try:
            import boto3
            self.region_name = region_name or os.getenv("AWS_REGION", "ap-northeast-2")
            self.sqs = boto3.client("sqs", region_name=self.region_name)
```

- **region**: `region_name` 인자가 없으면 **환경변수 `AWS_REGION`**, 없으면 **`"ap-northeast-2"`**. settings는 사용하지 않음.

### 2.4 message에 포함되는 sender

- L84: `"sender": (sender or "").strip() or None`  
- SendMessageView는 `enqueue_sms(..., sender=...)` 를 호출하지 않음 → `services.enqueue_sms` 의 `sender` 기본값 `None` → `queue.enqueue(..., sender=None)` → message의 `"sender"` 는 **None**.

### 2.5 현재 문제에 미치는 영향

- SQS 전송 실패 시 L106–108에서 `logger.exception` 후 **False** 반환. API는 이건 “해당 건 enqueue 실패”로만 처리하고 200 + `enqueued` 건수로 응답(이미 enqueue된 건은 그대로 SQS에 있음).
- **메시지가 막힐 수 있는 조건**: `queue_client.send_message` 가 예외를 일으키는 경우(자격증명 오류, 큐 없음, 권한 부족 등). 그 시점에 로그만 남고 해당 건만 enqueue 실패.

---

## [3] apps.worker.messaging_worker.sqs_main

### 3.1 파일 경로

- `apps/worker/messaging_worker/sqs_main.py`

### 3.2 Polling 루프 구조

```python
# L169–184: 메인 루프 진입 및 receive
    try:
        while not _shutdown:
            try:
                try:
                    raw = queue_client.receive_message(
                        queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                        wait_time_seconds=cfg.SQS_WAIT_TIME_SECONDS,
                    )
                except QueueUnavailableError as e:
                    logger.warning(...)
                    time.sleep(60)
                    continue
                if not raw:
                    continue
```

- **queue_name**: `cfg.MESSAGING_SQS_QUEUE_NAME` → `config.load_config()` → 환경변수 `MESSAGING_SQS_QUEUE_NAME` 없으면 `"academy-messaging-jobs"` (config.py L40).
- **wait_time_seconds**: `cfg.SQS_WAIT_TIME_SECONDS` → 환경변수 `MESSAGING_SQS_WAIT_SECONDS` 없으면 20 (config.py L42).

### 3.3 credit 검사 위치

```python
# L289–318: 잔액 검증 및 차감
                    # 잔액 검증 및 차감 (Django + info 있을 때, 단가 > 0)
                    deducted = False
                    try:
                        if info and float(base_price) > 0 and tenant_id is not None:
                            ...
                            bal = info.get("credit_balance", "0")
                            if float(bal) < float(base_price):
                                logger.warning(
                                    "tenant_id=%s insufficient_balance balance=%s base_price=%s, skip send",
                                    tenant_id, bal, base_price,
                                )
                                create_notification_log(
                                    tenant_id=int(tenant_id),
                                    success=False,
                                    amount_deducted=Decimal("0"),
                                    recipient_summary=to[:4] + "****",
                                    failure_reason="insufficient_balance",
                                )
                                queue_client.delete_message(...)
                                ...
                                continue
                            deduct_credits(int(tenant_id), base_price)
                            deducted = True
```

- **조건**: `info` 존재, `float(base_price) > 0`, `tenant_id is not None`.
- **검사**: `float(bal) < float(base_price)` 이면 **NotificationLog 실패 기록** + 메시지 삭제 + **continue**(Solapi 호출 안 함).
- **base_price == 0** 이면 이 블록 전체가 실행되지 않아 **잔액 검사/차감 없이** 바로 Solapi 호출로 진행됨.

### 3.4 sender 결정 로직

```python
# L251: payload에서 sender
                    sender = (data.get("sender") or "").strip()
# L260–278: Django 있을 때 테넌트 정보 조회
                    if tenant_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE"):
                        try:
                            ...
                            info = get_tenant_messaging_info(int(tenant_id))
                            if info:
                                ...
                                if not sender and info.get("sender"):
                                    sender = (info["sender"] or "").strip()
                        except Exception as e:
                            logger.warning("get_tenant_messaging_info failed: %s", e)

                    # L281–282: 최종 fallback
                    sender = (sender or "").strip() or cfg.SOLAPI_SENDER
```

- **우선순위**: (1) SQS payload `data.sender` → (2) `info["sender"]` (get_tenant_messaging_info → Tenant.messaging_sender) → (3) `cfg.SOLAPI_SENDER` (환경변수).
- SendMessageView가 sender를 넘기지 않으므로 payload.sender는 빈 문자열/None → 워커는 **Tenant.messaging_sender** 또는 **SOLAPI_SENDER** 사용.

### 3.5 messaging_is_active 사용 여부

- **검색**: `sqs_main.py` 내 `is_active`, `messaging_is_active` **0건**.
- **결론**: 워커는 **messaging_is_active 를 전혀 사용하지 않음**. 이 값으로 막는 구간 없음.

### 3.6 실패 시 NotificationLog 생성

- **잔액 부족**: L301–307에서 `create_notification_log(..., failure_reason="insufficient_balance")` 호출.
- **Solapi 실패(성공 아님)**: L356–384에서 `result.get("status") != "ok"` 이면 L368–374에서 `create_notification_log(..., failure_reason=result.get("reason", "send_failed")[:500])` 호출. 차감했으면 `rollback_credits` 호출 (L366–367).

### 3.7 이 계층에서 메시지가 막힐 수 있는 정확한 조건

| 조건 | 코드 위치 | 결과 |
|------|-----------|------|
| `DJANGO_SETTINGS_MODULE` 미설정 | L263 등 | `get_tenant_messaging_info` 호출 안 함 → info=None → base_price="0" → 잔액 검사 스킵. sender는 payload 또는 cfg.SOLAPI_SENDER만 사용. |
| `info` 없음 (테넌트 없음) | L292 | 잔액 검사 블록 미진입. sender는 L277–278에서만 채워짐(없으면 cfg.SOLAPI_SENDER). |
| `info` 있고 `float(base_price) > 0` 이고 `float(bal) < float(base_price)` | L296–314 | NotificationLog(insufficient_balance) 생성, 메시지 삭제, continue → **발송 안 함**. |
| `sender` 최종이 빈 문자열 (payload·info·cfg.SOLAPI_SENDER 모두 없음) | L283 이후 send_one_sms/send_one_alimtalk 내부 | send_one_sms L105–107에서 `{"status": "error", "reason": "sender_required"}` 반환 → L356–384에서 실패 로그 + 롤백. |

---

## [4] Solapi 호출 코드

### 4.1 실제 호출 함수 경로

- **SMS**: `apps/worker/messaging_worker/sqs_main.py` 내 함수 `send_one_sms` (L94–141).
- **알림톡**: 동일 파일 `send_one_alimtalk` (L50–91).

### 4.2 클라이언트 생성 (실제 vs Mock)

```python
# L41–47: sqs_main.py
def _get_solapi_client(cfg):
    """DEBUG=True 또는 SOLAPI_MOCK=true 이면 Mock (로그만), 아니면 실제 Solapi."""
    if os.environ.get("SOLAPI_MOCK", "").lower() in ("true", "1", "yes") or os.environ.get("DEBUG", "").lower() in ("true", "1", "yes"):
        from apps.support.messaging.solapi_mock import MockSolapiMessageService
        return MockSolapiMessageService(api_key=cfg.SOLAPI_API_KEY, api_secret=cfg.SOLAPI_API_SECRET)
    from solapi import SolapiMessageService
    return SolapiMessageService(api_key=cfg.SOLAPI_API_KEY, api_secret=cfg.SOLAPI_API_SECRET)
```

- **실제 HTTP 호출**: `solapi` 패키지의 `SolapiMessageService` 인스턴스의 `send()` (프로젝트 내부면 `supporting/solapi-python-main`, 아니면 설치된 패키지).

### 4.3 send_one_sms 예외 처리 구조

```python
# L104–107: sender 없으면 API 호출 전 반환
    client = _get_solapi_client(cfg)
    sender = (sender or cfg.SOLAPI_SENDER or "").strip()
    if not sender:
        return {"status": "error", "reason": "sender_required"}
# L114–141: 실제 전송 및 예외 처리
    try:
        message = RequestMessage(from_=sender, to=to, text=text)
        response = client.send(message)
        ...
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        reason = str(e)[:500]
        try:
            from solapi.error.MessageNotReceiveError import MessageNotReceivedError
            if isinstance(e, MessageNotReceivedError) and getattr(e, "failed_messages", None):
                # ... status_code, status_message 조합
                ...
        except Exception:
            logger.exception("send_sms failed to=%s****", to[:4])
        return {"status": "error", "reason": reason}
```

- **401 / 인증 오류**: `client.send(message)` 에서 예외 발생 시 **모두 `Exception`** 으로 잡혀 `reason = str(e)[:500]` 후 `{"status": "error", "reason": reason}` 반환. 호출부(sqs_main)에서는 이걸 “실패”로만 처리하고, **401인지 sender 미등록인지 구분하는 분기 없음**.
- **sender 미등록(솔라피 쪽 에러)**: 마찬가지로 Solapi SDK에서 예외로 올라오면 동일하게 `reason`에 메시지 들어가고 `{"status": "error", "reason": ...}` 반환.

### 4.4 send_one_alimtalk 예외 처리

```python
# L70–90
    try:
        ...
        response = client.send(message)
        ...
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        logger.warning("alimtalk failed to=%s****: %s", to[:4], e)
        return {"status": "error", "reason": str(e)[:500]}
```

- 401/미등록 등 모든 예외가 동일하게 `{"status": "error", "reason": str(e)[:500]}` 로 반환됨.

### 4.5 401 / sender 미등록 시 동작 (현재 구조)

1. **send_one_sms** 또는 **send_one_alimtalk** 내부에서 `client.send(message)` 가 예외를 발생시킴 (401, sender 미등록 등).
2. 해당 예외는 `except Exception as e` 에 걸려 `{"status": "error", "reason": str(e)[:500]}` 반환.
3. **sqs_main** L356–384: `result.get("status") != "ok"` 이므로 `create_notification_log(..., failure_reason=result.get("reason", "send_failed")[:500])` 실행, 차감했으면 `rollback_credits` 호출.
4. L390–394: 메시지는 **삭제하지 않음** (`delete_message` 호출 안 함). 따라서 **SQS 메시지는 다시 visible 되어 재시도**됨.
5. L392–394: `consecutive_errors` 가 `max_consecutive_errors`(10) 이상이면 워커가 exit(1)로 종료.

### 4.6 이 계층에서 메시지가 막힐 수 있는 정확한 조건

| 조건 | 코드 위치 | 결과 |
|------|-----------|------|
| `sender` 빈 문자열 (payload·테넌트·SOLAPI_SENDER 모두 없음) | send_one_sms L105–107 | API 호출 전에 `{"status": "error", "reason": "sender_required"}` 반환. |
| Solapi 401 (키/시크릿 오류) | client.send() → Exception | reason에 예외 메시지, NotificationLog 실패 기록, 메시지 삭제 안 함 → 재시도. |
| Solapi sender 미등록 등 4xx/5xx | 동일 | 동일. |
| SDK/네트워크 예외 | 동일 | 동일. |

---

## 요약: 현재 구조에서 메시지가 막힐 수 있는 정확한 조건 (코드 기준)

1. **API [1]**  
   - `Tenant.messaging_sender` 비어 있음 → 400, enqueue 자체가 안 함.  
   - 그 외 위 1.5 표의 조건에서 400 또는 해당 건 스킵.

2. **SQS [2]**  
   - `queue_client.send_message` 예외(자격증명, 큐 없음, 권한 등) → 해당 건만 enqueue 실패(False), 로그만 남음.

3. **워커 [3]**  
   - `info` 존재하고 `base_price > 0` 이고 `credit_balance < base_price` → insufficient_balance 로그 후 continue(발송 안 함).  
   - `base_price == 0` 이면 잔액 검사 없이 Solapi 호출로 진행.  
   - **messaging_is_active** 는 어디에서도 사용하지 않음 → 이 값으로는 막히지 않음.

4. **Solapi [4]**  
   - sender 최종이 비어 있음 → `sender_required` 로 실패 로그, 롤백, 메시지 재시도.  
   - 401/미등록/기타 예외 → 모두 `Exception` 으로 잡혀 failure_reason에 메시지 저장, 롤백, 메시지 재시도.
