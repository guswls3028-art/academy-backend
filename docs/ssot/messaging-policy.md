# 메시징/알림톡 운영 정책 SSOT (2026-08-19 갱신)

## 정책 분류 체계

코드 SSOT: `apps/support/messaging/policy.py` → `TRIGGER_POLICY` dict

### SYSTEM_AUTO — 시스템 필수 안내 (항상 자동, 사용자가 끌 수 없음)
| Trigger | 설명 | 수신자 | 발송 순간 |
|---------|------|--------|----------|
| registration_approved_student | 가입/계정 아이디 안내(학생) | 학생(학생 번호 없으면 학부모) | 신규 학생의 첫 ACTIVE 수강 확정, 학생 아이디 변경, 학생 전화번호 최초 등록 시 |
| registration_approved_parent | 가입/계정 안내(학부모) | 학부모 | 신규 학생의 첫 ACTIVE 수강 확정, 학부모 전화번호 변경/계정 연결 시 |
| password_find_otp | 비밀번호 찾기 OTP (legacy compatibility) | 요청자 | legacy OTP 요청 시 |
| password_reset_student | 비밀번호 변경/재설정(학생) | 학생(학생 번호 없으면 학부모) | 관리자/선생님/본인 비밀번호 변경 또는 재설정 시 |
| password_reset_parent | 비밀번호 변경/재설정(학부모) | 학부모 | 관리자/선생님/본인 비밀번호 변경 또는 재설정 시 |

### AUTO_DEFAULT — 학생 행동 즉시 통보 (자동 기본 on, 선생이 설정에서 끌 수 있음)
| Trigger | 설명 | 수신자 | 발송 순간 |
|---------|------|--------|----------|
| clinic_reservation_created | 클리닉 예약 완료 | 학부모 | 예약 생성(booked/pending) 시 |
| clinic_reservation_changed | 클리닉 예약 변경 | 학부모 | 예약 변경 시 |
| clinic_cancelled | 클리닉 예약 취소 | 학부모 | 상태 → cancelled |
| clinic_check_in | 클리닉 입실 | 학부모 | 상태 → attended |
| clinic_absent | 클리닉 결석 | 학부모 | 상태 → no_show |
| clinic_reminder | 클리닉 시작 N분 전 | 학생 | EventBridge `academy-v1-send-clinic-reminders` → `send_clinic_reminders` |
| clinic_self_study_completed | 클리닉 자율학습 완료(퇴실) | 학부모 | 자율학습 완료(complete) 시 |
| clinic_result_notification | 클리닉 결과 알림 | 학부모 | 클리닉 결과 확정 시 |
| counseling_reservation_created | 상담 예약 완료 | 학부모 | 상담 예약 시 |
| video_encoding_complete | 영상 인코딩 완료 | 스태프(업로더) | 인코딩 완료 시 |

### MANUAL_DEFAULT — 선생 검토 필요 (수동 기본, preview→confirm 또는 설정에서 자동화 가능)
| Trigger | 설명 | 수신자 | 발송 순간 |
|---------|------|--------|----------|
| exam_score_published | 성적 공개 | 학부모 | 선생이 수동 발송 |
| exam_not_taken | 시험 미응시 | 학부모 | 선생이 수동 발송 |
| retake_assigned | 재시험 배정 | 학부모 | 선생이 수동 발송 |
| assignment_not_submitted | 과제 미제출 | 학부모 | 선생이 수동 발송. 배치 명령은 있으나 운영 스케줄 미등록이므로 자동발화는 `manual_only` |
| assignment_registered | 과제 등록 알림 | 학부모 | 선생이 수동 발송 |
| assignment_due_hours_before | 과제 마감 N시간 전 | 학부모 | 스케줄러 미구현, `manual_only` |
| withdrawal_complete | 퇴원 안내 | 학부모 | 선생이 수동 발송 |
| check_in_complete | 일반 강의 입실 | 학부모 | 선생이 수동 발송 |
| absent_occurred | 일반 강의 결석 | 학부모 | 선생이 수동 발송 |
| monthly_report_generated | 월간 리포트 생성 | 학부모 | 선생이 수동 발송 |
| exam_scheduled_days_before | 시험 D-N 리마인더 | 학부모 | 스케줄러 미구현, `manual_only` |
| exam_start_minutes_before | 시험 시작 N분 전 | 학부모 | 스케줄러 미구현, `manual_only` |
| lecture_session_reminder | 수업 리마인더 | 학부모 | 스케줄러 미구현, `manual_only` |
| payment_complete | 결제 완료 | 학부모 | 결제 확정 시 |
| payment_due_days_before | 결제 예정 D-N | 학부모 | 스케줄러 미구현, `manual_only` |

### DISABLED — 비활성 (정책상 의미 없는 트리거)
| Trigger | 사유 |
|---------|------|
| class_enrollment_complete | 수강등록=행정작업, 알림 의미 없음 |
| enrollment_expiring_soon | 미구현 |
| student_signup | 레거시 |

## 핵심 원칙
1. **저장과 발송은 분리.** 학생 마스터 생성·가입 승인만으로는 발송하지 않는다. 신규 학생의 첫 `Enrollment.status=ACTIVE` 확정은 실제 수업 참여 확정이므로 계정 안내를 발송한다.
2. **SYSTEM_AUTO 외에는 사용자가 투명하게 보고 통제 가능.**
3. **일반 강의와 클리닉 정책 절대 분리.**
4. **숨겨진 자동 발송 금지.** 모든 발송 경로가 설정 콘솔에 노출.
5. **공용 알림톡 only.** 제품·고객·운영 경로 모두 SMS/LMS를 실발송하지 않는다. tenant별 PFID/provider도 사용하지 않으며, 운영 오류 알림은 Slack webhook만 사용한다.
6. **fallback 금지.** exact trigger의 공용 승인 템플릿 또는 명시 unified category 템플릿이 없으면 발송하지 않는다.
7. **비알림톡 입력 실패 폐쇄.** SMS/LMS와 알 수 없는 `message_mode`를 알림톡으로 보정하지 않는다. 신규 코드에는 SMS 발송·enqueue 호환 callable이나 `sms_allowed` capability를 만들지 않는다.

## 공용 알림톡 정책

- 모든 알림톡 큐 payload는 `OWNER_TENANT_ID` 공용 채널로 정규화한다.
- 원 업무 테넌트는 `source_tenant_id` 등 로그 메타데이터로만 남긴다.
- tenant별 AutoSendConfig는 enabled/delay/본문 메모 등 업무 설정으로만 사용하고, Solapi 검수 템플릿/PFID/provider의 출처가 될 수 없다.
- `send_alimtalk_via_owner()`는 `OWNER_TENANT_ID`의 exact trigger AutoSendConfig에 연결된 APPROVED 템플릿만 사용한다.
- `password_reset_*` 또는 `password_find_otp`가 `registration_approved_*` 템플릿으로 대체되는 fallback은 금지한다.
- 2026-07-08 Solapi 실등록 감사 기준 `notice_payment` SID는 provider에 없으므로 결제 트리거는 논리 매핑을 유지하되 fail-closed다.
- Community/Q&A 외부 알림톡은 승인 봉투가 없어 자유양식/출석 봉투로 fallback하지 않는다.

## 안전장치 체계
1. **Tenant.messaging_is_active** — 대표·관리자가 화면에서 직접 제어하는 학원 전체 on/off. 신규·기존 사용 중 학원은 기본 on이며 개인 고객의 선호를 코드나 운영 환경변수에 넣지 않는다.
2. **AutoSendConfig.enabled** — 트리거별 DB on/off (설정 콘솔에서 제어)
3. **TRIGGER_POLICY** — 코드 레벨 정책 분류 (SYSTEM_AUTO는 토글 비활성화)
4. **is_event_dry_run()** — MESSAGING_DRY_RUN_TRIGGERS 환경변수로 dry-run
5. **check_recipient_allowed()** — `MESSAGING_RECIPIENT_DENYLIST`의 운영 차단번호를 우선 거부하고, 테스트 환경에서는 `MESSAGING_TEST_WHITELIST`로 추가 제한한다. API enqueue와 워커 소비 입구에서 검사하며 공용 Solapi 호출 직전에도 다시 검사한다.
6. **NotificationPreviewToken** — preview→confirm 핸드셰이크 (1회용, 5분 TTL). confirm 성공 즉시 수신자/본문을 비우며, 1분 주기 `process_scheduled_notifications`가 만료 행을 회당 500건 정리한다. 수동 대량 정리는 `python manage.py purge_expired_notification_preview_tokens [--dry-run]`을 사용한다.
7. **멱등성 키** — business_idempotency_key (trigger + student_id + 날짜)
8. **Time Guard** — 과거 날짜 출결은 알림 차단
9. **계정 알림 event metadata** — `registration_approved_*`, `password_*` 발송은 큐 payload에 원 trigger를 `event_type`으로 싣는다. `NotificationLog.message_body` 보안 마스킹과 운영 추적은 이 값에 의존한다.
   신규 학생 계정 생성 시 초기 안내값은 암호화해 대기시키고, 첫 ACTIVE 수강 확정 후 계정 안내 outbox가 모두 확보되면 즉시 제거한다. 학생/학부모 계정 안내, 아이디 변경, 비밀번호 변경, 학생 전화번호 최초 등록은 SYSTEM_AUTO이며 legacy `send_welcome_message`/`skip_notify` 입력으로 끄지 않는다.
   `registration_approved_student|parent`의 첫 수강 계정 안내는 legacy `AutoSendConfig.enabled` 값으로 끌 수 없다. 공용 owner의 exact APPROVED 템플릿이 없을 때만 fail-closed하며, 학생과 학부모의 유효 수신번호가 다르면 각 계정 안내 outbox를 모두 확보해야 한다.
10. **DB dispatch/outbox** — 수동·시스템·영상·매치업·커뮤니티의 즉시 발송과 `AutoSendConfig.delay_mode` 예약 발송은 모두 `ScheduledNotification`을 먼저 저장한다. product producer의 `enqueue_alimtalk()` 직접 호출은 금지한다. `dispatch_key`에서 안정 occurrence key를 만들고 `pending → dispatching → sent(SQS 접수)`로 전이한다.
11. **SQS enqueue 복구** — transient enqueue 실패는 30초 지수 백오프, 최대 8회 재시도한다. `dispatching` 5분 stale claim도 같은 dispatch key로 회수한다. 입력 오류와 업무 tenant 전체 발송 중지 같은 확정 정책 차단은 SQS 호출·재시도 없이 즉시 terminal `failed`로 전이해 payload를 제거하며, transient 재시도 소진도 terminal `failed`다.
12. **provider 호출 경계** — 워커는 공급사 호출 전에 `NotificationLog.status=sending`을 영속화한다. `sending` 이후 crash/중복 SQS는 공급사를 다시 호출하지 않는다. 같은 SQS 메시지 재전달은 `sending→ambiguous`로 원자 승격하며 차감액을 유지한다. timeout처럼 접수 여부가 불명확한 결과와 함께 operations의 `action_required`로 운영 확인한다.
13. **provider 결과/크레딧 추적** — 성공 응답 group/message id는 `provider_message_id`에 저장한다. 크레딧 예약/롤백은 NotificationLog와 함께 멱등 처리하며 `ambiguous`는 자동 환불하지 않는다.
14. **outbox 개인정보 보존 최소화** — `ScheduledNotification.payload` 원문은 재시도 가능한 `pending/dispatching` 동안에만 보존한다. `sent/cancelled/terminal failed` 전이 시 수신번호, 본문, 치환값, 이름을 제거하고 전달 식별 메타데이터만 남긴다. 비-object legacy payload는 포렌식 원형을 보존한 채 terminal failed로 격리한다.
15. **계정 target key 무전화번호 원칙** — 학생/학부모 계정 알림의 `target_id`는 `student:{student_id}`, `parent:{student_id}`, `parent-account:{parent_id}`만 사용한다. 저장 어댑터와 API는 legacy `parent:{student_id}:{phone}` suffix를 제거한다.
16. **첫 수강 계정 안내 멱등성** — 변경 배포 후 생성된 학생만 암호화된 pending 안내를 가진다. 학생-only 등록·가입 승인·학생 Excel 등록은 발송하지 않는다. 수강 bulk 등록, 수강 Excel 등록, `PENDING|INACTIVE → ACTIVE` 전이에서 커밋 후 pending 안내를 확인하며, 계정 target 기반 outbox가 이미 있으면 재사용한다. 모든 유효 수신자 outbox 확보 전에는 암호문을 유지하고 동일 수강 요청으로 재시도한다. 기존 학생과 두 번째 이후 수강은 pending 값이 없으므로 발송하지 않는다.
17. **업무 tenant 긴급 중지** — `MESSAGING_DISABLED_TENANT_IDS`는 확인된 오발송·중복 확산 같은 긴급 사고에만 원 업무 tenant 기준으로 API enqueue와 워커 소비를 모두 중단한다. 온보딩 승인이나 고객 개인 선호에는 사용하지 않는다. SSM 변경 뒤 각 런타임이 새 값을 읽도록 재기동하며, 해제 전에는 outbox·SQS·provider pending과 연락처 정본을 확인한다. 화면은 이 운영 hold를 고객 설정과 구분해 표시하며 고객 토글이 이를 덮어쓰지 못한다.
18. **공용 owner와 고객 설정 분리** — 공용 owner tenant는 승인 채널 인프라의 소유자일 뿐 고객별 전체 사용 설정의 전역 기준이 아니다. owner 학원이 자기 알림톡을 꺼도 다른 업무 tenant의 발송·계정 복구는 계속되며, owner 경계에서 전역으로 공유되는 차단은 테스트 tenant와 긴급 운영 hold뿐이다.
19. **공급자 계정 일일 브레이크** — 시간당 tenant 한도와 별개로 모든 업무 tenant가 공유하는 공급자 계정에 KST 날짜 기준 `MESSAGING_PROVIDER_DAILY_DISPATCH_LIMIT`(기본 900) 한도를 적용한다. `ScheduledNotification.last_attempt_at` 예약과 outbox가 없는 legacy `NotificationLog`를 중복 없이 합산하며, 한도에 도달한 신규 outbox는 실패/폐기하지 않고 다음 날 00:05 KST로 이월한다. PostgreSQL transaction advisory lock이 tenant 간 동시 claim을 직렬화하며 owner tenant 행의 존재 여부에는 의존하지 않는다. 공급자가 `QuotaExceeded` 또는 `NotEnoughBalance`로 접수 전 거절하면 provider ID·차감이 없는 확정 실패로 닫고 `ambiguous`로 남기지 않는다. 자동 재발송은 하지 않는다.
20. **개인정보 없는 incident trace** — outbox와 worker log는 원문 번호 대신 `MESSAGING_TENANT_BINDING_KEY` HMAC `recipient_fingerprint`를 저장하고, `origin_type`/`origin_id`로 Excel job·수동 batch·domain object를 연결한다. terminal payload에는 이 비식별 메타데이터와 기존 dispatch/business key만 남긴다. 키 순환 중 조회는 fallback key 지문도 함께 계산한다.
21. **Excel 계정 안내 provenance** — Excel로 신규 학생을 만든 job ID는 암호화 pending 계정 안내와 함께 저장한다. 첫 ACTIVE 수강에서 `origin_type=excel_import`, `origin_id=<AIJob job_id>`를 학생/학부모 outbox로 전달하고, 모든 유효 outbox 확보 뒤 비밀번호 암호문과 provenance를 함께 제거한다.
22. **canonical payload 무결성** — 신규 SQS payload는 `occurrence_key`를 명시하고 worker가 수신자·event·target·template을 다시 조합한 business key와 producer key가 같은지 확인한다. signed key를 복사한 뒤 수신자 등을 바꾼 payload는 `invalid_business_idempotency_key`로 공급자 호출 전에 폐기한다.

## 운영 검증

- 배포 후 실발송 검증은 `pwsh scripts/v1/run-messaging-verify-send.ps1 -AwsProfile default`만 사용한다.
- 이 스크립트는 API 인스턴스에서 `messaging_verify_common_alimtalk`을 실행하며, 수신번호는 통제번호 `01031217466` 하나만 허용한다.
- 검증 트리거는 owner exact approved template(`password_reset_student` 기본)을 사용한다. SMS/LMS, tenant별 PFID/provider, 템플릿 fallback을 쓰지 않는다.
- 성공 판정은 SQS enqueue가 아니라 워커가 만든 `NotificationLog.status=sent`, `message_mode=alimtalk`, `tenant_id=OWNER_TENANT_ID`, `provider_message_id` 기록까지다.
- 제품 메시징 사고는 `python manage.py diagnose_messaging_incident --tenant-id <id> --recipient <번호> [--origin-id <job-id>] [--since-hours 72] [--provider]`로 조회한다. 출력은 상태/트리거/연결 건수와 공급자 type/status 집계만 포함하고 번호·본문·비밀번호·provider ID·입력한 origin ID를 출력하지 않는다.

## 변경 이력
- 2026-08-22: 숨은 테넌트별 코드 차단을 제거하고 대표·관리자가 직접 바꾸는 전체 알림톡 설정을 발송 정책에 연결했다. 환경변수 hold는 긴급 사고 전용으로 분리해 화면에 명시하며, 공급자 quota/잔액 접수 전 거절은 확정 실패로 종결한다.
- 2026-08-21: `enqueue_sms`/`send_sms`/provider SMS 호환 callable, `sms_allowed` API 필드, SMS 이름의 계정 알림 throttle을 제거했다. 명시된 비알림톡 `message_mode`는 API·outbox·SQS·worker 경계에서 알림톡으로 보정하지 않고 terminal fail-closed하도록 고정했다. 기존 로그·테넌트 설정 데이터는 이력으로 보존한다.
- 2026-08-20: 플랫폼 운영자 장애 SMS 예외와 활성화 스크립트·워크플로 입력·provider 호출 코드를 제거했다. 운영 오류는 Slack으로만 알리고, 기존 SMS audit action은 이력 조회와 짧은 중복 억제 기간에만 읽는다.
- 2026-08-20: 공유 공급자 KST 일일 900건 기본 브레이크, 수신번호 HMAC 지문, Excel job provenance, canonical business-key 재검증, 개인정보 없는 단일 incident 진단 명령을 추가했다.
- 2026-08-19: 신규 학생 계정 안내 발송 시점을 학생 마스터 생성/가입 승인에서 첫 ACTIVE 수강 확정으로 이동. 초기 안내값은 암호화 보관하고 전체 계정 outbox 확보 후 제거하며, 기존 학생·추가 수강·동일 요청 재시도는 중복 발송하지 않도록 고정.
- 2026-08-19: 잘못 등록된 외부 수신번호와 대량 계정 알림 사고 대응을 위해 운영 수신번호 denylist를 API enqueue·워커 소비·Solapi 호출 직전의 세 경계에 적용하고, tenant 긴급 중지의 재기동/해제 조건을 명시.
- 2026-07-26: 제품 메시징과 분리된 운영자 장애 SMS 단일 예외를 명시. 고정 통제번호에는 플랫폼 발급 테넌트 코드/내부 ID와 통제된 장애 분류/건수만 90-byte 집계로 보내며 owner 수정 테넌트명·사용자 본문·경로·개인정보는 금지. 발송 전 attempt receipt와 접수 직후 group ID를 저장하고, 공급사 미확정은 보존 기간 동안 자동 재발송하지 않는 at-most-once 보류와 실행당 10건 공정 순환 재조회, 확정 실패 5분 cooldown, 시간당 12회 상한으로 중복·폭주를 제한.
- 2026-07-13: 즉시/예약 수동 발송 DB dispatch, SQS enqueue 지수 백오프, stale dispatch 회수, provider `processing→sending→sent|failed|ambiguous` 경계를 도입. 공급사 exactly-once 미지원 구간은 중복 방지를 우선해 자동 재발송하지 않고 operations risk로 노출. `notice_payment` SID 누락은 preflight/send/operations에서 명시적으로 fail-close. 종단 outbox/사용된 preview payload의 PII를 즉시 제거하고 만료 preview token 자동 purge 및 전화번호 없는 계정 target key를 적용.
- 2026-07-08: Solapi provider 실등록 상태와 코드 변수표를 재대조. score ITEM_LIST 등록 변수는 학원이름/학생이름/강의명/차시명/선생님메모/사이트링크 6개로 고정하고, 시험1~4/총점/숙제완성도는 선생님메모 내부 치환 값으로만 사용한다. `notice_payment` SID 누락 상태를 fail-closed로 고정. manual default/community 자유양식 fallback과 Q&A 출석 봉투 fallback을 제거.
- 2026-06-06: SMS/LMS 및 tenant별 알림톡 채널/provider 사용을 금지하고, exact 공용 승인 템플릿 없으면 fail-closed하도록 정책 갱신. 운영 검증 수신번호를 `01031217466`으로 고정하고 provider id 로그를 추가.
- 2026-05-25: `clinic_reminder` 운영 EventBridge 연결. `process_scheduled_notifications` 운영 스케줄 추가. 운영 스케줄이 없는 `assignment_not_submitted`는 자동발화 구현상태에서 제외해 원장 화면 혼선 방지.
- 2026-05-23: 학생 등록 welcome/가입 승인 알림도 `registration_approved_student|parent` event metadata를 큐에 싣도록 정렬. 계정성 알림 로그 마스킹 기준을 문서화.
- 2026-05-21: 공개 로그인 화면 계정복구 SSOT를 `/api/v1/auth/account-recovery/dispatch/`로 정리. `password_find_otp`는 legacy OTP 경로로 명시.
- 2026-04-10: 코드 기반 전면 갱신 — clinic_check_out 제거(clinic_self_study_completed로 통합), 누락 트리거 13개 추가
- 2026-03-28: 정책 확정 — 4분류 체계 (SYSTEM_AUTO/AUTO_DEFAULT/MANUAL_DEFAULT/DISABLED)
- 2026-03-28: 클리닉 트리거 세분화 (cancelled, check_in, check_out, absent)
- 2026-03-28: 설정 콘솔 재정렬 (정책 배지, 템플릿 읽기 전용, DISABLED 숨김)
- 2026-03-28: 일반 강의 출결 자동 발송 코드 완전 제거
- 2026-03-28: 행정 화면 AutoSendToggle 전면 제거
