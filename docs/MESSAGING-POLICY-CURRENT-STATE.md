# 메시징 정책 현황 분석 (실제 코드·설정 기준)

작성일: 2025-03-08  
기준: academy / academyfront 실제 코드, 설정, 배포 스크립트만 참조.

---

## 8. 우선 확인한 핵심 질문 (실제 코드 기준 답변)

### 1) messaging_is_active는 현재 실제 차단 조건으로 사용되고 있는가?

**아니오.**

- **Tenant 모델**: `messaging_is_active` 필드 존재 (tenant.py L38, default=False).
- **사용처**:
  - `credit_services.get_tenant_messaging_info()`: 반환 dict에 `"is_active": t["messaging_is_active"]` 포함.
  - `MessagingInfoSerializer`: `is_active = serializers.BooleanField(source="messaging_is_active", read_only=True)` 로 GET /info/ 응답에 포함.
  - management command `set_tenant_messaging_credits`: 설정/복구용.
- **미사용**: `policy.can_send_sms()`, `enqueue_sms()`, `send_sms()`, `sqs_main.py` 어디에서도 `messaging_is_active`를 검사하지 않음.  
→ **저장·노출만 되고, 발송 허용/차단 판단에는 사용되지 않음.**

---

### 2) both는 현재 실제로 어떤 의미로 동작하는가?

**알림톡 우선 발송, 실패 시에만 SMS 폴백.**

- **정의**: `models.AutoSendConfig.message_mode` choices `("both", "알림톡→SMS폴백")`, serializers/views/worker README 동일 문구.
- **워커 동작** (sqs_main.py L446–463):
  - `message_mode == "both"` 이고 `pf_id`·`template_id` 있으면: `send_one_alimtalk()` 호출.
  - `result.get("status") != "ok"` 이면 "alimtalk failed, fallback to SMS" 로그 후, **tenant_id == OWNER_TENANT_ID 일 때만** `send_one_sms()` 호출.
  - owner가 아니면 SMS 폴백 없이 실패로 처리.
- **동시 발송 아님.** 알림톡 1차 시도 → 실패 시에만 SMS 1회.

---

### 3) 알림톡 채널 resolve는 진짜 중앙화되었는가?

**예. 단일 진입점 있음.**

- **중앙**: `apps/support/messaging/policy.py` 의 `resolve_kakao_channel(tenant_id)`.
  - 테스트 테넌트(9999) → `{"pf_id": "", "use_default": True}`.
  - 그 외: `get_tenant_messaging_info(tenant_id)` 로 `kakao_pfid` 조회, 있으면 해당 채널, 없으면 `settings.SOLAPI_KAKAO_PF_ID` 사용.
  - 반환: `{"pf_id": str, "use_default": bool}`.
- **워커**: Django 로드 시 `resolve_kakao_channel(int(tenant_id))` 호출 후 `pf_id_tenant = (channel.get("pf_id") or "").strip()` 사용 (sqs_main.py L339, 348).  
  Django 미로드 시에는 워커 내부에서 `info["kakao_pfid"]` 및 `cfg.SOLAPI_KAKAO_PF_ID` 사용 (동일 로직).
- **API/서비스**: 발송 시 채널을 직접 resolve하지 않음. enqueue만 하고 워커가 resolve.  
→ **채널 선택은 policy 한 곳에서 정의되고, 워커가 그 결과를 사용.**

---

### 4) 관리자용 앱에서 tenant 메시징 상태를 현재 어디까지 보여주고 있는가?

**현재 노출:**

- **GET /api/v1/messaging/info/** 응답: `kakao_pfid`, `messaging_sender`, `credit_balance`, `is_active`, `base_price`.
- **관리자 앱** (academyfront):
  - **MessageSettingsPage**: 카카오 PFID 입력/저장, "현재 연동된 PFID: xxx", 발신번호는 "설정 > 내 정보" 안내.
  - **MessageLinkPage**: PFID 연동 안내.
  - **DashboardPage / Header**: `credit_balance` 로 "알림톡" 잔액 표시.
  - **MessageSendPage / SendMessageModal**: 발송 유형 선택 (SMS만 / 알림톡만 / 알림톡→SMS 폴백), 템플릿 선택.

**미노출:**

- "기본 채널 사용 중" / "자체 채널 사용 중" 구분 없음 (kakao_pfid 유무로 계산 가능하지만 UI에 없음).
- "SMS는 내 테넌트(owner)에서만 가능" 정책 안내 없음.
- `is_active`(messaging_is_active) 는 API에 있으나 관리자 앱에서 별도 문구/뱃지로 표시하지 않음.

---

### 5) owner tenant가 아닌 곳에서 SMS 관련 버튼/액션/설정이 노출되고 있는가?

**예. 전 테넌트 동일 UI.**

- **SendMessageModal**: 모든 테넌트에 "SMS만", "알림톡→SMS 폴백" 라디오 노출. tenant별로 비활성화하지 않음.
- **MessageAutoSendPage**: 트리거별 "SMS만 / 알림톡만 / 알림톡→SMS 폴백" 선택 가능.
- **백엔드**: owner가 아닌 테넌트가 SMS 또는 both로 enqueue 시도 시 `enqueue_sms()` 내부에서 `can_send_sms(tenant_id)` 가 False → `MessagingPolicyError` 발생 → SendMessageView에서 403 + "문자(SMS) 발송은 내 테넌트에서만 가능합니다." 반환.  
→ **동작은 정책대로 막혀 있으나, UI는 owner가 아닌 테넌트에도 SMS 옵션이 그대로 보임.**

---

### 6) API와 worker가 OWNER_TENANT_ID / 기본 채널 설정을 같은 방식으로 쓰고 있는가?

**같은 ENV 이름·같은 기본값이지만, 배포 경로가 달라 불일치 가능성 있음.**

- **API**  
  - `apps/api/config/settings/base.py`: `OWNER_TENANT_ID = int(os.getenv("OWNER_TENANT_ID", "1"))`.  
  - SOLAPI: `os.getenv("SOLAPI_*", "")` (SOLAPI_KAKAO_PF_ID, SOLAPI_KAKAO_TEMPLATE_ID 등).
- **Worker**  
  - `apps/worker/messaging_worker/config.py`: `OWNER_TENANT_ID=int(os.environ.get("OWNER_TENANT_ID", "1"))`,  
    `SOLAPI_KAKAO_PF_ID=os.environ.get("SOLAPI_KAKAO_PF_ID", "").strip()` 등.
- **배포**  
  - `scripts/v1/update-workers-env-sqs.ps1`: API SSM에서 워커 SSM으로 **SOLAPI_*** 만 복사함.  
    **OWNER_TENANT_ID, TEST_TENANT_ID는 복사하지 않음.**  
  - 따라서 워커는 SSM에 OWNER_TENANT_ID가 없으면 **항상 기본값 1** 사용.  
  - API SSM에만 OWNER_TENANT_ID=2 등으로 넣어 두면 API는 2, 워커는 1로 동작할 수 있음.  
→ **코드상 해석 방식은 동일하나, 배포 스크립트가 워커에 OWNER_TENANT_ID/TEST_TENANT_ID를 넣지 않아 불일치 위험 있음.**

---

## 2. 사실 기반 현황 분석

### A. 현재 메시징 관련 실제 진입점

| 구분 | 진입점 | 호출 체인 | 판단 위치 |
|------|--------|-----------|-----------|
| 동기 SMS | `send_sms()` (services.py) | students/views 비밀번호 찾기 등 → send_sms(..., tenant_id=tenant.id) | send_sms 내부: is_messaging_disabled, can_send_sms (policy) |
| 비동기 enqueue | `enqueue_sms()` (services.py) | SendMessageView, send_welcome_messages, send_registration_approved_messages → enqueue_sms(tenant_id=..., message_mode=...) | enqueue_sms 내부: is_messaging_disabled, mode in ("sms","both") 시 can_send_sms |
| Worker 소비 | sqs_main.py main loop | SQS 수신 → tenant_id 추출 → TEST_TENANT_ID 스킵 → 예약 취소 체크 → get_tenant_messaging_info + resolve_kakao_channel → 잔액 차감 → message_mode별 발송 | sqs_main: tenant_id==TEST_TENANT_ID 스킵; message_mode=="sms" 또는 both 폴백 시 tenant_id==OWNER_TENANT_ID 검사; pf_id는 resolve_kakao_channel (Django 있을 때) |

- **SMS 허용 여부**: policy.can_send_sms(tenant_id) (API/서비스), cfg.OWNER_TENANT_ID (워커).  
- **알림톡 채널**: policy.resolve_kakao_channel(tenant_id) (워커에서 Django 로드 시 사용).

---

### B. 현재 tenant 메시징 관련 실제 필드와 의미

| 필드 | 실제 사용 여부 | 정책/판단 사용 | 관리자 앱 노출 |
|------|----------------|----------------|----------------|
| kakao_pfid | 사용 | resolve_kakao_channel에서 "자체 채널" 여부 결정 | 설정 페이지 PFID 입력·저장, "현재 연동된 PFID" 표시 |
| messaging_sender | 사용 | 발송 시 발신번호로 사용 (워커·API) | 설정/내 정보 안내 |
| credit_balance | 사용 | 워커에서 잔액 검사·차감 | 대시보드·헤더 잔액 표시 |
| messaging_is_active | 저장·조회만 | **발송 차단 조건으로 미사용** | API는 is_active로 반환, 프론트는 별도 뱃지/문구 없음 |
| messaging_base_price | 사용 | 워커에서 차감 단가 | API 반환, 프론트 특별 표시 없음 |

---

### C. 현재 시스템 전역 설정

- **OWNER_TENANT_ID**: API는 settings (base.py) → os.getenv("OWNER_TENANT_ID", "1"). Worker는 config.py → os.environ.get("OWNER_TENANT_ID", "1").  
  워커 SSM에는 스크립트가 이 값을 넣지 않음 → **워커는 기본 1만 사용할 가능성.**
- **SOLAPI_***: API는 base.py/worker.py, 워커는 config.py.  
  update-workers-env-sqs.ps1이 API SSM에서 SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_SENDER, SOLAPI_KAKAO_PF_ID, SOLAPI_KAKAO_TEMPLATE_ID 복사 → **같은 값 쓰도록 되어 있음.**
- **TEST_TENANT_ID**: API base.py에 설정 있음, 워커 config에 기본 9999. 워커 SSM에 명시되지 않으면 기본값 사용.

---

### D. 현재 정책 적용 상태

- **SMS owner tenant 제한**:  
  - API: enqueue_sms/send_sms에서 can_send_sms(tenant_id) 호출.  
  - 워커: message_mode=="sms" 일 때 및 both 폴백 시 tenant_id != OWNER_TENANT_ID 이면 차단·rollback·실패 로그.  
  → **적용됨.**
- **알림톡 채널 선택**: resolve_kakao_channel 한 곳에서 tenant override + system default fallback.  
  → **중앙화됨.**
- **messaging_is_active**: 발송 차단 조건으로 **미적용.**
- **both**: 알림톡 우선 후 조건부 SMS 폴백으로 **일관되게 동작.**

---

### E. 현재 관리자용 앱 상태

- 메시징 설정 카드: PFID, 발신번호 안내, 연동 가이드 있음.
- "기본 채널 사용 / 자체 채널 사용 / 비활성 / fallback" 상태는 **표시하지 않음.**
- "SMS는 owner tenant 전용" 안내 없음.
- owner가 아닌 테넌트에서도 SMS만/알림톡→SMS 폴백 선택 가능 (실제 발송은 403).

---

## 1. 확인된 사실

- SMS는 OWNER_TENANT_ID에 해당하는 tenant에서만 허용되며, can_send_sms / 워커 OWNER_TENANT_ID 검사로 적용됨.
- 알림톡은 모든 tenant 허용, 채널은 resolve_kakao_channel로 tenant override + system default fallback.
- both는 "알림톡 우선, 실패 시 SMS 폴백"으로 모델·시리얼라이저·워커·문서 일치.
- messaging_is_active는 DB·API 반환에만 쓰이고, 발송 허용/차단에는 사용되지 않음.
- API와 워커의 OWNER_TENANT_ID/SOLAPI 해석 방식은 동일하나, 워커 SSM에 OWNER_TENANT_ID/TEST_TENANT_ID를 넣지 않아 배포 환경에 따라 불일치 가능.

---

## 2. 확인되지 않은 부분

- 배포 시 API/워커 SSM에 실제로 OWNER_TENANT_ID가 어떻게 들어가는지 (수동 입력 여부).
- messaging_is_active를 “메시징 비활성” 차단으로 쓸 계획인지, 아니면 표시용만인지.

---

## 3. 현재 구현상 위험 요소

- 워커 SSM에 OWNER_TENANT_ID/TEST_TENANT_ID가 없으면 워커는 항상 기본값(1, 9999) 사용 → API와 다르게 설정된 경우 정책 불일치.
- owner가 아닌 테넌트에 SMS/폴백 옵션이 그대로 노출되어, 403 전까지는 “가능한 것처럼” 보일 수 있음.

---

## 4. 최소 수정으로 가능한 안정화 포인트

- GET /messaging/info/에 정책·채널 상태 추가: `sms_allowed`(bool), `channel_source`("tenant_override"|"system_default") 등.  
  → 관리자 앱에서 "SMS 가능 여부", "기본/자체 채널" 표시 및 owner가 아닐 때 SMS 옵션 비활성/안내.
- 워커 SSM 갱신 스크립트에 OWNER_TENANT_ID, TEST_TENANT_ID를 API SSM에서 복사하거나, 문서에 “워커 env에 동일 값 설정 필수” 명시.
- (선택) messaging_is_active를 정책에 반영할지 결정 후, 반영 시에만 policy/services/worker에 조건 추가.
- (선택) 발송 결과/로그에 reason_code, policy_block, source 등 최소 필드만 추가해 추적성 개선.
