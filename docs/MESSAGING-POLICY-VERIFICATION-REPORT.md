# 메시징 정책 운영 반영 검증 보고서

작성일: 2025-03-08  
기준: 실제 코드·AWS SSM·배포 스크립트 실행 결과만 기록. 추측 없음.

---

## 1. 배포/환경 검증

### 1.1 API SSM (`/academy/api/env`) 확인

- **확인 방법:** `aws ssm get-parameter --name "/academy/api/env" --with-decryption --profile default --region ap-northeast-2` 실행.
- **결과:**
  - **OWNER_TENANT_ID:** 키 없음. (API는 `apps/api/config/settings/base.py`에서 `os.getenv("OWNER_TENANT_ID", "1")` → **기본값 1** 사용.)
  - **TEST_TENANT_ID:** 키 없음. (동일하게 코드 기본값 9999 사용.)
  - SOLAPI 관련: `SOLAPI_API_KEY`, `SOLAPI_API_SECRET`, `SOLAPI_SENDER`, `SOLAPI_KAKAO_PF_ID` 존재. `SOLAPI_KAKAO_TEMPLATE_ID`는 응답 JSON에 없음(빈 값이면 스크립트가 복사하지 않을 수 있음).
  - **확인된 사실:** API SSM에는 OWNER_TENANT_ID, TEST_TENANT_ID가 **실제로 존재하지 않음**.

### 1.2 update-workers-env-sqs.ps1 실행

- **실행:** `powershell -File .\scripts\v1\update-workers-env-sqs.ps1 -AwsProfile default`
- **결과:** 정상 종료. 로그에 "SOLAPI_* + OWNER_TENANT_ID/TEST_TENANT_ID copied from /academy/api/env" 출력.
- **동작:** API SSM에 해당 키가 있고 값이 비어 있지 않을 때만 워커 SSM에 복사. 현재 API에 OWNER_TENANT_ID/TEST_TENANT_ID가 없으므로 **복사된 값 없음**.

### 1.3 워커 SSM (`/academy/workers/env`) 동기화 후 확인

- **확인 방법:** 동일 파라미터 get-parameter 후 Base64 디코드하여 JSON 키 목록 확인.
- **결과:**
  - **OWNER_TENANT_ID:** 없음.
  - **TEST_TENANT_ID:** 없음.
  - **SOLAPI_*:** `SOLAPI_API_KEY`, `SOLAPI_API_SECRET`, `SOLAPI_SENDER`, `SOLAPI_KAKAO_PF_ID` 존재.
  - **SOLAPI_MOCK:** `"true"` 존재 (워커는 Mock 모드).
  - **MESSAGING_SQS_QUEUE_NAME:** `academy-v1-messaging-queue`.
- **확인된 사실:** 워커 SSM에도 OWNER_TENANT_ID, TEST_TENANT_ID는 **없음**. API에 없으므로 스크립트가 넣을 수 없음.

### 1.4 워커 재시작/재배포 필요 여부

- **env 반영 시점:** `scripts/v1/resources/worker_userdata.ps1` 기준, EC2 부팅 시 UserData가 `aws ssm get-parameter --name /academy/workers/env` 로 **한 번** 읽어 `/opt/workers.env` 생성 후 컨테이너에 `--env-file`로 전달. 이미 떠 있는 인스턴스는 SSM 변경만으로는 env가 바뀌지 않음.
- **이번 변경:** 워커 SSM에 새 키(OWNER_TENANT_ID 등)를 **추가하지 않았음** (API에 없어서). 따라서 **이번만 놓고 보면 instance-refresh 불필요**.
- **앞으로:** API SSM에 OWNER_TENANT_ID/TEST_TENANT_ID를 넣고 update-workers-env-sqs.ps1 실행한 뒤에는, 워커가 새 값을 쓰려면 **instance-refresh**로 새 인스턴스가 기동해 SSM을 다시 읽어야 함.

### 1.5 API와 워커 최종값 비교

| 항목 | API | 워커 |
|------|-----|------|
| OWNER_TENANT_ID | 설정 없음 → 코드 기본값 **1** | SSM 없음 → config 기본값 **1** |
| TEST_TENANT_ID | 설정 없음 → 코드 기본값 **9999** | SSM 없음 → config 기본값 **9999** |
| SOLAPI_* | SSM에 키 존재 | SSM에 동일 키 복사됨 |
| SOLAPI_MOCK | API SSM에는 없음(실제 발송용) | 워커 SSM **true** (Mock) |

- **확인된 사실:** 현재 상태에서는 API와 워커 모두 OWNER_TENANT_ID=1, TEST_TENANT_ID=9999를 **코드 기본값**으로 사용하므로 **정책상 일치**.

---

## 2. 관리자 앱 반영 검증 (코드 기준)

- **검증 방법:** 브라우저 미실행. 배포된 프론트 코드 및 API 응답 구조 기준으로만 서술.

### 2.1 Owner tenant (tenant_id=1)

- **메시징 상태 패널:** `MessageSettingsPage.tsx` 상단 "메시징 상태" Panel에서 `info?.channel_source`, `info?.sms_allowed` 사용. API가 `sms_allowed: true`, `channel_source: "system_default"|"tenant_override"` 반환.
- **채널 출처:** `channel_source === "tenant_override"` → "자체 채널 사용 중", 아니면 "기본 채널 사용 중".
- **SMS 사용 가능:** `sms_allowed === true` → "사용 가능" 표시.
- **SendMessageModal:** `smsAllowed === true` 이면 "SMS만", "알림톡→SMS 폴백" 라디오 활성.
- **MessageAutoSendPage:** `smsAllowed === true` 이면 발송 방식 select에서 SMS만·알림톡→SMS 폴백 선택 가능.
- **확인된 사실:** owner tenant일 때 위 동작이 코드상 보장됨.

### 2.2 Non-owner tenant

- **SMS 불가 안내:** MessageSettingsPage "문자(SMS) 사용"에서 `!info?.sms_allowed` 시 "문자(SMS)는 내 테넌트 전용 정책으로 현재 이 학원에서는 사용할 수 없습니다." 문구 표시.
- **SMS/both 비활성:** SendMessageModal에서 `!smsAllowed` 시 해당 두 라디오 `disabled`, 동일 안내 문구. MessageAutoSendPage에서 발송 방식 옵션 `disabled={!smsAllowed}` 및 상단 안내 문구.
- **기존 sms/both → alimtalk 정규화:** MessageAutoSendPage에서 `effectiveMode = !smsAllowed && (sms|both) ? "alimtalk" : config.message_mode`, select의 value는 `effectiveMode`. 저장 시 `handleUpdate`에서 `!smsAllowed`이면 `message_mode`를 alimtalk로 보정해 `updateMut.mutate(toSend)` 호출. 따라서 **화면 표시**와 **저장 페이로드** 모두 non-owner일 때 sms/both가 alimtalk로 정규화됨.
- **확인된 사실:** non-owner에서 SMS/both 비활성 및 안내, 저장 시 alimtalk로 보정되는 것이 코드상 구현됨.

---

## 3. 데이터 정합성 점검

- **DB 직접 조회:** 미수행 (현재 세션에서 DB/ Django shell 접근 없음).
- **코드 경로상 영향:**
  - non-owner tenant에 대해 `AutoSendConfig.message_mode`가 `sms` 또는 `both`인 행이 **DB에 이미 있어도**:
    - **자동발송 트리거 시:** `send_welcome_messages` / `send_registration_approved_messages` 등에서 `enqueue_sms(..., message_mode=config.message_mode)` 호출. `message_mode in ("sms","both")`이면 `can_send_sms(tenant_id)` 검사에서 False → `MessagingPolicyError` 발생하여 enqueue 자체가 되지 않음 (services.py).
    - **워커:** 만약 예전에 enqueue된 메시지가 큐에 남아 있고 message_mode가 sms/both라면, 워커가 처리할 때 `tenant_id != OWNER_TENANT_ID`이면 SMS 경로에서 차단·rollback·실패 로그 (sqs_main.py).
  - **관리자 앱:** GET /messaging/auto-send/ 로 가져온 config에 message_mode가 sms/both여도, `effectiveMode`로 "alimtalk"로 보여주고 PATCH 시 `toSend`에서 alimtalk로 보정해 저장하므로, **한 번이라도 자동발송 설정을 저장하면** 해당 tenant의 config는 alimtalk로 덮어쓰여짐.
- **정리:** DB에 sms/both가 남아 있어도 런타임에서는 **enqueue 단계 또는 워커 단계에서 차단**됨. UI에서 저장 시 alimtalk로 정규화됨. **마이그레이션은 수행하지 않았으며, 현황만 보고함.**

---

## 4. 실제 발송 검증 준비 (tenant 1 기준)

- **enqueue 진입점:** SendMessageView → `enqueue_sms(tenant_id=tenant.id, ...)`. tenant 1이면 `can_send_sms(1)` True.
- **큐:** `MESSAGING_SQS_QUEUE_NAME=academy-v1-messaging-queue`, API·워커 SSM 모두 동일.
- **워커:** 메시징 워커 ASG가 해당 큐를 소비. 워커 SSM에 **SOLAPI_MOCK=true** 가 있으므로 `config.py` 로드 시 `_require_solapi`가 placeholder 허용하고, `sqs_main.py`의 `_get_solapi_client`가 `SOLAPI_MOCK`/`DEBUG`일 때 `MockSolapiMessageService` 사용 → **실제 Solapi 호출 없음**.
- **sender:** API SSM에 `SOLAPI_SENDER=01012345678`, 워커 SSM에도 동일 키 복사됨.
- **결론 (사실만):**
  - **지금 바로 1건 “enqueue → 큐 적재 → 워커가 메시지 소비”까지:** 가능. (tenant 1, 발신번호·본문 등 유효하면 enqueue 성공, 워커가 처리.)
  - **지금 바로 1건 “실제 문자가 수신자에게 도달”:** 불가. 워커가 **SOLAPI_MOCK=true** 로 Mock 모드라 실제 발송하지 않음.
- **실제 문자까지 테스트하려면:** 워커 SSM에서 SOLAPI_MOCK을 제거하거나 false로 변경한 뒤, **instance-refresh**로 워커가 새 env를 읽도록 해야 함. (그 전에 솔라피 발신번호·IP 허용 등 운영 요건 확인 필요.)

---

## 5. 결과 요약 (요청 형식)

### 1) 확인된 사실

- API SSM에 OWNER_TENANT_ID, TEST_TENANT_ID **없음**. API·워커 모두 코드 기본값 1, 9999 사용 → 정책 일치.
- update-workers-env-sqs.ps1 실행 완료. 복사 대상에 OWNER_TENANT_ID, TEST_TENANT_ID 포함되어 있으며, API에 키가 없어 이번에는 워커 SSM에 추가된 값 없음.
- 워커 SSM에는 SOLAPI_MOCK=true 존재. 워커는 Mock 모드로 동작해 실제 발송 안 함.
- 관리자 앱 코드상 owner/non-owner별 메시징 상태·채널 출처·SMS 비활성·저장 시 alimtalk 정규화 반영됨.
- non-owner의 기존 sms/both 설정은 enqueue 또는 워커에서 차단되며, UI 저장 시 alimtalk로 덮어쓰기 가능.

### 2) 아직 확인 안 된 것

- API SSM이 평문 JSON으로 내려오는지 Base64인지에 따른 차이(실제로 API 호출 시 JSON으로 받음). 워커 SSM은 Base64 저장 확인됨.
- DB 내 non-owner tenant의 AutoSendConfig 중 message_mode=sms/both 인 행 수 및 tenant_id별 분포 (DB 미접근).
- 배포된 프론트(academyfront)가 최신 커밋 반영 여부 및 실제 브라우저에서의 화면 표시.

### 3) 운영 반영 완료된 것

- update-workers-env-sqs.ps1에 OWNER_TENANT_ID, TEST_TENANT_ID 복사 로직 반영 및 실행 완료.
- API·워커가 동일한 OWNER/TEST_TENANT_ID 기본값을 쓰는 상태로 정책 일치.
- (이미 적용된) 백엔드 GET /messaging/info/ 의 sms_allowed, channel_source 및 관리자 앱 표시·비활성·정규화 로직이 코드에 반영된 상태.

### 4) 남은 리스크

- API SSM에만 OWNER_TENANT_ID를 나중에 넣고 워커 SSM 갱신을 빼먹거나, 갱신 후 instance-refresh를 하지 않으면, API와 워커의 OWNER_TENANT_ID가 달라질 수 있음.
- 워커 SOLAPI_MOCK=true 인 채로 두면 실제 문자 발송은 계속되지 않음. 실제 발송 테스트 시 SSM 수정 + instance-refresh 필요.

### 5) 실제 테스트 발송 가능 여부

- **enqueue까지:** 가능 (tenant 1, 유효한 요청이면 1건 테스트 가능).
- **실제 문자 수신까지:** 현재 워커 env가 SOLAPI_MOCK=true 이므로 **불가**. Mock 해제 및 워커 재기동 후에만 가능.

### 6) 테스트 전에 필요한 마지막 조치

- **enqueue + 워커 처리(로그까지) 확인:** 추가 조치 없이 테스트 가능. (실제 발송은 Mock.)
- **실제 1건 문자 수신 테스트:** (1) 워커 SSM에서 SOLAPI_MOCK 제거 또는 false 설정, (2) 메시징 워커 ASG instance-refresh 실행, (3) 솔라피 발신번호·IP 허용 등 운영 요건 확인 후 1건 발송 테스트.
