# Messaging Worker (SQS + Solapi)

SQS `academy-messaging-jobs` 수신 → message_mode에 따라 SMS만/알림톡만/알림톡→SMS폴백. 예약 취소 시 발송 직전 Double Check로 스킵.

---

## 1. 인프라 (비용 절감)

- **인스턴스**: **t3.nano**(또는 가장 싼 스펙). CPU보다 네트워크 I/O만 쓰므로 고사양 불필요.
- **반드시 스팟 인스턴스(Spot Instance)** 로 구성 → 일반 온디맨드 대비 **70% 이상 절감**.

---

## 2. 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `SOLAPI_API_KEY` | ✅ | Solapi API 키 |
| `SOLAPI_API_SECRET` | ✅ | Solapi API 시크릿 |
| `SOLAPI_SENDER` | ✅ | 발신 번호 (예: 01012345678) |
| `SOLAPI_KAKAO_PF_ID` | - | 카카오 비즈니스 채널 ID (알림톡 사용 시) |
| `SOLAPI_KAKAO_TEMPLATE_ID` | - | 카카오 검수 완료 템플릿 ID. **ENV로 관리해 코드 수정 없이 교체** |
| `MESSAGING_SQS_QUEUE_NAME` | - | 기본값 `academy-messaging-jobs` |
| `AWS_REGION` | - | 기본값 `ap-northeast-2` |
| `MESSAGING_SQS_WAIT_SECONDS` | - | Long Polling 대기(기본 20) |
| `DJANGO_SETTINGS_MODULE` | - | 예약 취소 Double Check 시 설정 |

---

## 3. SQS 운용 전략

- **Visibility Timeout**: 솔라피 타임아웃(약 5~10초)보다 넉넉히 **30~60초** (기본 60). 짧으면 중복 발송 대참사.
- **Dead Letter Queue (DLQ)**: **3번 재시도** 후 실패 메시지는 DLQ로 격리 → "왜 문자 안 왔냐" 민원 확인용 로그.
- **Long Polling**: `WaitTimeSeconds` **20초** → SQS 빈 쿼리 비용 최소화.

---

## 4. message_mode: SMS / 알림톡 / 둘 다

| message_mode | 동작 |
|--------------|------|
| `sms` | SMS만 발송. pf_id/template_id 불필요. |
| `alimtalk` | 알림톡만 발송. 실패 시 폴백 없음. pf_id·template_id 필수. |
| `both` | 알림톡 우선 시도, 실패 시 SMS 폴백. |

- `use_alimtalk_first=True` (하위호환) → `both`, `False` → `sms`
- **템플릿 관리**: 카카오 검수 끝난 **템플릿 ID**를 ENV(`SOLAPI_KAKAO_TEMPLATE_ID`) 또는 payload `template_id`로 전달.

---

## 5. 예약 취소 대응 (Revoke + Double Check)

- 사용자가 예약을 취소하면, 해당 예약의 발송은 **SQS에 이미 들어간 뒤**일 수 있음.
- **발송 로직 입구에서 Double Check 필수**: `reservation_id`가 있으면 `is_reservation_cancelled(reservation_id)` 호출. **취소 상태면 발송 스킵**하고 메시지 삭제.
- 워커 실행 시 `DJANGO_SETTINGS_MODULE` 설정 시 `django.setup()` 호출 → `apps.support.messaging.services.is_reservation_cancelled()` 에서 `Reservation` 모델의 `status == 'CANCELLED'` 여부 조회.

---

## 6. 실행

```bash
pip install -r requirements/worker-messaging.txt
python scripts/create_sqs_resources.py ap-northeast-2

# 예약 취소 체크 필요 시
export DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod
python -m apps.worker.messaging_worker.sqs_main
```

---

## 7. API/서비스에서 발송 요청

- **비동기 (권장)**  
  `enqueue_sms(tenant_id=request.tenant.id, to="01012345678", text="내용", reservation_id=123, use_alimtalk_first=True)`
  → SQS 적재, 워커가 tenant 잔액 검증·차감 후 알림톡(테넌트 PFID) 우선 → 실패 시 롤백·SMS, 예약 취소 시 스킵.
- **동기**  
  `send_sms(to="01012345678", text="내용")` (API 서버에 solapi 설치 필요).

API 키/시크릿은 **환경변수**로만 설정하고 코드에 노출하지 마세요.

---

## 8. 발송 ID 저장 (민원 대응)

Solapi 응답의 **group_id**(및 messageId)를 발송 로그/예약 테이블에 저장해 두면, "문자 안 왔어요" 민원 시 Solapi 콘솔에서 해당 ID로 조회해 원인 파악이 가능합니다.

---

## 9. Fake Solapi (DEBUG / 테스트)

- **DEBUG=True** 또는 **SOLAPI_MOCK=true** 이면 실제 API를 호출하지 않고, 발송될 JSON만 콘솔에 예쁘게 로그합니다.
- 잔액 차감·템플릿 미승인 에러를 피하려면 개발/스트레스 테스트 시 이 모드로 실행하세요.
- `apps.support.messaging.solapi_mock.MockSolapiMessageService` 사용.

## 10. 스트레스 테스트

```bash
# 100건 enqueue (워커는 별도 터미널에서 DEBUG=True 로 실행)
python scripts/stress_test_messaging_worker.py

# 100건 enqueue 후 워커 자동 실행, 큐가 비워질 때까지 대기
python scripts/stress_test_messaging_worker.py --run-worker
```

## 11. 알림톡 템플릿 변수 (미리 확정)

- `apps.support.messaging.alimtalk_templates` 에 템플릿 변수명 상수 및 치환 헬퍼 정의.
- **변수명**: `name`, `date`, `time`, `clinic_name`, `place`, `link`, `title` (DB 필드와 매칭).
- 솔라피/카카오 콘솔에 등록할 템플릿 문구 예: `#{name}님, #{date} #{time} #{clinic_name} 예약이 완료되었습니다.`
- 치환 데이터 생성: `build_replacements(context)` / `template_context_from_reservation(reservation)` 사용.
