# Messaging Worker · 메시지 발송 흐름 (SSOT)

**기준**: `apps/support/messaging/`, `apps/worker/messaging_worker/`.  
Base path: `/api/v1/messaging/`.

---

## 1. 개요

- **SQS** `academy-messaging-jobs` 수신 → **Solapi** SMS/알림톡 발송
- **message_mode**: `sms` | `alimtalk` | `both` (3가지 발송 방식)
- **발신번호 우선순위**: payload.sender → Tenant.messaging_sender → SOLAPI_SENDER(env)
- **자동발송**: 트리거별 템플릿 설정 (가입 완료, 클리닉 알림 등)

---

## 2. API 엔드포인트 (실제 코드 기준)

| Method | Path | View | 비고 |
|--------|------|------|------|
| GET | `/api/v1/messaging/info/` | MessagingInfoView | 잔액, PFID, messaging_sender |
| PATCH | `/api/v1/messaging/info/` | MessagingInfoView | kakao_pfid, messaging_sender |
| POST | `/api/v1/messaging/verify-sender/` | VerifySenderView | phone_number |
| POST | `/api/v1/messaging/charge/` | ChargeView | amount |
| GET | `/api/v1/messaging/log/` | NotificationLogListView | page, page_size |
| GET | `/api/v1/messaging/channel-check/` | ChannelCheckView | 스텁 |
| POST | `/api/v1/messaging/send/` | SendMessageView | student_ids, send_to, message_mode, template_id, raw_body, raw_subject |
| GET | `/api/v1/messaging/templates/` | MessageTemplateListCreateView | category 쿼리 |
| POST | `/api/v1/messaging/templates/` | MessageTemplateListCreateView | category, name, body, subject |
| GET | `/api/v1/messaging/templates/<pk>/` | MessageTemplateDetailView | |
| PATCH | `/api/v1/messaging/templates/<pk>/` | MessageTemplateDetailView | |
| DELETE | `/api/v1/messaging/templates/<pk>/` | MessageTemplateDetailView | |
| POST | `/api/v1/messaging/templates/<pk>/submit-review/` | MessageTemplateSubmitReviewView | 알림톡 검수 신청 |
| GET | `/api/v1/messaging/auto-send/` | AutoSendConfigView | 자동발송 설정 목록 |
| PATCH | `/api/v1/messaging/auto-send/` | AutoSendConfigView | configs: [{ trigger, template_id, enabled, message_mode }] |

---

## 3. message_mode

| 값 | 동작 |
|----|------|
| `sms` | SMS만 발송. pf_id/template_id 불필요. |
| `alimtalk` | 알림톡만. 실패 시 폴백 없음. pf_id·template_id 필수. |
| `both` | 알림톡 우선, 실패 시 SMS 폴백. |

- `use_alimtalk_first` (하위호환): True → both, False → sms

---

## 4. Worker 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| SOLAPI_API_KEY | ✅ | Solapi API 키 |
| SOLAPI_API_SECRET | ✅ | Solapi API 시크릿 |
| SOLAPI_SENDER | ✅ | 기본 발신 번호 |
| SOLAPI_KAKAO_PF_ID | - | 카카오 비즈니스 채널 ID |
| SOLAPI_KAKAO_TEMPLATE_ID | - | 기본 알림톡 템플릿 ID |
| MESSAGING_SQS_QUEUE_NAME | - | academy-messaging-jobs |
| MESSAGING_SQS_WAIT_SECONDS | - | 20 |
| AWS_REGION | - | ap-northeast-2 |
| DJANGO_SETTINGS_MODULE | - | 예약 취소 Double Check 시 |

---

## 5. SQS 메시지 형식

```json
{
  "tenant_id": 1,
  "to": "01012345678",
  "text": "본문",
  "sender": null,
  "message_mode": "sms",
  "reservation_id": null,
  "alimtalk_replacements": [{"key": "name", "value": "홍길동"}],
  "template_id": "solapi_template_id_string",
  "created_at": "2026-02-17T..."
}
```

---

## 6. 자동발송 (AutoSendConfig)

**트리거**: student_signup, clinic_reminder, clinic_reservation_created, clinic_reservation_changed

**연동**:
- `send_welcome_messages()`: student_signup 설정 있으면 enqueue_sms로 실제 발송
- `send_clinic_reminder_for_students()`: 현재 스텁 (추후 clinic_reminder 연동)

**가입 완료 템플릿 변수**: #{student_name_2}, #{student_name_3}, #{site_link}, #{student_id}, #{student_password}, #{parent_password}, #{parent_id}

---

## 7. 실행

```bash
pip install -r requirements/worker-messaging.txt
export DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod
python -m apps.worker.messaging_worker.sqs_main
```

---

## 8. 관련 문서

- `apps/worker/messaging_worker/README.md`: 워커 상세
- `docs_cursor/14-solapi-check-guide.md`: 발신번호·잔액 확인
