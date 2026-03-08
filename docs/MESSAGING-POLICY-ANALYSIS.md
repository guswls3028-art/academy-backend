# 메시징 정책 — 현재 구조 분석 및 반영 설계

## 1. 현재 구조 분석

### 1.1 Solapi 연동 위치

| 위치 | 용도 |
|------|------|
| `apps/support/messaging/services.py` | `get_solapi_client()`, `send_sms()` (동기 발송), `enqueue_sms()` (SQS 인큐) |
| `apps/support/messaging/solapi_sender_client.py` | 발신번호 검증 `verify_sender_number()` |
| `apps/support/messaging/solapi_template_client.py` | 알림톡 템플릿 검수 신청 `create_kakao_template()`, `validate_template_variables()` |
| `apps/support/messaging/solapi_mock.py` | MockSolapiMessageService (DEBUG/SOLAPI_MOCK 시) |
| `apps/worker/messaging_worker/sqs_main.py` | SQS 소비 → `send_one_sms()`, `send_one_alimtalk()` 호출 |
| `apps/worker/messaging_worker/config.py` | SOLAPI_*, SOLAPI_KAKAO_PF_ID, SOLAPI_KAKAO_TEMPLATE_ID 등 ENV 로드 |
| `apps/api/config/settings/base.py` | SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_SENDER, SOLAPI_KAKAO_PF_ID, SOLAPI_KAKAO_TEMPLATE_ID |

### 1.2 SMS/알림톡 발송 진입점

| 진입점 | 방식 | tenant 정보 |
|--------|------|-------------|
| `apps/domains/students/views.py` (비밀번호 찾기 인증번호) | `send_sms(phone, text)` 동기 호출 | `request.tenant` 있으나 send_sms에 미전달 |
| `apps/support/messaging/services.py` | `send_welcome_messages()`, `send_registration_approved_messages()` 내부에서 `enqueue_sms(tenant_id=..., ...)` | tenant_id 전달 |
| `apps/support/messaging/views.py` (SendMessageView) | `enqueue_sms(tenant_id=tenant.id, ...)` | request.tenant.id |
| 워커 `sqs_main.py` | SQS 메시지의 `tenant_id`로 `get_tenant_messaging_info(tenant_id)` 조회 후 발송 | 메시지 body의 tenant_id |

### 1.3 Tenant별 발신 채널/설정 (기존 모델·설정)

**Tenant 모델 (`apps/core/models/tenant.py`):**

- `kakao_pfid` — 학원 개별 카카오 프로필 ID (연동 시 저장)
- `messaging_sender` — 학원별 SMS 발신번호 (솔라피 등록 번호)
- `messaging_is_active` — 알림톡 기능 활성화 여부
- `credit_balance` — 선불 충전 잔액
- `messaging_base_price` — 건당 발송 단가

**전역 설정 (base.py / worker config):**

- `SOLAPI_SENDER` — 기본 발신번호
- `SOLAPI_KAKAO_PF_ID` — 기본(시스템) 알림톡 채널 PF ID
- `SOLAPI_KAKAO_TEMPLATE_ID` — 기본 알림톡 템플릿 ID

**채널 선택 로직 (현재):**

- 워커: `pf_id_tenant = (info["kakao_pfid"] or "").strip()` → `pf_id = pf_id_tenant or cfg.SOLAPI_KAKAO_PF_ID`
- 즉, tenant에 kakao_pfid가 있으면 사용, 없으면 시스템 기본값. “자체 채널 강제” 플래그는 없음.

### 1.4 미완성 상태

- **SMS tenant 제한 없음**: 어떤 tenant든 SMS/문자 발송 가능. “1번 tenant만 허용” 정책 미구현.
- **채널 선택 분산**: 워커 내부에서만 `pf_id_tenant or cfg.SOLAPI_KAKAO_PF_ID` 처리. API/서비스 레이어에는 동일 정책을 적용하는 공통 resolver 없음.
- **정책 상수 부재**: “1번 = 내 테넌트” 등 매직넘버가 없고, OWNER_TENANT_ID 같은 설정/상수 없음.
- **자체 채널 강제 여부**: tenant에 “자체 채널만 사용” 플래그가 없어, 현재는 **미설정 시 전부 기본 채널 fallback** 상태. “자체 채널 강제 + 필수값 비어있음” 시 에러 vs fallback 선택은 **현재 코드상 해당 케이스가 없음** → 유지 시 **fallback**이 맞고, 추후 “자체 채널 강제” 플래그를 넣을 때만 정책 에러 검토 가능.

---

## 2. 반영 설계

### 2.1 정책 상수

- `OWNER_TENANT_ID`: SMS 허용 tenant (내 테넌트).  
  - `apps/api/config/settings/base.py`: `OWNER_TENANT_ID = int(os.getenv("OWNER_TENANT_ID", "1"))`  
  - `apps/worker/messaging_worker/config.py`: 동일한 ENV `OWNER_TENANT_ID` (기본 1) 추가.

### 2.2 중앙 resolver / 정책 모듈

- **파일**: `apps/support/messaging/policy.py` (신규)
- **내용**:
  - `OWNER_TENANT_ID`: settings에서 읽어 재노출 (테스트/의존성 주입 가능하게).
  - `can_send_sms(tenant_id: int) -> bool`: `tenant_id == OWNER_TENANT_ID`.
  - `resolve_kakao_channel(tenant_id: int) -> dict`:  
    - `get_tenant_messaging_info(tenant_id)` 로 tenant의 `kakao_pfid` 조회.  
    - `pf_id = (tenant kakao_pfid).strip() or settings.SOLAPI_KAKAO_PF_ID`  
    - 반환: `{"pf_id": str, "use_default": bool}` (use_default = tenant kakao_pfid가 비어 있을 때 True).
  - `MessagingPolicyError` 예외: 정책 위반 시 사용 (예: SMS 비허용 tenant).

### 2.3 SMS 정책 적용

- **enqueue_sms (services.py)**  
  - `message_mode in ("sms", "both")` 이면 `can_send_sms(tenant_id)` 확인.  
  - False면 로그 남기고 `MessagingPolicyError` 발생 → 호출부(SendMessageView, send_welcome_messages 등)에서 처리.

- **send_sms (services.py)**  
  - 인자에 `tenant_id: Optional[int] = None` 추가.  
  - `tenant_id`가 주어졌을 때 `not can_send_sms(tenant_id)` 이면 로그 후 `{"status": "error", "reason": "sms_allowed_only_for_owner_tenant"}` 반환.

- **비밀번호 찾기 (students/views.py)**  
  - `send_sms(phone, text, tenant_id=tenant.id)` 로 호출.  
  - 반환값이 error면 403 + 메시지 반환.

- **워커 (sqs_main.py)**  
  - 실제로 **SMS를 보내는 직전** (message_mode == "sms" 인 경로, 및 message_mode == "both" 인 경로에서 알림톡 실패 후 SMS fallback 시) `tenant_id != OWNER_TENANT_ID` 이면:  
    - 로그에 “SMS not allowed for tenant” 등 명시.  
    - 발송하지 않고, 실패 로그(예: failure_reason="sms_not_allowed_for_tenant") 기록 후 메시지 삭제하고 다음 메시지로 진행.

### 2.4 알림톡 채널 선택 통일

- **워커**  
  - Django 로드 시: `resolve_kakao_channel(tenant_id)` 사용해 `pf_id` 결정.  
  - 미로드 시: 기존처럼 `info["kakao_pfid"] or cfg.SOLAPI_KAKAO_PF_ID` 유지.
- **데이터 구조**  
  - 기존 Tenant 필드만 사용. “자체 채널 사용 여부”는 `kakao_pfid` 비어 있으면 기본 채널, 있으면 자체 채널로 해석 (추가 필드 없음).  
  - 필요 시 나중에만 `use_own_channel_only` 같은 플래그 추가하여 “강제 + 비어있음 → 에러” 처리 가능.

### 2.5 안전장치 (자체 채널 불완전 시)

- **현재**: tenant `kakao_pfid`가 비어 있으면 이미 `pf_id_tenant or cfg.SOLAPI_KAKAO_PF_ID`로 fallback 중.
- **선택**: “자체 채널 강제”가 없으므로 **fallback 유지**. 추후 “자체 채널 강제” 플래그 도입 시에만, 그 플래그가 True이고 pfid 비어있을 때만 정책 에러로 처리하면 됨.

---

## 3. 수정 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `apps/support/messaging/policy.py` | 신규. OWNER_TENANT_ID, can_send_sms, resolve_kakao_channel, MessagingPolicyError |
| `apps/api/config/settings/base.py` | OWNER_TENANT_ID 추가 |
| `apps/worker/messaging_worker/config.py` | OWNER_TENANT_ID 추가 |
| `apps/support/messaging/services.py` | enqueue_sms 정책 검사 및 예외, send_sms에 tenant_id 및 정책 검사 |
| `apps/domains/students/views.py` | send_sms(..., tenant_id=tenant.id), 반환값 처리 및 403 |
| `apps/support/messaging/views.py` | enqueue_sms 호출 시 MessagingPolicyError 처리 → 403 |
| `apps/support/messaging/services.py` (send_welcome, send_registration_approved) | enqueue_sms 호출 전 can_send_sms 확인하여 SMS/both일 때만 검사, 실패 시 skip 로그 (또는 예외 처리 후 skip) |
| `apps/worker/messaging_worker/sqs_main.py` | SMS 발송 전 OWNER_TENANT_ID 검사, 알림톡 pf_id는 resolve_kakao_channel 사용 (Django 있을 때) |

---

## 4. 동작 예시

- **Tenant 1 (내 테넌트)**  
  - SMS: 허용. 동기 send_sms, enqueue_sms(sms/both) 모두 발송 가능.  
  - 알림톡: kakao_pfid 없으면 시스템 기본 채널, 있으면 해당 채널.

- **일반 tenant (2, 3, …)**  
  - SMS: enqueue_sms(sms/both) 호출 시 정책 에러(403). send_sms(tenant_id=2) 시 에러 반환. 워커에서도 SMS 경로 차단.  
  - 알림톡: 기존과 동일. tenant kakao_pfid 없으면 시스템 기본 채널.

- **자체 채널 연동 tenant**  
  - kakao_pfid 설정됨 → 알림톡 시 해당 채널 사용.  
  - SMS는 여전히 tenant_id != 1 이면 차단.

---

## 5. 남은 TODO

- (선택) 관리자 설정 화면에서 “기본 채널 사용 / 자체 채널 사용” 문구 반영 (기존 kakao_pfid 유무로 구분 가능).
- (선택) “자체 채널 강제” 플래그 및 해당 시 필수값 비어있으면 정책 에러 처리.
- (선택) OWNER_TENANT_ID를 SSOT 문서/파라미터에 명시.
