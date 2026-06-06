# 메시징 도메인 SSOT (알림톡/SMS 발송 시스템)

> 최종 갱신: 2026-06-06 (공용 owner 알림톡 only, provider id 검증 반영)
> 근거: 코드 직접 확인. 추측 없음.

---

## 🚨 §0. 학원장 mental model (절대 원칙, 모든 알림톡 작업의 base)

이 박스를 **반드시 먼저 읽고** 알림톡/메시징 작업 진행. AI가 한 달 반 동안 학원장 의도를 못 따라가서 격분 누적된 핵심 원칙. 메모리 [[feedback_alimtalk_template_prefix_immutable]] 1:1 동기화.

### 비유: 봉투와 편지

- **카카오 검수 통과 ITEM_LIST 4종 양식 = 장식이 다른 4가지 봉투** (header/highlight/item.list/prefix는 카카오에 박혀있어 변경 불가 = 봉투의 장식)
- **`#{선생님메모}` 한 자리 = 봉투 안에 들어가는 자유 편지** (학원장 자유 편집, 무제한 길이)

선생님이 양식 UI에서 하는 모든 행위 — 변수블록 추가/제거, 문구 변경, 새 카테고리 안내, 양식 디자인 — 은 **전부 `#{선생님메모}` 한 자리 안에서 일어남**. 봉투 자체는 장식, 변경 불가.

### 봉투 의미 매칭 (학원장이 카테고리 선택 = 봉투 선택)

| 봉투 | prefix (장식 고정) | 자동 슬롯 (장식) | 학원장 자유 편지 |
|---|---|---|---|
| **score** | `[성적표 안내]` | 학원이름/학생이름/강의명/차시명 | `#{선생님메모}` |
| **attendance** | `[출석 안내]` | 학원이름/학생이름/강의명/차시명/날짜/시간 | `#{선생님메모}` |
| **clinic_info** | `[클리닉 안내]` | 학원이름/학생이름/장소/날짜/시간 | `#{선생님메모}` |
| **clinic_change** | `[일정 변경 안내]` | 학원이름/학생이름/기존일정/변동사항/수정자 | `#{선생님메모}` |
| NONE notice_withdrawal | `[HakwonPlus] 퇴원 처리 완료` | 학원명/학생이름2 | **없음** (시스템 안내) |
| NONE notice_payment | `[HakwonPlus] 결제 완료 안내` | 학원명/학생이름2/사이트링크 | **없음** (시스템 안내) |

성적 관련 안내 → score 봉투. 클리닉 관련 → clinic_info 봉투. 출석 → attendance 봉투. 클리닉 일정 변경/취소 → clinic_change 봉투. **봉투 안 편지(`#{선생님메모}`)는 무제한 자유.**

### 영구 금지 패턴 (응답/제안/백로그에 떠오르면 즉시 멈춤)

- ❌ "이 트리거는 양식이 없다 → 카카오 검수 신청하자"
- ❌ "exam/assignment/notice/community/staff 카테고리에 매핑이 없으니 추가하자"
- ❌ "새로운 양식이 필요하니 Solapi에 등록하자 / 봉투 새로 만들자"
- ❌ "신규 알림톡 템플릿 만들자"
- ❌ "fallback 양식이 필요할 수도 있다" 추론
- ❌ NONE 양식 본문 미반영을 결함으로 분류 / 매핑 제거 시도
- ❌ 양식 추가 / 신규 검수를 backlog · P1 · P2 · 개선안으로 적기

### 학원장 톡 원문 (한 달 반 격분 끝에 박힌 mental model)

- "우리가 만드는 양식 수정은 사실 `#{선생님메모}` 안에만 들어가는거임 ㅇㅋ?"
- "선생님이 변수블록 추가 제거 문구변경등을 하는건 모두 `#{선생님메모}` 여기 안에서 일어나는거라고 이걸 이해시키는데에 1달 반이 걸렸다"
- "계속 양식이없다고 탬플릿 추가해야한다고 하는데 `#{선생님메모}` 여기에 박으면된다고 우리가 프로그램에서 선생님 통해 제작한 풀 템플릿을 그럼 무제한으로 쓸 수 있잖아"
- "`#{선생님메모}` 여기를 제외한건 그냥 일종의 껍데기 UI라고 생각해도됨. 그냥 장식마냥"
- "근데 이제 장식이 용도에 맞게 배치되어야하니까. 성적 관련된건 성적쪽 `#{선생님메모}` // 클리닉 관련된건 클리닉쪽 `#{선생님메모}`"
- "이 내용 전달이 너무 어려웠음 아무리 말해도 계속 탬플릿 추가하려고 하니까 미쳐버리는거임"

### How to apply (모든 알림톡 작업 진행 전 자기 검토)

1. 내가 양식 추가 / 카카오 검수 / 새 템플릿 / 새 봉투 권유하려 하나? → **멈춤**
2. 모든 새 안내 문구 / 카테고리 / 트리거 → 4종 봉투 중 의미 가까운 것 선택 + `#{선생님메모}` 본문 자유 작성으로 cover
3. 학원장이 명시적으로 "새 카카오 양식 검수 받자" 한 경우만 신규 검수 진행
4. NONE 자동발송 매핑(withdrawal_complete/payment_complete/payment_due_days_before) 제거 금지 — 시스템 안내 자동발송이 정상
5. UI에서 양식 본문 편집 잠금 금지 — domain.md §5 "잠금/플래그 강제 금지" 정신

관련: [[domain.md §5 / §5.5]] / [[feedback_alimtalk_template_prefix_immutable]] / [[project_alimtalk_audit_2026_05_13]]

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
      -> (이하 동일, tenant별 PFID/provider fallback 없음)
```

### 시스템 필수 알림톡 파이프라인 (가입/비번)

```
가입 승인 / 계정 복구
  -> send_alimtalk_via_owner()          [apps/domains/messaging/policy.py]
    -> 오너 테넌트의 승인 템플릿 조회
    -> enqueue_sms() (오너 tenant_id로)
      -> (이하 동일)
```

공개 로그인 화면의 아이디 찾기/비밀번호 찾기 정본은 `backend/docs/domain/account-recovery.md`다.

- 현재 공개 endpoint: `/api/v1/auth/account-recovery/dispatch/`
- 아이디 찾기: `registration_approved_student` / `registration_approved_parent` 템플릿 재사용, 비밀번호는 `변경되지 않음`
- 비밀번호 찾기: `password_reset_student` / `password_reset_parent`, 6자리 숫자 임시 비밀번호
- `password_find_otp`는 legacy OTP 경로 호환용이다.

### 각 단계의 역할

| 단계 | 파일 | 역할 |
|------|------|------|
| `send_event_notification` | services.py:267 | AutoSendConfig 조회, enabled/dry-run 확인, 공용 owner 템플릿 또는 unified 템플릿 resolve, 수신자 전화번호 추출 |
| `enqueue_sms` | services.py:111 | 정책 검증(disabled/restricted/whitelist/SMS 차단), owner tenant_id 정규화, SQS enqueue |
| `MessagingSQSQueue.enqueue` | sqs_queue.py:62 | SQS 메시지 구성, business_idempotency_key 생성, 큐 전송 |
| 메시징 워커 `main` | sqs_main.py:314 | SQS Long Polling, Redis 멱등 잠금, 예약 취소 확인, 잔액 검증/차감, 공급자별 발송, 로그 기록 |
| `_dispatch_alimtalk` | sqs_main.py:630 | 공용 시스템 PFID + 공용 Solapi로 알림톡 발송 |
| `_dispatch_sms` | sqs_main.py:617 | legacy 함수. 신규 발송은 정책에서 차단되며 큐 SMS payload도 실패 로그로 닫는다 |

운영 실사용 검증은 `scripts/v1/run-messaging-verify-send.ps1` → `messaging_verify_common_alimtalk`로만 수행한다. 이 경로는 통제번호 `01031217466`으로 `password_reset_student` owner exact approved template을 발송하고, 워커가 `NotificationLog.provider_message_id`를 남긴 뒤 성공으로 판정한다.

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

출처: `alimtalk_content_builders.py:68-104` (2026-05-13 기준)

| 트리거 | 템플릿 타입 | 자동 발화 | 비고 |
|--------|------------|---|------|
| `clinic_reservation_created` | clinic_info | ✅ | |
| `clinic_reminder` | clinic_info | ✅ | EventBridge `academy-v1-send-clinic-reminders` → `send_clinic_reminders` |
| `clinic_check_in` | clinic_info | ✅ | |
| `clinic_absent` | clinic_info | ✅ | ⚠️ 결석 통보를 "[클리닉 안내]" prefix — 의미 검토 |
| `clinic_self_study_completed` | clinic_info | ✅ | clinic_check_out 통합. ⚠️ "완료"를 "안내" prefix — 의미 검토 |
| `clinic_result_notification` | clinic_info | ✅ | ⚠️ "결과"를 "안내" prefix — 의미 검토 |
| `counseling_reservation_created` | clinic_info | ✅ | ⚠️ "상담"이 "[클리닉 안내]" prefix — 학부모 혼동 가능 |
| `clinic_reservation_changed` | clinic_change | ✅ | `clinic.services.lifecycle`가 기존일정/변동사항/수정자 변수 생성 |
| `clinic_cancelled` | clinic_change | ✅ | `clinic.services.lifecycle`가 기존일정/변동사항/수정자 변수 생성 |
| `check_in_complete` | attendance | ✅ | |
| `absent_occurred` | attendance | ✅ | |
| `lecture_session_reminder` | attendance | manual | minutes_before 스케줄러 미구현 |
| `exam_score_published` | score | manual (정책: 저장≠발송) | "[성적표 안내]" prefix 의미 일치 |
| `monthly_report_generated` | score | manual | ⚠️ 월간 집계를 강의명/차시명 단일 변수에 매핑 — 의미 약함 |
| `withdrawal_complete` | notice_withdrawal | manual | NONE 고정 본문 시스템 안내 |
| `payment_complete` | notice_payment | manual | NONE 고정 본문 시스템 안내 |
| `payment_due_days_before` | notice_payment | manual | NONE 고정 본문 시스템 안내 |

### 의도적으로 매핑 제외된 트리거 (`alimtalk_content_builders.py:92-104` 주석)

다음 트리거들은 코드 자동 발화 (`IMPLEMENTED_AUTO_TRIGGERS`) 또는 수동 발송 진입(`ALLOWED_TRIGGERS`) 대상이지만 통합 4종 매핑은 의도적으로 제외:

| 트리거 | 매핑 제외 사유 |
|---|---|
| `exam_scheduled_days_before` / `exam_start_minutes_before` / `exam_not_taken` / `retake_assigned` | score 매핑 한때 있었으나 자동 발화 결함 회피 위해 제거 (`ff2a3f93` / `2cfaea34`) |
| `assignment_registered` / `assignment_due_hours_before` / `assignment_not_submitted` | 동일. `assignment_not_submitted`는 배치 명령은 있으나 운영 스케줄 미등록이라 자동발화 상태는 `manual_only` |
| `video_encoding_complete` / `matchup_report_submitted` | "[성적표 안내]" prefix 의미 불일치 (강사 본인/owner/admin 알림) |
| `qna_answered` / `counsel_answered` | 한 때 TYPE_SCORE 재사용 ([[v1_2_0_seal_2026_04_30]] §6) 이었으나 prefix 의미 불일치로 매핑 제거. test_alimtalk_content_builders.py:55-60 None assert 적용 |

### 매핑 X 트리거의 실제 발송 path

`build_unified_replacements` (line 333-335) → trigger 매핑 X → `return []` (빈 replacements). 이후 `notification_service.send_event_notification` (line 175-223) "기존 모드" 진입:
1. 자체 tenant template 의 solapi_template_id + status==APPROVED 사용 (있는 경우)
2. 없으면 owner tenant (T1 hakwonplus) 의 같은 trigger AutoSendConfig template fallback (line 103-117)
3. owner 에도 없으면 발송 차단 (`return False`)

→ 즉, 위 매핑 제외 trigger 들의 실제 운영 발송 여부 = owner tenant AutoSendConfig 의 별도 승인 template 등록 상태에 의존. **운영 검증 시점에 trigger 별로 owner config 존재 + solapi_status=APPROVED 확인 필요**.

**참고:** `clinic_reservation_changed`와 `clinic_cancelled`는 `clinic_change` 템플릿을 사용하여 기존일정/변동사항/수정자 변수를 ITEM_LIST에 표시.

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
| I. 비밀번호 | `password_find_otp` | 비밀번호 찾기 인증번호 (legacy OTP) | SYSTEM_AUTO |
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
| `clinic_reservation_created` | `clinic/services/lifecycle.py` + `clinic/views/participant_views.py` | create service 이벤트, view가 `on_commit` 발송 |
| `clinic_reservation_changed` | `clinic/services/lifecycle.py` + `clinic/views/participant_views.py` | change_booking service 이벤트, view가 `on_commit` 발송 |
| `clinic_cancelled` | `clinic/services/lifecycle.py` + `clinic/views/participant_views.py` | service가 이벤트 선택, view가 `on_commit` 발송 |
| `clinic_check_in` | `clinic/services/lifecycle.py` + `clinic/views/participant_views.py` | service가 이벤트 선택, view가 `on_commit` 발송 |
| `clinic_absent` | `clinic/services/lifecycle.py` + `clinic/views/participant_views.py` | service가 이벤트 선택, view가 `on_commit` 발송 |
| `clinic_self_study_completed` | `clinic/services/lifecycle.py` + `clinic/views/participant_views.py` | complete service 이벤트, view가 `on_commit` 발송 |
| `check_in_complete` | attendance/views.py:80 | `_send_attendance_notification` |
| `absent_occurred` | attendance/views.py:80 | `_send_attendance_notification` |
| `registration_approved_*` | (가입 승인 플로우) | `send_alimtalk_via_owner` 경유 |
| `password_find_otp` | legacy OTP 경로 | `send_alimtalk_via_owner` 경유 |
| `password_reset_*` | 현재 공개 비밀번호 찾기 + 관리자/선생님 재설정 | `send_alimtalk_via_owner` 경유 |

### 도메인 코드에서 send_event_notification 호출이 확인되지 않은 트리거

- `student_signup` (DISABLED, 레거시)
- `class_enrollment_complete` (DISABLED)
- `enrollment_expiring_soon` (DISABLED)
- `withdrawal_complete` (MANUAL_DEFAULT)
- `lecture_session_reminder` (MANUAL_DEFAULT, minutes_before=30)
- `exam_scheduled_days_before` ~ `retake_assigned` (MANUAL_DEFAULT, 시험 관련 5개)
- `assignment_registered` ~ `assignment_not_submitted` (MANUAL_DEFAULT, 과제 관련 3개)
- `monthly_report_generated` (MANUAL_DEFAULT)
- `clinic_reminder` (AUTO_DEFAULT, minutes_before=30, EventBridge 운영 스케줄 적용)
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

출처: `alimtalk_content_builders.py:127-136` (2026-05-13 정정 — 8 카테고리 제거)

| 카테고리 | 템플릿 타입 | 비고 |
|----------|------------|---|
| grades | score | "[성적표 안내]" prefix 의미 일치 |
| attendance | attendance | |
| lecture | attendance | |
| clinic | clinic_info (또는 clinic_change*) | |
| payment | notice_payment | NONE 고정 본문 시스템 안내 |

### 매핑 의도적 제외 카테고리

다음 카테고리는 코드 매핑 없음. `get_unified_for_category` → `(None, None)` 반환:

- **exam / assignment**: score 매핑 한때 있었으나 의미 일치 검토 보류 — 필요 시 §5.5 정책 따라 기존 4종 양식 + 본문 변수 재활용으로 확장
- **notice / community / staff / default / student**: 카카오 등록 양식 부재

→ 위 카테고리로 호출 시 `get_unified_for_category` 가 (None, None) 반환. 다른 카테고리/템플릿으로 fallback하지 않는다.

*clinic 카테고리: template_name에 "변경/취소/change/cancel/reschedule" 키워드가 있거나, extra_vars에 클리닉기존일정/클리닉변동사항/클리닉수정자가 있으면 clinic_change. 그 외 clinic_info. (`get_unified_for_category` line 163-189)

### 시스템 기본양식 (통합 4종 제외)

`SYSTEM_TEMPLATE_CATEGORIES = frozenset({"signup"})` (line 117)

signup 카테고리만 자체 Solapi 템플릿을 유지. 나머지는 모두 통합 4종으로 라우팅.

### 수동 발송 제약

- `message_mode` 기본값: `"alimtalk"`
- `message_mode`는 알림톡으로 정규화된다. SMS/LMS 실발송은 차단된다.
- 알림톡 모드이면 공용 승인 `solapi_template_id` 필수
- Rate limit: 시간당 500건 (line 474-482)
- 최대 200명 일괄 발송 (line 504-508)
- 발신번호/PFID/provider는 공용 owner 설정만 사용한다.

---

## 6. Provider/채널 정책

### 공용 owner provider

출처: `policy.py`, `queue_service.py`, `sqs_main.py`

- 실발송 provider/PFID는 `OWNER_TENANT_ID` 공용 설정만 사용한다.
- tenant별 `messaging_provider`, `kakao_pfid`, 자체 Solapi/Ppurio 키는 신규 실발송 경로에서 사용하지 않는다.
- `enqueue_sms()`는 알림톡 payload의 `tenant_id`를 owner tenant로 정규화하고 원 업무 테넌트는 `source_tenant_id`로 남긴다.
- worker도 raw/legacy SQS payload의 `tenant_id`를 owner tenant로 재정규화한다. 예약 취소 같은 업무 조회는 `source_tenant_id`를 사용하고, 발송/차감/로그 tenant는 owner tenant만 사용한다.

### 알림톡 채널 결정

출처: `policy.py`

- `resolve_kakao_channel()`은 항상 `settings.SOLAPI_KAKAO_PF_ID` 공용 PFID를 반환한다.
- `channel_source` API 값은 `common_owner`다.

### SMS 정책

출처: `policy.py`, `queue_service.py`, `sqs_main.py`

- SMS/LMS 실발송 전체 금지.
- `can_send_sms()`는 항상 `False`.
- `message_mode="sms"` 신규 enqueue는 `MessagingPolicyError(reason="sms_disabled")`.
- worker가 legacy SMS payload를 받으면 발송하지 않고 실패 로그로 닫는다.
- 테스트 테넌트(9999): 모든 메시징 비활성

### 테넌트 자체 연동 키

출처: `policy.py`

legacy 설정 필드는 남아 있을 수 있으나 신규 실발송 경로에서 사용하지 않는다.

### 제한 테넌트

출처: `policy.py:83`

`RESTRICTED_MESSAGING_TENANTS = frozenset()` -- 현재 비어 있음 (림글리쉬 제한 해제 완료).

## 7. SQS 메시지 구조

출처: `sqs_queue.py:102-128`

| 필드 | 타입 | 설명 |
|------|------|------|
| `tenant_id` | int | 공용 owner 테넌트 ID |
| `source_tenant_id` | int/null | 원 업무 테넌트 ID. 발송 채널/provider 결정에는 사용하지 않음 |
| `to` | str | 수신 번호 (하이픈 제거됨) |
| `text` | str | 본문 (SMS용 + 알림톡 대체 텍스트) |
| `sender` | null | 공용 owner 발신 설정을 worker가 사용 |
| `created_at` | str (ISO) | 생성 시각 |
| `message_mode` | "alimtalk" | 발송 방식. SMS/LMS는 차단 |
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
canonical = f"msg:{tenant_id}:{source_tenant_id}:{channel}:{event_type}:{target_type}:{target_id}:{recipient}:{occurrence_key}:{template_id}"
SHA-256(canonical) -> 64자 hex
```

- `tenant_id`: 공용 owner tenant
- `source_tenant_id`: 원 업무 tenant. owner 직접 발송이면 빈 문자열
- `channel`: "alimtalk"만 신규 발송 가능
- `event_type`: 트리거명 또는 "manual_send"
- `occurrence_key`: 호출자 지정 또는 현재 시각(초 단위)
- DB UniqueConstraint: `(tenant, message_mode, business_idempotency_key)` where `business_idempotency_key > ""` (models.py:64-68)

### _domain_object_id

도메인 코드에서 context에 `_domain_object_id`를 전달하여 occurrence_key로 사용. 예:

- `f"clinic_participant_{obj.pk}"` (`clinic/views/participant_views.py` create)
- `f"participant_{participant.pk}_{next_status}_{int(time.time())}"` (`clinic/services/lifecycle.py`)
- `f"booking_change_{new_booking.pk}"` (`clinic/views/participant_views.py`)
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
| 3043 | 아이템 하이라이트 불일치 | templateItem/templateItemHighlight 불일치 (카카오 공식 코드) |
| 3076 | 변수값 길이 초과 | 변수 값이 23자 초과 |

---

## 11. 공용 오너 알림톡 only

### send_event_notification 템플릿 resolve

출처: `services.py:309-320`

1. 현재 테넌트의 AutoSendConfig 조회: enabled/delay/본문 메모만 사용
2. 검수 템플릿은 명시 unified category 템플릿 또는 오너 테넌트(`OWNER_TENANT_ID`, 기본 1)의 exact trigger 승인 템플릿만 사용
3. tenant template, 다른 trigger, SMS로 fallback하지 않음. 공용 승인 템플릿이 없으면 fail-closed

### send_alimtalk_via_owner

출처: `apps/domains/messaging/policy.py`의 `send_alimtalk_via_owner()`

- 모든 테넌트에서 가입/비번 관련 알림톡은 오너 테넌트의 exact trigger 승인 템플릿으로 발송
- `password_reset_*`, `password_find_otp`가 `registration_approved_*` 템플릿을 재활용하는 fallback 금지
- SMS fallback 없음. tenant별 PFID/provider 사용 없음. 공용 템플릿이 없으면 발송 실패

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

출처: `clinic/views/participant_views.py` + `clinic/services/lifecycle.py`

```python
def _send_clinic_notification(tenant, student, trigger, context=None):
    for send_to in ("parent", "student"):
        send_event_notification(tenant, trigger, student, send_to, context)
```

- **학생 + 학부모 동시 발송** (AUTO_DEFAULT 정책)
- `transaction.on_commit()` 내에서 호출 (트랜잭션 커밋 후 발송)
- create/change_booking/status/complete/uncomplete 계열은 `clinic.services.lifecycle`이 `ClinicNotificationEvent`를 반환하고 view가 `on_commit`으로 발송한다.
- clinic_info context 변수: 클리닉명, 장소, 날짜, 시간, _domain_object_id
- clinic_change context 변수: 클리닉명, 장소, 날짜, 시간, 클리닉기존일정, 클리닉변동사항, 클리닉수정자, _domain_object_id
- `clinic_check_in`/`clinic_absent` 트리거 전용 추가 변수: **도착시간** (상태 처리 시점의 `timezone.now()` → `HH:MM` 포맷). 선생님메모 본문에서 `#{도착시간}`으로 사용 가능.
- 2026-05-23 단말 확인: 실제 카카오톡 복붙 본문에는 자유 본문(`#{선생님메모}`)만 보이고 ITEM_LIST 변수는 별도 UI로 표시될 수 있다. 현재 템플릿 본문은 검수 통과한 자유 본문 정책을 유지한다. 기본 본문에 일정 정보를 중복 삽입하려면 기존 테넌트 템플릿 reset/overwrite 정책을 먼저 정해야 한다.

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
| `apps/domains/clinic/views/participant_views.py` | 섹션 4, 13 (트리거 호출 위치, on_commit dispatch) |
| `apps/domains/clinic/services/lifecycle.py` | 섹션 4, 8, 13 (상태/완료 전이와 클리닉 이벤트 context) |
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
