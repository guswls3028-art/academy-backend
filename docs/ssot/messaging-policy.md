# 메시징/알림톡 운영 정책 SSOT (2026-04-10 갱신)

## 정책 분류 체계

코드 SSOT: `apps/support/messaging/policy.py` → `TRIGGER_POLICY` dict

### SYSTEM_AUTO — 시스템 필수 안내 (항상 자동, 사용자가 끌 수 없음)
| Trigger | 설명 | 수신자 | 발송 순간 |
|---------|------|--------|----------|
| registration_approved_student | 가입 안내(학생) | 학생 | 가입/등록 승인 시 |
| registration_approved_parent | 가입 안내(학부모) | 학부모 | 가입/등록 승인 시 |
| password_find_otp | 비밀번호 찾기 OTP | 요청자 | 비밀번호 찾기 요청 시 |
| password_reset_student | 비밀번호 재설정(학생) | 학생 | 비밀번호 재설정 시 |
| password_reset_parent | 비밀번호 재설정(학부모) | 학부모 | 비밀번호 재설정 시 |

### AUTO_DEFAULT — 학생 행동 즉시 통보 (자동 기본 on, 선생이 설정에서 끌 수 있음)
| Trigger | 설명 | 수신자 | 발송 순간 |
|---------|------|--------|----------|
| clinic_reservation_created | 클리닉 예약 완료 | 학부모 | 예약 생성(booked/pending) 시 |
| clinic_reservation_changed | 클리닉 예약 변경 | 학부모 | 예약 변경 시 |
| clinic_cancelled | 클리닉 예약 취소 | 학부모 | 상태 → cancelled |
| clinic_check_in | 클리닉 입실 | 학부모 | 상태 → attended |
| clinic_absent | 클리닉 결석 | 학부모 | 상태 → no_show |
| clinic_reminder | 클리닉 시작 N분 전 | 학생 | 스케줄러 |
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
| assignment_not_submitted | 과제 미제출 | 학부모 | 선생이 수동 발송 |
| assignment_registered | 과제 등록 알림 | 학부모 | 선생이 수동 발송 |
| assignment_due_hours_before | 과제 마감 N시간 전 | 학부모 | 스케줄러 |
| withdrawal_complete | 퇴원 안내 | 학부모 | 선생이 수동 발송 |
| check_in_complete | 일반 강의 입실 | 학부모 | 선생이 수동 발송 |
| absent_occurred | 일반 강의 결석 | 학부모 | 선생이 수동 발송 |
| monthly_report_generated | 월간 리포트 생성 | 학부모 | 선생이 수동 발송 |
| exam_scheduled_days_before | 시험 D-N 리마인더 | 학부모 | 스케줄러 |
| exam_start_minutes_before | 시험 시작 N분 전 | 학부모 | 스케줄러 |
| lecture_session_reminder | 수업 리마인더 | 학부모 | 스케줄러 |
| payment_complete | 결제 완료 | 학부모 | 결제 확정 시 |
| payment_due_days_before | 결제 예정 D-N | 학부모 | 스케줄러 |

### DISABLED — 비활성 (정책상 의미 없는 트리거)
| Trigger | 사유 |
|---------|------|
| class_enrollment_complete | 수강등록=행정작업, 알림 의미 없음 |
| enrollment_expiring_soon | 미구현 |
| student_signup | 레거시 |

## 핵심 원칙
1. **저장과 발송은 분리.** 행정 작업(저장/등록/수정)만으로 알림 발송 금지.
2. **SYSTEM_AUTO 외에는 사용자가 투명하게 보고 통제 가능.**
3. **일반 강의와 클리닉 정책 절대 분리.**
4. **숨겨진 자동 발송 금지.** 모든 발송 경로가 설정 콘솔에 노출.

## Owner Fallback 정책
모든 트리거에서 owner tenant fallback 허용. config.enabled가 2차 가드.

## 안전장치 체계
1. **AutoSendConfig.enabled** — DB 레벨 on/off (설정 콘솔에서 제어)
2. **TRIGGER_POLICY** — 코드 레벨 정책 분류 (SYSTEM_AUTO는 토글 비활성화)
3. **is_event_dry_run()** — MESSAGING_DRY_RUN_TRIGGERS 환경변수로 dry-run
4. **check_recipient_allowed()** — MESSAGING_TEST_WHITELIST로 수신자 제한
5. **NotificationPreviewToken** — preview→confirm 핸드셰이크 (1회용, 5분 TTL)
6. **멱등성 키** — business_idempotency_key (trigger + student_id + 날짜)
7. **Time Guard** — 과거 날짜 출결은 알림 차단

## 변경 이력
- 2026-04-10: 코드 기반 전면 갱신 — clinic_check_out 제거(clinic_self_study_completed로 통합), 누락 트리거 13개 추가
- 2026-03-28: 정책 확정 — 4분류 체계 (SYSTEM_AUTO/AUTO_DEFAULT/MANUAL_DEFAULT/DISABLED)
- 2026-03-28: 클리닉 트리거 세분화 (cancelled, check_in, check_out, absent)
- 2026-03-28: 설정 콘솔 재정렬 (정책 배지, 템플릿 읽기 전용, DISABLED 숨김)
- 2026-03-28: 일반 강의 출결 자동 발송 코드 완전 제거
- 2026-03-28: 행정 화면 AutoSendToggle 전면 제거
