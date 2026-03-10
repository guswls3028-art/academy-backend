# 자동발송 이벤트 중심 스펙 (SSOT)

대형학원(대치동형) SaaS에서 자동발송은 **"몇 분 전 알림"** 수준이 아니라 **학원 운영 사건(event) 중심**으로 설계한다.

---

## 1. 이벤트별 필수 구성

각 이벤트마다 아래 항목이 있어야 한다.

### 1.1 발송 조건

| 항목 | 설명 |
|------|------|
| **어떤 사건** | 어떤 이벤트 발생 시 발송할지 |
| **시점** | 즉시 발송 / N분 전 / N분 후 / N일 전 등 |
| **반복** | 1회만 / 반복 허용 |
| **상태 조건** | 결석일 때만, 미제출일 때만, 재원생만 등 (선택) |

### 1.2 발송 대상

- 학생
- 학부모
- 담당 강사
- 담임/관리자
- **다중 대상 동시 발송** 가능 여부

### 1.3 메시지 템플릿 변수

- 학생명, 학부모명
- 강의명, 반명, 강사명
- 날짜, 시간, 교실/지점
- 시험명, 과제명, 점수/평균/등급
- 예약 일시, 출결 상태
- 결제/미납 금액
- 상담 링크 / 확인 링크

### 1.4 발송 정책

- 앱푸시만 / 문자만 / 알림톡만
- 앱푸시 실패 시 문자 fallback
- 야간 발송 제한
- 중복 발송 방지
- 같은 이벤트 재발송 쿨타임

### 1.5 운영 로그

- 왜 발송됐는지
- 누가/어떤 규칙으로 발송했는지
- 성공/실패, 실제 수신자
- 템플릿 버전, 재시도 여부

---

## 2. 8개 이벤트 카테고리 (UI 구간)

| 구간 | 설명 | 우선 트리거 |
|------|------|-------------|
| **가입/등록** | 회원가입, 반등록, 수강변경, 만료 등 | 가입완료, 반등록완료, 수강시작일, 등록만료예정, 퇴원, 빈자리안내 |
| **출결** | 수업 전 알림, 입실/퇴실, 지각/결석 | 수업시작 N분전, 입실완료, 결석발생, 연속/누적 결석 |
| **강의** | 차시·수업 관련 | (수업 시작 N분 전은 출결과 통합 가능) |
| **시험** | 시험 lifecycle 전체 | 시험예정 N일전, 시험시작 N분전, 시험미응시, 성적공개, 재시험대상지정 |
| **과제** | 과제 등록/마감/미제출/채점 | 과제등록, 과제마감 N시간전, 과제미제출, 과제제출완료, 채점완료 |
| **성적** | 성적·리포트 | 성적공개, 월간리포트, 성적하락감지, 상담권장 |
| **클리닉/상담** | 클리닉·보강·상담 예약/변경 | 클리닉예약완료, 클리닉시작 N분전, 상담예약완료 |
| **결제** | 결제/미납/만료 | 결제완료, 미납발생, 납부예정일 N일전 |
| **운영공지** | 휴강/보강/강의실변경/긴급공지 | 휴강공지, 시간표변경, 긴급공지 |

---

## 3. Top 15 우선 구현 트리거

1. **수업 시작 N분 전**
2. **입실 완료**
3. **결석 발생**
4. **시험 예정 N일 전**
5. **시험 시작 N분 전**
6. **시험 미응시**
7. **성적 공개**
8. **재시험 대상 지정**
9. **과제 등록**
10. **과제 마감 N시간 전**
11. **과제 미제출**
12. **클리닉 예약 완료**
13. **클리닉 시작 N분 전**
14. **상담 예약 완료**
15. **월간 성적 리포트 발송**

---

## 4. 트리거 코드 매핑 (Backend Trigger choices)

### A. 가입/등록 (signup_registration)

- `student_signup` — 가입 완료
- `registration_approved_student` — 가입 승인(학생)
- `registration_approved_parent` — 가입 승인(학부모)
- `class_enrollment_complete` — 반 등록 완료
- `class_change_complete` — 수강반 변경
- `enrollment_start_date` — 수강 시작일 도래
- `enrollment_expiring_soon` — 등록 만료 예정
- `withdrawal_complete` — 퇴원 처리 완료
- `waitlist_enrollment_complete` — 대기 등록 완료
- `vacancy_notice` — 빈자리 발생 안내

### B. 출결 (attendance)

- `lecture_session_reminder` — 수업 시작 N분 전
- `check_in_complete` — 입실 처리 완료
- `late_occurred` — 지각 발생
- `absent_occurred` — 결석 발생
- `unauthorized_absent_occurred` — 무단결석 발생
- `check_out_complete` — 퇴실 처리 완료
- `consecutive_absent_n` — 연속 결석 N회
- `monthly_absent_n` — 월 누적 결석 N회

### C. 시험 (exam)

- `exam_scheduled_days_before` — 시험 예정 N일 전
- `exam_start_minutes_before` — 시험 시작 N분 전
- `exam_available` — 시험 응시 가능 상태
- `exam_not_taken` — 시험 미응시
- `exam_ended` — 시험 종료
- `exam_score_entered` — 성적 입력 완료
- `exam_score_published` — 성적 공개
- `retake_assigned` — 재시험 대상 지정
- `retake_scheduled` — 재시험 예정 안내
- `exam_fail_or_below` — 불합격/기준 미달

### D. 과제 (assignment)

- `assignment_registered` — 과제 등록
- `assignment_due_hours_before` — 과제 마감 N시간 전
- `assignment_not_submitted` — 과제 미제출
- `assignment_submitted` — 과제 제출 완료
- `assignment_graded` — 과제 채점 완료
- `assignment_feedback_added` — 과제 피드백 등록
- `assignment_consecutive_missing_n` — 과제 누적 미제출 N회

### E. 성적/리포트 (grades)

- `exam_score_published` — (시험과 공유)
- `assignment_score_published` — 과제 성적 공개
- `monthly_report_generated` — 월간 성적 리포트 생성
- `grade_drop_detected` — 성적 하락 감지
- `retake_result_published` — 재시험 결과 공개

### F. 클리닉/상담 (clinic)

- `clinic_reservation_created` — 클리닉 예약 완료
- `clinic_reminder` — 클리닉 시작 N분 전
- `clinic_reservation_changed` — 클리닉 예약 변경
- `clinic_reservation_cancelled` — 클리닉 취소
- `counseling_reservation_created` — 상담 예약 완료
- `counseling_reservation_changed` — 상담 일정 변경/취소
- `counseling_summary_sent` — 상담 후 요약 발송

### G. 결제 (payment)

- `payment_complete` — 결제 완료
- `payment_failed` — 결제 실패
- `arrears_occurred` — 미납 발생
- `payment_due_days_before` — 납부 예정일 N일 전
- `enrollment_expiring_soon` — 수강 만료 예정

### H. 운영공지 (notice)

- `class_cancelled_notice` — 휴강 공지
- `makeup_scheduled_notice` — 보강 공지
- `room_change_notice` — 강의실 변경
- `schedule_change_notice` — 시간표 변경
- `instructor_change_notice` — 강사 변경
- `urgent_notice` — 긴급 공지

---

## 5. 템플릿 변수 (실무 필수)

- `{{학생명}}`, `{{학부모명}}`
- `{{지점명}}`, `{{반명}}`, `{{강의명}}`, `{{강사명}}`
- `{{수업일시}}`, `{{교실명}}`
- `{{시험명}}`, `{{과제명}}`, `{{성적}}`, `{{평균점수}}`, `{{등급}}`
- `{{결석횟수}}`, `{{지각횟수}}`
- `{{예약일시}}`, `{{상담일시}}`
- `{{결제금액}}`, `{{납부기한}}`
- `{{링크}}`

템플릿은 **학생용 / 학부모용 / 강사·운영자용**으로 분리 권장.

---

## 6. 운영 기능 필수

- 테스트 발송, 발송 미리보기
- 최근 발송 로그
- **중복 발송 방지**, 발송 제외 시간(야간)
- 대상 미리보기, 예약 취소/일시정지
- 규칙 on/off, 실패 재시도
- 규칙 우선순위, 조건 충돌 방지

---

## 7. 구현 단계

- **Phase 1**: 9구간 UI + Top 15 트리거 코드 등록 (발송 로직은 추후 연동)
- **Phase 2**: 발송 조건(시점/반복/상태), 발송 대상(학생/학부모/강사/담임), 발송 정책(채널/야간/쿨타임) 필드 및 UI
- **Phase 3**: 템플릿 변수 확장, 운영 로그, 테스트 발송/미리보기/중복방지

이 문서는 메시지 도메인 자동발송의 단일 진실(SSOT)이며, Backend `AutoSendConfig` Trigger choices 및 Frontend 구간/트리거 매핑은 이 스펙을 따른다.
