# 메시징/알림톡 도메인 — 운영 정책 SSOT

## 핵심 원칙
1. **저장과 발송은 분리.** 행정 작업(저장/등록/수정)만으로 알림 발송 금지.
2. **의도한 순간에만, 의도한 사람에게만, 의도한 내용으로만** 발송.
3. **멀티테넌트 격리 절대.** 테넌트 간 데이터/설정/수신자 혼선 불가.
4. **숨겨진 자동 발송 금지.** 모든 발송 경로가 사용자에게 투명해야 함.

## 트리거별 정책

### ALWAYS_ACTIVE (시스템 자동 — 사용자 요청 시 즉시 발송)
| Trigger | 도메인 | 발송 순간 | 금지 순간 | 수신자 | 미리보기 | 비고 |
|---------|--------|----------|----------|--------|---------|------|
| registration_approved_student | 가입 | 가입 승인 시 | — | 학생 | 불필요 | 아이디/비밀번호 포함 |
| registration_approved_parent | 가입 | 가입 승인 시 | — | 학부모 | 불필요 | 학부모/학생 정보 포함 |
| password_find_otp | 비밀번호 | 비밀번호 찾기 요청 시 | — | 요청자 | 불필요 | OTP 코드 |
| password_reset_student | 비밀번호 | 비밀번호 재설정 시 | — | 학생 | 불필요 | 임시 비밀번호 |
| password_reset_parent | 비밀번호 | 비밀번호 재설정 시 | — | 학부모 | 불필요 | 임시 비밀번호 |

### MANUAL_ONLY (선생 수동 — preview → confirm 필수)
| Trigger | 도메인 | 발송 순간 | 금지 순간 | 수신자 | 미리보기 | 비고 |
|---------|--------|----------|----------|--------|---------|------|
| check_in_complete | 출결 | 선생이 출석 탭에서 "입실 알림 발송" 클릭 | 출결 저장/수정/일괄처리만으로 발송 금지 | 학부모 | 필수 | 클리닉 포함 |
| absent_occurred | 출결 | 선생이 출석 탭에서 "결석 알림 발송" 클릭 | 출결 저장/수정/일괄처리만으로 발송 금지 | 학부모 | 필수 | — |

### DISABLED (비활성 — 정책 미확정, 안전하게 잠금)
| Trigger | 도메인 | 정책 미확정 사유 | 활성화 조건 |
|---------|--------|----------------|------------|
| class_enrollment_complete | 수강등록 | 행정 작업으로 오발송 위험 | 수동 발송 구조 전환 후 |
| withdrawal_complete | 퇴원 | 퇴원 시 자동 발송 여부 미확정 | 정책 결정 후 |
| exam_score_published | 성적 | 점수 입력마다 발송되는 구조 위험 | 수동 발송 구조 전환 후 |
| exam_not_taken | 시험 | 미응시 자동 판정 기준 미확정 | 정책 결정 후 |
| assignment_not_submitted | 과제 | 배치 발송 조건 미확정 | 정책 결정 후 |
| assignment_registered | 과제 | 과제 등록=행정 작업 | 수동 발송 구조 전환 후 |
| 나머지 (reminder, clinic 등) | 다양 | 미구현 또는 정책 미확정 | 구현 완료 후 |

## 안전장치 체계
1. **AutoSendConfig.enabled** — DB 레벨 on/off
2. **is_event_dry_run()** — 환경변수 MESSAGING_DRY_RUN_TRIGGERS로 트리거별 dry-run
3. **check_recipient_allowed()** — 환경변수 MESSAGING_TEST_WHITELIST로 수신자 제한
4. **NotificationPreviewToken** — preview → confirm 핸드셰이크 (1회용, 5분 TTL)
5. **is_messaging_disabled()** — 테넌트 9999 발송 비활성화
6. **멱등성 키** — business_idempotency_key (trigger + student_id + 날짜)

## 금지 패턴
- signal/post_save에서 자동 발송
- bulk_create/bulk_update에서 자동 발송
- on_commit 콜백에서 행정 작업 결과로 자동 발송 (가입 안내 제외)
- AutoSendToggle을 행정 화면(출결/시험/과제)에 노출
- preview 없이 confirm 직접 호출
- 과거 날짜 이벤트에 대한 현재 시점 알림 발송

## 변경 이력
- 2026-03-28: 운영사고 수습 — 입실 자동 발송 제거, 수동 발송 구조 도입
- 2026-03-28: 전역 AutoSendToggle 제거 (시험/과제/성적 화면)
- 2026-03-28: 비필수 트리거 전면 비활성화
