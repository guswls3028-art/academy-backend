# 메시징 도메인 SSOT (알림톡/SMS 발송 시스템)

> 최종 갱신: 2026-04-09
> 근거: 코드 직접 확인. 추측 없음.

---

## 1. 아키텍처 개요

### 발송 파이프라인

```
도메인 이벤트 (출결/클리닉/시험 등)
  -> send_event_notification()          [services.py:267]
    -> AutoSendConfig 조회 (enabled 확인)
    -> 통합 템플릿 매핑 (alimtalk_content_builders.py)
    -> enqueue_sms()                    [services.py:111]
      -> MessagingSQSQueue.enqueue()    [sqs_queue.py:62]
        -> SQS (academy-v1-messaging-queue)
          -> 메시징 워커                [sqs_main.py:314]
            -> _dispatch_alimtalk() / _dispatch_sms()  [sqs_main.py:630/617]
              -> Solapi SDK / 뿌리오 API
                -> 카카오 알림톡 / 통신사 SMS
```

### 수동 발송 파이프라인

```
관리자 UI (SendMessageModal)
  -> SendMessageView.post()             [views.py:439]
    -> 통합 4종 템플릿 매핑 (CATEGORY_TO_TEMPLATE_TYPE)
    -> build_manual_replacements()       [alimtalk_content_builders.py:171]
    -> enqueue_sms()                     [services.py:111]
      -> (이하 동일)
```

### 시스템 필수 알림톡 파이프라인 (가입/비번)

```
가입 승인 / 비밀번호 찾기
  -> send_alimtalk_via_owner()          [policy.py:311]
    -> 오너 테넌트의 승인 템플릿 조회
    -> enqueue_sms() (오너 tenant_id로)
      -> (이하 동일)
```

### 각 단계의 역할

| 단계 | 파일 | 역할 |
|------|------|------|
| `send_event_notification` | services.py:267 | AutoSendConfig 조회, enabled/dry-run 확인, 오너 테넌트 폴백, 통합 템플릿 빌드, 수신자 전화번호 추출 |
| `enqueue_sms` | services.py:111 | 정책 검증(disabled/restricted/whitelist/SMS 허용), SQS enqueue |
| `MessagingSQSQueue.enqueue` | sqs_queue.py:62 | SQS 메시지 구성, business_idempotency_key 생성, 큐 전송 |
| 메시징 워커 `main` | sqs_main.py:314 | SQS Long Polling, Redis 멱등 잠금, 예약 취소 확인, 잔액 검증/차감, 공급자별 발송, 로그 기록 |
| `_dispatch_alimtalk` | sqs_main.py:630 | 시스템 기본 채널이면 시스템 Solapi, 테넌트 자체 채널이면 테넌트 공급자(solapi/ppurio)로 발송 |
| `_dispatch_sms` | sqs_main.py:617 | 공급자별(ppurio/자체solapi/시스템solapi) SMS 발송. 90byte 이하 SMS, 초과 LMS |

---

## 2. Solapi 통합 템플릿 4종

### 템플릿 목록

| 상수 | Solapi ID | 타입 | 등록 변수 |
|------|-----------|------|-----------|
| `SOLAPI_CLINIC_INFO` | `KA01TP2604061058318608Hy40ZnTFZT` | ITEM_LIST (clinic_info) | 학원이름, 학생이름, 클리닉장소, 클리닉날짜, 클리닉시간, 선생님메모, 사이트링크 |
| `SOLAPI_CLINIC_CHANGE` | `KA01TP260406110706969XS06XRZveEk` | ITEM_LIST (clinic_change) | 학원이름, 학생이름, 클리닉기존일정, 클리닉변동사항, 클리닉수정자, 선생님메모, 사이트링크 |
| `SOLAPI_SCORE` | `KA01TP260406105458211774JKJ3OU55` | ITEM_LIST (score) | 학원이름, 학생이름, 강의명, 차시명, 선생님메모, 사이트링크 |
| `SOLAPI_ATTENDANCE` | `KA01TP260406121126868FGddLmrDFUC` | ITEM_LIST (attendance) | 학원이름, 학생이름, 강의명, 차시명, 강의날짜, 강의시간, 선생님메모, 사이트링크 |

출처: `alimtalk_content_builders.py:23-27`, `TEMPLATE_TYPE_VARIABLES` (line 275-288)

### 활성화 플래그

`UNIFIED_TEMPLATES_ENABLED = True` (line 21). 카카오 검수 승인 완료 후 True로 설정됨. 미승인 상태에서 True로 두면 Solapi 발송 거부.

### Solapi 템플릿 본문 구조

```
#{선생님메모}
#{사이트링크}
```

백엔드에서 트리거별 메시지를 조립해 `#{선생님메모}` 값으로 전송. `#{사이트링크}`는 테넌트 URL.

### TEMPLATE_TYPE_VARIABLES 매핑

`alimtalk_content_builders.py:275-288`에 정의. 각 템플릿 타입에 Solapi로 전달해야 하는 **전체** 변수 목록. 누락하면 카카오 에러 3063.

### ITEM_LIST 변수 23자 제한

- `ITEM_LIST_VAR_MAX_LEN = 23` (line 291)
- 선생님메모, 사이트링크를 **제외**한 변수 값이 23자 초과 시 22자 + "..." 로 truncate (`alimtalk_content_builders.py:258-259`, `437-438`)

### 선생님메모 자동 보강 로직

`build_unified_replacements` (line 382-424), `build_manual_replacements` (line 227-246):

- template.body(선생님 편집 가능)에 핵심 변수(`#{학생이름}`, `#{클리닉장소}` 등)가 **하나도** 없으면
- 핵심 정보(학생이름 + 라벨:값 쌍)를 **자동으로** 선생님메모 앞에 추가
- 변수가 하나라도 있으면 선생님이 커스텀한 것으로 판단하여 자동 보강 안 함

---

## 3. 트리거 -> 템플릿 매핑 (TRIGGER_TO_TEMPLATE_TYPE)

출처: `alimtalk_content_builders.py:48-78`

| 트리거 | 템플릿 타입 | 비고 |
|--------|------------|------|
| `clinic_reservation_created` | clinic_info | |
| `clinic_reminder` | clinic_info | |
| `clinic_check_in` | clinic_info | |
| `clinic_absent` | clinic_info | |
| `clinic_self_study_completed` | clinic_info | clinic_check_out 통합 |
| `clinic_result_notification` | clinic_info | |
| `counseling_reservation_created` | clinic_info | |
| `clinic_reservation_changed` | clinic_info | clinic_change 대신 clinic_info 사용 (카카오 3073 에러) |
| `clinic_cancelled` | clinic_info | 동일 사유로 clinic_info 사용 |
| `check_in_complete` | attendance | |
| `absent_occurred` | attendance | |
| `lecture_session_reminder` | attendance | |
| `exam_scheduled_days_before` | score | |
| `exam_start_minutes_before` | score | |
| `exam_not_taken` | score | |
| `exam_score_published` | score | |
| `retake_assigned` | score | |
| `assignment_registered` | score | |
| `assignment_due_hours_before` | score | |
| `assignment_not_submitted` | score | |
| `monthly_report_generated` | score | |

**참고:** `clinic_reservation_changed`와 `clinic_cancelled`는 원래 `clinic_change` 템플릿이 맞지만, 카카오 하이라이트 길이 제한 3073 에러로 `clinic_info`를 사용 (line 59-61 주석).

---

## 4. 자동 발송 트리거 전체 목록

### AutoSendConfig.Trigger choices

출처: `models.py:187-229`

| 그룹 | 트리거 | 라벨 | 정책 분류 |
|------|--------|------|-----------|
| A. 가입/등록 | `student_signup` | 가입 완료(레거시/미사용) | DISABLED |
| | `registration_approved_student` | 가입 안내(학생) | SYSTEM_AUTO |
| | `registration_approved_parent` | 가입 안내(학부모) | SYSTEM_AUTO |
| | `class_enrollment_complete` | 반 등록 완료 | DISABLED |
| | `enrollment_expiring_soon` | 등록 만료 예정 | DISABLED |
| | `withdrawal_complete` | 퇴원 처리 완료 | MANUAL_DEFAULT |
| B. 출결 | `lecture_session_reminder` | 수업 시작 N분 전 | MANUAL_DEFAULT |
| | `check_in_complete` | 입실 완료(일반 강의) | MANUAL_DEFAULT |
| | `absent_occurred` | 결석 발생(일반 강의) | MANUAL_DEFAULT |
| C. 시험 | `exam_scheduled_days_before` | 시험 예정 N일 전 | MANUAL_DEFAULT |
| | `exam_start_minutes_before` | 시험 시작 N분 전 | MANUAL_DEFAULT |
| | `exam_not_taken` | 시험 미응시 | MANUAL_DEFAULT |
| | `exam_score_published` | 성적 공개 | MANUAL_DEFAULT |
| | `retake_assigned` | 재시험 대상 지정 | MANUAL_DEFAULT |
| D. 과제 | `assignment_registered` | 과제 등록 | MANUAL_DEFAULT |
| | `assignment_due_hours_before` | 과제 마감 N시간 전 | MANUAL_DEFAULT |
| | `assignment_not_submitted` | 과제 미제출 | MANUAL_DEFAULT |
| E. 성적 | `monthly_report_generated` | 월간 성적 리포트 발송 | MANUAL_DEFAULT |
| F. 클리닉 | `clinic_reminder` | 클리닉 시작 N분 전 | AUTO_DEFAULT |
| | `clinic_reservation_created` | 클리닉 예약 완료 | AUTO_DEFAULT |
| | `clinic_reservation_changed` | 클리닉 예약 변경 | AUTO_DEFAULT |
| | `clinic_cancelled` | 클리닉 예약 취소 | AUTO_DEFAULT |
| | `clinic_check_in` | 클리닉 입실 | AUTO_DEFAULT |
| | `clinic_check_out` | 클리닉 퇴실(완료) | (모델에 존재하나 정책 미등록 = DISABLED) |
| | `clinic_absent` | 클리닉 결석 | AUTO_DEFAULT |
| | `clinic_self_study_completed` | 자율학습 완료 | AUTO_DEFAULT |
| | `clinic_result_notification` | 클리닉 대상 해소(완료) | AUTO_DEFAULT |
| | `counseling_reservation_created` | 상담 예약 완료 | AUTO_DEFAULT |
| G. 결제 | `payment_complete` | 결제 완료 | MANUAL_DEFAULT |
| | `payment_due_days_before` | 납부 예정일 N일 전 | MANUAL_DEFAULT |
| I. 비밀번호 | `password_find_otp` | 비밀번호 찾기 인증번호 | SYSTEM_AUTO |
| | `password_reset_student` | 비밀번호 재설정(학생) | SYSTEM_AUTO |
| | `password_reset_parent` | 비밀번호 재설정(학부모) | SYSTEM_AUTO |

정책 분류 출처: `policy.py:25-67`

### 정책 분류 의미

| 분류 | 의미 |
|------|------|
| SYSTEM_AUTO | 시스템 필수. 항상 자동. 사용자가 끌 수 없음 |
| AUTO_DEFAULT | 자동 기본값. 사용자가 끌 수 있음 |
| MANUAL_DEFAULT | 수동 기본값. preview -> confirm 필요. 사용자가 자동화 가능 |
| DISABLED | 현재 비활성 |

### 각 트리거의 코드 호출 위치

| 트리거 | 호출 파일:라인 | 비고 |
|--------|---------------|------|
| `clinic_reservation_created` | clinic/views.py:728 | `transaction.on_commit` |
| `clinic_reservation_changed` | clinic/views.py:1197 | `transaction.on_commit` |
| `clinic_cancelled` | clinic/views.py:872 | set_status에서 next_status=cancelled |
| `clinic_check_in` | clinic/views.py:872 | set_status에서 next_status=checked_in |
| `clinic_absent` | clinic/views.py:872 | set_status에서 next_status=absent |
| `clinic_self_study_completed` | clinic/views.py:939 | complete 액션 |
| `check_in_complete` | attendance/views.py:80 | `_send_attendance_notification` |
| `absent_occurred` | attendance/views.py:80 | `_send_attendance_notification` |
| `registration_approved_*` | (가입 승인 플로우) | `send_alimtalk_via_owner` 경유 |
| `password_find_otp` | (비번 찾기 플로우) | `send_alimtalk_via_owner` 경유 |
| `password_reset_*` | (비번 리셋 플로우) | `send_alimtalk_via_owner` 경유 |

### 도메인 코드에서 send_event_notification 호출이 확인되지 않은 트리거

- `student_signup` (DISABLED, 레거시)
- `class_enrollment_complete` (DISABLED)
- `enrollment_expiring_soon` (DISABLED)
- `withdrawal_complete` (MANUAL_DEFAULT)
- `lecture_session_reminder` (MANUAL_DEFAULT, minutes_before=30)
- `exam_scheduled_days_before` ~ `retake_assigned` (MANUAL_DEFAULT, 시험 관련 5개)
- `assignment_registered` ~ `assignment_not_submitted` (MANUAL_DEFAULT, 과제 관련 3개)
- `monthly_report_generated` (MANUAL_DEFAULT)
- `clinic_reminder` (AUTO_DEFAULT, minutes_before=30)
- `clinic_check_out` (Trigger choices에 존재하나, `clinic_self_study_completed`로 통합)
- `clinic_result_notification` (AUTO_DEFAULT)
- `counseling_reservation_created` (AUTO_DEFAULT)
- `payment_complete`, `payment_due_days_before` (MANUAL_DEFAULT)

**참고:** minutes_before가 있는 트리거는 스케줄러가 호출해야 하며, 현재 스케줄러 구현 상태는 별도 확인 필요.

---

## 5. 수동 발송 경로

### SendMessageView 알림톡 라우팅

출처: `views.py:537-577`

1. `message_mode == "alimtalk"`이면:
2. 템플릿의 `category`와 `name`으로 `get_unified_for_category()` 호출
3. 통합 4종 매핑이 있으면 해당 Solapi 템플릿 사용
4. `signup` 카테고리면 자체 Solapi 템플릿 유지 (`SYSTEM_TEMPLATE_CATEGORIES`)
5. 매핑 없으면 `score`로 fallback (line 567-571)
6. `build_manual_replacements()`로 replacements 빌드 (line 636)

### CATEGORY_TO_TEMPLATE_TYPE 매핑

출처: `alimtalk_content_builders.py:101-114`

| 카테고리 | 템플릿 타입 |
|----------|------------|
| grades | score |
| exam | score |
| assignment | score |
| attendance | attendance |
| lecture | attendance |
| clinic | clinic_info (또는 clinic_change*) |
| payment | score |
| notice | score |
| community | score |
| staff | score |
| default | score |
| student | score |

*clinic 카테고리: template_name에 "변경/취소/change/cancel/reschedule" 키워드가 있거나, extra_vars에 클리닉기존일정/클리닉변동사항/클리닉수정자가 있으면 clinic_change. 그 외 clinic_info. (`get_unified_for_category` line 142-164)

### 시스템 기본양식 (통합 4종 제외)

`SYSTEM_TEMPLATE_CATEGORIES = frozenset({"signup"})` (line 117)

signup 카테고리만 자체 Solapi 템플릿을 유지. 나머지는 모두 통합 4종으로 라우팅.

### 수동 발송 제약

- `message_mode` 기본값: `"sms"` (views.py:453)
- 알림톡 모드이면 `solapi_template_id` 필수 (line 573-577)
- Rate limit: 시간당 500건 (line 474-482)
- 최대 200명 일괄 발송 (line 504-508)
- 알림톡 전용이면 발신번호 선택, SMS면 발신번호 필수 (line 461-468)

---

## 6. Provider 분기 로직

### tenant.messaging_provider

출처: `policy.py:231-246`

- `get_tenant_provider(tenant_id)`: Tenant 모델의 `messaging_provider` 필드 조회. 기본값 `"solapi"`.
- 값: `"solapi"` 또는 `"ppurio"`

### 시스템 기본 채널 vs 테넌트 자체 채널

출처: `policy.py:207-228`, `sqs_main.py:630-659`

**알림톡 채널 결정 (`resolve_kakao_channel`):**

1. 테넌트의 `kakao_pfid` 조회
2. 값이 있으면: `use_default=False` (테넌트 자체 채널)
3. 값이 없으면: `settings.SOLAPI_KAKAO_PF_ID` (시스템 기본 채널), `use_default=True`

**워커의 `_dispatch_alimtalk` 분기 (sqs_main.py:630-659):**

1. `use_default_channel=True` (시스템 기본 채널):
   - **tenant_provider와 무관하게** 시스템 Solapi로 발송
   - 이유: 뿌리오는 @xxx 형식 PFID만 지원하므로 Solapi 형식 PFID를 넘기면 실패
2. `use_default_channel=False` (테넌트 자체 채널):
   - `tenant_provider == "ppurio"` -> ppurio 알림톡
   - 자체 solapi 키 있으면 -> 자체 solapi 알림톡
   - 그 외 -> 시스템 solapi 알림톡

### ppurio 테넌트의 알림톡 처리

- ppurio 테넌트라도 시스템 기본 채널(Solapi PFID) 사용 시에는 시스템 Solapi로 발송
- ppurio 전용 알림톡은 자체 채널(kakao_pfid) 설정 시에만 사용

### SMS 정책

출처: `policy.py:188-197`, `sqs_main.py:661-667`

- OWNER_TENANT_ID (기본 1): 항상 SMS 허용
- 자체 연동 키 보유 테넌트: SMS 허용
- 그 외: SMS 차단 (`MessagingPolicyError` raise)
- 테스트 테넌트(9999): 모든 메시징 비활성

### 테넌트 자체 연동 키

출처: `policy.py:249-274`

`get_tenant_own_credentials()` -> Tenant 모델의 필드:
- `own_solapi_api_key`, `own_solapi_api_secret`
- `own_ppurio_api_key`, `own_ppurio_account`

### 제한 테넌트

출처: `policy.py:83`

`RESTRICTED_MESSAGING_TENANTS = frozenset()` -- 현재 비어 있음 (림글리쉬 제한 해제 완료).

### 메시징 공급자 상세

#### 솔라피(Solapi) -- 기본값
- API Key + API Secret 방식 (HMAC-SHA256)
- 솔라피 콘솔(console.solapi.com)에서 발급

#### 뿌리오(ppurio) -- 선택
- 다우기술 제공 B2B 기업용 메시징 API 서비스
- API: `https://api.bizppurio.com` (v3)
- 인증: Basic Auth(`계정ID:API키` Base64) -> Bearer Token (24시간 유효)
- SMS/LMS/MMS/알림톡/친구톡/RCS 지원
- 토큰 발급: `POST /v1/token` (Basic Auth)
- 메시지 발송: `POST /v3/message` (Bearer Token)
- SMS/LMS 자동 판별: EUC-KR 90바이트 기준

---

## 7. SQS 메시지 구조

출처: `sqs_queue.py:102-128`

| 필드 | 타입 | 설명 |
|------|------|------|
| `tenant_id` | int | 테넌트 ID (필수. 워커에서 잔액/PFID/공급자 조회) |
| `to` | str | 수신 번호 (하이픈 제거됨) |
| `text` | str | 본문 (SMS용 + 알림톡 대체 텍스트) |
| `sender` | str/null | 발신 번호 |
| `created_at` | str (ISO) | 생성 시각 |
| `message_mode` | "sms"/"alimtalk" | 발송 방식 |
| `reservation_id` | int (optional) | 예약 ID (워커에서 취소 여부 double check) |
| `alimtalk_replacements` | list[{key, value}] (optional) | 알림톡 템플릿 치환 변수 |
| `template_id` | str (optional) | Solapi 알림톡 템플릿 ID |
| `event_type` | str (optional) | 비즈니스 이벤트 유형 (30자 제한, 멱등성 키용) |
| `business_idempotency_key` | str | SHA-256 해시 (중복 발송 방지) |

SQS 큐 이름: `academy-v1-messaging-queue` (SSOT, `sqs_queue.py:53`)
DLQ: `academy-v1-messaging-queue-dlq` (line 54)

---

## 8. 멱등성/중복 방지

### 3-Layer 중복 방지

| Layer | 위치 | 메커니즘 |
|-------|------|----------|
| Layer 1: Redis Lock | sqs_main.py:373 | `acquire_job_lock(f"messaging:{message_id}")` - SQS MessageId 기준. Redis 장애 시 fail-closed (메시지 SQS에 남겨 재시도) |
| Layer 2: DB Claim | sqs_main.py:517-541 | `claim_notification_slot()` - business_idempotency_key 기준 UniqueConstraint. 이미 claimed면 삭제 후 스킵 |
| Layer 3: Legacy DB Dedup | sqs_main.py:547-562 | `sqs_message_id` + `success=True` 존재 확인 (Layer 2 미사용 시 폴백) |

### business_idempotency_key 구조

출처: `sqs_queue.py:22-34`

```
canonical = f"msg:{tenant_id}:{channel}:{event_type}:{target_type}:{target_id}:{recipient}:{occurrence_key}:{template_id}"
SHA-256(canonical) -> 64자 hex
```

- `channel`: "sms" 또는 "alimtalk"
- `event_type`: 트리거명 또는 "manual_send"
- `occurrence_key`: 호출자 지정 또는 현재 시각(초 단위)
- DB UniqueConstraint: `(tenant, message_mode, business_idempotency_key)` where `business_idempotency_key > ""` (models.py:64-68)

### _domain_object_id

도메인 코드에서 context에 `_domain_object_id`를 전달하여 occurrence_key로 사용. 예:

- `f"clinic_participant_{obj.pk}"` (clinic/views.py:726)
- `f"participant_{obj.pk}_{next_status}_{int(time.time())}"` (clinic/views.py:870)
- `str(attendance.id)` (attendance/views.py:77)

---

## 9. 안전장치 체계

### Dry-Run

출처: `policy.py:141-173`

- 환경변수 `MESSAGING_DRY_RUN_TRIGGERS`:
  - `"*"`: 모든 이벤트 트리거 dry-run (가입/비번 제외)
  - `"check_in_complete,absent_occurred"`: 특정 트리거만
  - 비어있으면: dry-run 없음 (운영 모드)
- 가입/비번 관련 5개 트리거는 dry-run 대상에서 **항상 제외** (ALWAYS_LIVE_TRIGGERS, line 160-166)

### 테스트 화이트리스트

출처: `policy.py:106-138`

- 환경변수 `MESSAGING_TEST_WHITELIST`: 콤마 구분 전화번호
- 설정 시: 해당 번호에만 실발송 (테스트 모드)
- 미설정 시: 모든 번호 허용 (운영 모드)

### 테넌트 레벨 비활성화

- `is_messaging_disabled(tenant_id)`: TEST_TENANT_ID(기본 9999)이면 모든 메시징 스킵 (policy.py:101-103)
- `is_messaging_restricted(tenant_id)`: RESTRICTED_MESSAGING_TENANTS에 포함되면 비계정 메시징 차단 (현재 비어있음)
- `AutoSendConfig.enabled`: 트리거별 on/off (models.py:249)

### 워커 레벨 안전장치

- 예약 취소 Double Check: `reservation_id` 있으면 발송 직전 DB 확인 (sqs_main.py:441-457)
- 잔액 검증: 발송 전 잔액 < 단가이면 스킵 (sqs_main.py:564-596)
- 테넌트 ID 필수: `tenant_id` 없으면 메시지 삭제 (sqs_main.py:414-425)

### 금지 패턴

- signal/post_save에서 자동 발송
- bulk_create/bulk_update에서 자동 발송
- on_commit 콜백에서 행정 작업 결과로 자동 발송 (가입 안내 제외)
- AutoSendToggle을 행정 화면(출결/시험/과제)에 노출
- preview 없이 confirm 직접 호출
- 과거 날짜 이벤트에 대한 현재 시점 알림 발송

---

## 10. 카카오 ITEM_LIST 제약 사항

### 23자 제한

- ITEM_LIST 템플릿의 변수 값은 23자 이하 (카카오 정책)
- `ITEM_LIST_VAR_MAX_LEN = 23` (`alimtalk_content_builders.py:291`)
- 선생님메모와 사이트링크는 제한 미적용
- 초과 시 22자 + "..." 로 truncate

### 중복 필터 정책

- Solapi에 등록된 변수 **전체**를 보내야 함. 누락 시 에러.
- 값이 없는 변수는 `"-"` 로 전달 (`alimtalk_content_builders.py:262`, `441`)

### 카카오 에러 코드

| 에러 | 의미 | 발생 조건 |
|------|------|-----------|
| 3063 | 잘못된 파라미터 | 등록 변수 누락 또는 변수명 불일치 |
| 3073 | 하이라이트 길이 제한 | ITEM_LIST 하이라이트 항목 길이 초과 (clinic_change에서 발생하여 clinic_info로 전환한 사유) |
| 3076 | 변수값 길이 초과 | 변수 값이 23자 초과 |

---

## 11. 오너 테넌트 폴백

### send_event_notification 폴백 로직

출처: `services.py:309-320`

1. 현재 테넌트의 AutoSendConfig 조회
2. 없으면 오너 테넌트(`OWNER_TENANT_ID`, 기본 1) config로 fallback
3. 알림톡 템플릿 미승인 시: 통합 4종 > 오너 테넌트 승인 템플릿 폴백 (line 344-365)

### send_alimtalk_via_owner

출처: `policy.py:311-386`

- 모든 테넌트에서 가입/비번 관련 알림톡은 오너 테넌트의 승인 템플릿으로 발송
- 비번 리셋 -> 가입 승인 템플릿 재활용 (FALLBACK_TRIGGERS, line 340-344):
  - `password_reset_student` -> `registration_approved_student`
  - `password_reset_parent` -> `registration_approved_parent`
  - `password_find_otp` -> `registration_approved_student`
- SMS fallback 없음 (알림톡 전용)

---

## 12. 자동 프로비저닝

### AutoSendConfigView._auto_provision

출처: `views.py:892-930`

- 테넌트가 처음 자동발송 설정에 접근 시 1회 실행 (configs가 하나도 없으면 트리거)
- `default_templates.py:get_default_templates(tenant.name)` 으로 기본 템플릿 생성
- `{academy_name}` 플레이스홀더를 tenant.name으로 치환
- 기본 설정: `enabled=True`, `message_mode="alimtalk"`
- 자유양식 템플릿(`freeform_*`)은 AutoSendConfig 생성 없이 MessageTemplate만 생성 (유효 트리거가 아님)

### 기본 템플릿 목록

출처: `default_templates.py:13-399`

**자동발송 트리거 템플릿** (27개):
registration_approved_student, registration_approved_parent, withdrawal_complete, lecture_session_reminder, check_in_complete, absent_occurred, exam_scheduled_days_before, exam_start_minutes_before, exam_not_taken, exam_score_published, retake_assigned, assignment_registered, assignment_due_hours_before, assignment_not_submitted, monthly_report_generated, clinic_reminder, clinic_reservation_created, clinic_reservation_changed, clinic_self_study_completed, clinic_cancelled, clinic_check_in, clinic_check_out, clinic_absent, clinic_result_notification, counseling_reservation_created, payment_complete, payment_due_days_before

**자유양식 템플릿** (7개):
freeform_general, freeform_grades, freeform_lecture, freeform_exam, freeform_assignment, freeform_payment, freeform_clinic

### ProvisionDefaultTemplatesView

출처: `views.py:977`

POST로 기존 기본 템플릿 리셋 가능. 이름이 기본값과 동일한 템플릿은 최신 기본값으로 덮어쓰기. 사용자가 새로 만든 템플릿은 유지.

---

## 13. 클리닉/출결 알림 발송 패턴

### _send_clinic_notification

출처: `clinic/views.py:25-35`

```python
def _send_clinic_notification(tenant, student, trigger, context=None):
    for send_to in ("parent", "student"):
        send_event_notification(tenant, trigger, student, send_to, context)
```

- **학생 + 학부모 동시 발송** (AUTO_DEFAULT 정책)
- `transaction.on_commit()` 내에서 호출 (트랜잭션 커밋 후 발송)

### _send_attendance_notification

출처: `attendance/views.py:34-85`

- **학부모만 발송** (`send_to="parent"`)
- Time Guard: 세션 날짜가 오늘이 아니면 발송하지 않음 (과거 날짜 행정 수정 시 알림 방지)
- context: 강의명, 차시명, 날짜, 시간, 반이름, _domain_object_id

---

## 14. MessageTemplate 모델

출처: `models.py:105-179`

### Category choices

DEFAULT, SIGNUP, ATTENDANCE, LECTURE, EXAM, ASSIGNMENT, GRADES, CLINIC, PAYMENT, NOTICE, COMMUNITY, STAFF

### 주요 필드

| 필드 | 설명 |
|------|------|
| `category` | 카테고리 (default/signup/attendance/...) |
| `name` | 템플릿 이름 (120자) |
| `subject` | 제목 (선택, 200자) |
| `body` | 본문 (`#{변수명}` 포함) |
| `solapi_template_id` | Solapi 알림톡 템플릿 ID |
| `solapi_status` | 검수 상태: ""(미신청), PENDING, APPROVED, REJECTED |
| `is_system` | 시스템 기본 양식 여부 (True면 수정/삭제 불가) |
| `is_user_default` | 사용자 지정 기본 양식 (tenant+category당 1개, UniqueConstraint) |

---

## 15. 코드 수정 시 주의사항

### 이 문서와 동기화해야 하는 코드 파일

| 파일 | 동기화 대상 |
|------|------------|
| `apps/support/messaging/alimtalk_content_builders.py` | 섹션 2, 3, 5 (템플릿 ID, 매핑, 변수) |
| `apps/support/messaging/models.py` | 섹션 4, 14 (Trigger choices, MessageTemplate) |
| `apps/support/messaging/default_templates.py` | 섹션 12 (기본 템플릿) |
| `apps/support/messaging/policy.py` | 섹션 4, 6, 9 (정책 분류, 공급자, dry-run) |
| `apps/support/messaging/services.py` | 섹션 1, 11 (파이프라인, 폴백) |
| `apps/support/messaging/sqs_queue.py` | 섹션 7, 8 (메시지 구조, 멱등성) |
| `apps/worker/messaging_worker/sqs_main.py` | 섹션 1, 6, 8 (워커, 공급자 분기, 멱등성) |
| `apps/domains/clinic/views.py` | 섹션 4, 13 (트리거 호출 위치) |
| `apps/domains/attendance/views.py` | 섹션 4, 13 (트리거 호출 위치) |

### TEMPLATE_TYPE_VARIABLES 변경 시

- Solapi에 등록된 변수와 **정확히 일치**해야 함
- 변수 추가/제거 시 Solapi 템플릿 재검수 필요
- 불일치 시 카카오 에러 3063 (잘못된 파라미터) 발생

### default_templates.py 변경 시

- 이미 프로비저닝된 테넌트의 DB 데이터는 자동 갱신되지 않음
- `ProvisionDefaultTemplatesView` POST로 기존 기본 템플릿 리셋 가능
- 사용자가 편집한 템플릿은 유지됨 (이름이 다르면 별도 생성)

### TRIGGER_TO_TEMPLATE_TYPE 변경 시

- 해당 트리거의 Solapi 템플릿 ID가 올바른지 확인
- `UNIFIED_TEMPLATES_ENABLED = True` 상태에서 미승인 템플릿 ID 사용 시 Solapi 발송 거부

### 새 트리거 추가 시 체크리스트

1. `models.py` AutoSendConfig.Trigger choices에 추가
2. `policy.py` TRIGGER_POLICY에 정책 분류 추가
3. `alimtalk_content_builders.py` TRIGGER_TO_TEMPLATE_TYPE에 매핑 추가
4. `default_templates.py` _TEMPLATE_DEFINITIONS에 기본 템플릿 추가
5. 도메인 코드에서 `send_event_notification()` 호출 추가
6. 이 문서 갱신

---

## 변경 이력

- 2026-03-28: 운영사고 수습 -- 입실 자동 발송 제거, 수동 발송 구조 도입
- 2026-03-28: 전역 AutoSendToggle 제거 (시험/과제/성적 화면)
- 2026-03-28: 비필수 트리거 전면 비활성화
- 2026-03-31: 뿌리오(ppurio) v3 API 전환
- 2026-04-09: SSOT 문서 전면 재작성 -- 코드 기반 16개 섹션 구조화
