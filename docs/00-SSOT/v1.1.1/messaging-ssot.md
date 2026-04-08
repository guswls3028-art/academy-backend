# 메시지 도메인 SSOT

> 최종 갱신: 2026-04-08
> 버전: V1.1.1

---

## 1. 카카오 채널 / 발신 정보

| 항목 | 값 | 비고 |
|------|---|------|
| 카카오 채널 PF ID | `KA01PF260213050050151CbZonvKMlh4` | 모든 테넌트 공유 |
| 카카오 채널명 | **학원플러스** | Solapi 콘솔/카카오비즈에서만 변경 가능 |
| SMS 발신번호 | `01031217466` (Tenant 1) | 테넌트별 `messaging_sender` |
| 사이트 링크 | `https://{tenant_primary_domain}` | `get_tenant_site_url()` |

**채널명 "학원플러스"는 코드/템플릿이 아닌 카카오 비즈니스 채널 설정.** 변경하려면 카카오 비즈니스센터에서 채널명 수정 필요.

---

## 2. 변수 계약 (프론트 ↔ 백엔드 ↔ Solapi)

| 변수 | Solapi 변수명 | 백엔드 소스 | 프론트 전달 | 예시값 |
|------|-------------|-----------|-----------|--------|
| 학원명 | `#{학원명}` | `tenant.name` (자동) | 불필요 | HakwonPlus |
| 학생이름 (전체) | `#{학생이름}` | `student.name` (자동) | 불필요 | 홍길동 |
| 학생이름 (뒤 2글자) | `#{학생이름2}` | `student.name[-2:]` (자동) | 불필요 | 길동 |
| 학생이름 (전체) | `#{학생이름3}` | `student.name` (자동) | 불필요 | 홍길동 |
| 사이트링크 | `#{사이트링크}` | `get_tenant_site_url()` (자동) | 불필요 | https://hakwonplus.com |
| 강의명 | `#{강의명}` | `alimtalk_extra_vars` | `lecture.title` | 수학 심화반 |
| 차시명 | `#{차시명}` | `alimtalk_extra_vars` | `session.title` | 12차시 (03/24) |
| 시험명 | `#{시험명}` | `alimtalk_extra_vars` | `exam.title` | 단원평가 3회 |
| 과제명 | `#{과제명}` | `alimtalk_extra_vars` | `homework.title` | 교재 p.52~60 |
| 시험성적 | `#{시험성적}` | `alimtalk_extra_vars` | `buildScoreDetail()` | [시험]\n- 단원평가... |
| 클리닉명 | `#{클리닉명}` | `alimtalk_extra_vars` | 클리닉 컨텍스트 | 수학 보충 |
| 클리닉합불 | `#{클리닉합불}` | `alimtalk_extra_vars` | 클리닉 결과 | 합격 |
| 공지내용 | `#{공지내용}` | `raw_body` 전체 (freeform) | 직접 입력 | (자유 텍스트) |
| 날짜 | `#{날짜}` | `alimtalk_extra_vars` | 이벤트 컨텍스트 | 2026-03-24 |
| 시간 | `#{시간}` | `alimtalk_extra_vars` | 이벤트 컨텍스트 | 14:00 |

**자동 변수** (학원명, 학생이름, 사이트링크): 백엔드 `SendMessageView`에서 자동 치환. 프론트 전달 불필요.
**컨텍스트 변수** (강의명, 시험명 등): 프론트에서 `alimtalkExtraVars`로 전달 또는 자동발송 시 서비스에서 직접 구성.

### 2-B. 통합 템플릿 변수 (Solapi 등록 변수, 2026-04-08~)

> **핵심:** 솔라피에는 `#{선생님메모}`, 프론트 UI에는 `내용`으로 표시. 카카오 검수에서 `#{내용}`은 어뷰징 우려로 반려됨.

| 템플릿 타입 | Solapi ID | 등록 변수 | 용도 |
|------------|-----------|----------|------|
| **clinic_info** | `KA01TP2604061058318608Hy40ZnTFZT` | 학원이름, 학생이름, 클리닉장소, 클리닉날짜, 클리닉시간, **선생님메모**, 사이트링크 | 클리닉 일정 안내 |
| **clinic_change** | `KA01TP260406110706969XS06XRZveEk` | 학원이름, 학생이름, 클리닉기존일정, 클리닉변동사항, 클리닉수정자, **선생님메모**, 사이트링크 | 클리닉 변경/취소 |
| **score** | `KA01TP260406105458211774JKJ3OU55` | 학원이름, 학생이름, 강의명, 차시명, **선생님메모**, 사이트링크 | 성적/시험/과제 안내 |
| **attendance** | `KA01TP260406121126868FGddLmrDFUC` | 학원이름, 학생이름, 강의명, 차시명, 강의날짜, 강의시간, **선생님메모**, 사이트링크 | 수업 출석 안내 |

**활성화 플래그:** `UNIFIED_TEMPLATES_ENABLED` (`alimtalk_content_builders.py`)
- ~~`False`~~: 기존 개별 승인 템플릿 사용
- **`True` (현재, 2026-04-08 활성화)**: 통합 4종 사용 중 (카카오 승인 완료)

---

## 3. 이벤트별 메시지 매핑

### A. 실제 동작 중 (APPROVED, 코드 연결 완료)

| 이벤트 | 트리거 | 수신자 | DB id | Solapi 상태 | 코드 위치 |
|--------|--------|--------|-------|------------|----------|
| 가입 안내 (학생) | `registration_approved_student` | 학생 | 3 | **APPROVED** | `services.py:send_welcome_messages` |
| 가입 안내 (학부모) | `registration_approved_parent` | 학부모 | 4 | **APPROVED** | `services.py:send_welcome_messages` |
| 임시 비밀번호 (학생) | `password_reset_student` | 학생 | 102 | **APPROVED** | `policy.py:send_alimtalk_via_owner` |
| 임시 비밀번호 (학부모) | `password_reset_parent` | 학부모 | 107 | **APPROVED** | `policy.py:send_alimtalk_via_owner` |
| 비밀번호 찾기 인증번호 | `password_find_otp` | 학생 | 112 | **APPROVED** | `policy.py:send_alimtalk_via_owner` |
| 수동 발송 | (관리자 UI) | 선택 대상 | - | (자유양식) | `views.py:SendMessageView` |

### B. 통합 템플릿 (APPROVED, 2026-04-08 승인) — `UNIFIED_TEMPLATES_ENABLED=True` 활성화 완료

> 아래 트리거들은 통합 4종 템플릿으로 커버됨. 승인 후 개별 KA01TP260324... 템플릿은 불필요.

| 통합 타입 | 커버 트리거 |
|-----------|-----------|
| **attendance** | `lecture_session_reminder`, `check_in_complete`, `absent_occurred` |
| **score** | `exam_*`, `retake_assigned`, `assignment_*`, `monthly_report_generated` |
| **clinic_info** | `clinic_reservation_created`, `clinic_reminder`, `clinic_check_*`, `clinic_absent`, `clinic_self_study_completed`, `clinic_result_notification`, `counseling_reservation_created` |
| **clinic_change** | `clinic_reservation_changed`, `clinic_cancelled` |

#### 레거시 수동 발송 (자유양식 카테고리별, 개별 승인)

| 카테고리 | 템플릿명 | DB id | Solapi ID | 변수 | 링크 목적지 |
|----------|---------|-------|-----------|------|-----------|
| grades | 성적표 안내 | 237 | `KA01TP2603240518351...` | 학원명, 학생이름2, 강의명, 차시명, **시험성적**, 사이트링크 | `/student/grades` |
| clinic | 클리닉 결과 안내 | 259 | `KA01TP2603240518355...` | 학원명, 학생이름2, **클리닉명**, **클리닉합불**, 사이트링크 | `/student/clinic` |
| attendance | 수업 안내 | 146 | `KA01TP2603240518359...` | 학원명, 학생이름2, **공지내용**, 사이트링크 | `/student/sessions` |
| attendance | 출결 안내 | 238 | `KA01TP2603240518363...` | 학원명, 학생이름2, **공지내용**, 사이트링크 | `/student/attendance` |
| exam | 시험 안내 | 147 | `KA01TP2603240518367...` | 학원명, 학생이름2, **공지내용**, 사이트링크 | `/student/exams` |
| assignment | 과제 안내 | 148 | `KA01TP2603240518371...` | 학원명, 학생이름2, **공지내용**, 사이트링크 | `/student/submit/assignment` |
| clinic | 클리닉 안내 | 239 | `KA01TP2603240518375...` | 학원명, 학생이름2, **공지내용**, 사이트링크 | `/student/clinic` |
| payment | 수납 안내 | 149 | `KA01TP2603240518379...` | 학원명, 학생이름2, **공지내용**, 사이트링크 | (대시보드) |
| notice | 공지사항 안내 | 144 | `KA01TP2603240518388...` | 학원명, 학생이름2, **공지내용**, 사이트링크 | `/student/notices` |

#### 이벤트 기반 자동 발송 (코드 구현 필요)

| 이벤트 | 트리거 | 수신자 | DB id | Solapi ID | 변수 | 링크 목적지 | 코드 구현 |
|--------|--------|--------|-------|-----------|------|-----------|----------|
| 수업 시작 알림 | `lecture_session_reminder` | 학생 | 243 | `KA01TP2603240519279...` | 학원명, 학생이름2, 강의명, 차시명 | `/student/sessions` | **미구현** |
| 입실 완료 | `check_in_complete` | 학부모 | 244 | `KA01TP2603240519284...` | 학원명, 학생이름2, 강의명, 차시명, 날짜, 시간 | `/student/attendance` | **미구현** |
| 결석 발생 | `absent_occurred` | 학부모 | 245 | `KA01TP2603240519288...` | 학원명, 학생이름2, 강의명, 차시명 | `/student/attendance` | **미구현** |
| 시험 예정 | `exam_scheduled_days_before` | 학생 | 246 | `KA01TP2603240519292...` | 학원명, 학생이름2, 시험명, 강의명 | `/student/exams` | **미구현** |
| 시험 미응시 | `exam_not_taken` | 학생 | 248 | `KA01TP2603240519297...` | 학원명, 학생이름2, 시험명, 강의명 | `/student/exams/:id` | **미구현** |
| 재시험 대상 | `retake_assigned` | 학생 | 250 | `KA01TP2603240519301...` | 학원명, 학생이름2, 시험명, 강의명 | `/student/exams` | **미구현** |
| 새 과제 등록 | `assignment_registered` | 학생 | 251 | `KA01TP2603240519305...` | 학원명, 학생이름2, 과제명, 강의명 | `/student/submit/assignment` | **미구현** |
| 과제 미제출 | `assignment_not_submitted` | 학생 | 253 | `KA01TP2603240519309...` | 학원명, 학생이름2, 과제명, 강의명 | `/student/submit/assignment` | **미구현** |
| 성적 공개 | `exam_score_published` | 학부모 | 249 | `KA01TP2603240519313...` | 학원명, 학생이름2, 시험명, 강의명, 시험성적 | `/student/grades` | **미구현** |
| 클리닉 시작 | `clinic_reminder` | 학생 | 255 | `KA01TP2603240519335...` | 학원명, 학생이름2, 클리닉명 | `/student/clinic` | **스텁** |
| 결제 완료 | `payment_complete` | 학부모 | 261 | `KA01TP2603240519317...` | 학원명, 학생이름2 | (대시보드) | **미구현** |
| 납부 예정 | `payment_due_days_before` | 학부모 | 262 | `KA01TP2603240519322...` | 학원명, 학생이름2 | (대시보드) | **미구현** |
| 반 등록 완료 | `class_enrollment_complete` | 학부모 | 266 | `KA01TP2603240519326...` | 학원명, 학생이름2 | `/student/sessions` | **미구현** |
| 퇴원 처리 | `withdrawal_complete` | 학부모 | 242 | `KA01TP2603240519330...` | 학원명, 학생이름2 | - | **미구현** |
| ~~긴급 공지~~ | ~~`urgent_notice`~~ | - | - | - | - | - | **삭제** (카카오 정책 위반) |

#### Solapi 미등록 (DB만 존재, 등록 보류)

| 이벤트 | DB id | 사유 |
|--------|-------|------|
| 과제 마감 임박 | 252 | 미등록 (과제 미제출과 중복성) |
| 시험 시작 알림 | 247 | 미등록 (시험 예정과 중복성) |
| 클리닉 예약 완료/변경 | 256, 257 | 미등록 (클리닉 안내 자유양식으로 대체) |
| 자율학습 완료 | 258 | 미등록 (클리닉 안내 자유양식으로 대체) |
| 상담 예약 완료 | 260 | 미등록 (클리닉 안내 자유양식으로 대체) |
| 월간 성적 리포트 | 254 | 미등록 (성적표 안내로 대체) |
| 성적 안내 (freeform) | 263 | 미등록 (성적표 안내로 대체) |
| 보충수업 안내 | 264 | 미등록 (클리닉 안내로 대체) |

---

## 4. 링크 목적지 검증

### 학생 앱 라우트 존재 여부

| 링크 경로 | 라우트 존재 | 페이지 설명 |
|----------|-----------|-----------|
| `/student/dashboard` | ✅ | 홈 대시보드 |
| `/student/grades` | ✅ | 성적 확인 |
| `/student/exams` | ✅ | 시험 목록 |
| `/student/exams/:id` | ✅ | 시험 상세/응시 |
| `/student/exams/:id/result` | ✅ | 시험 결과 |
| `/student/submit/assignment` | ✅ | 과제 제출 |
| `/student/sessions` | ✅ | 수업 일정 |
| `/student/attendance` | ✅ | 출결 현황 |
| `/student/clinic` | ✅ | 클리닉 예약 |
| `/student/notices` | ✅ | 공지사항 |

### 현재 링크 동작

**현재:** `#{사이트링크}` = `https://hakwonplus.com` (도메인만)
→ 클릭 시 로그인 → `/student/dashboard`로 리다이렉트
→ **딥링크 미지원** (해당 정보 페이지로 바로 이동 불가)

**향후 개선:** 이벤트별 딥링크 경로 추가 가능 (예: `https://hakwonplus.com/student/grades`)
- 프론트 라우터 이미 존재하므로 백엔드에서 경로만 추가하면 됨

---

## 5. 렌더 예시

### 성적표 안내 (승인 후)

```
안녕하세요, HakwonPlus입니다.
홍길학생님, 성적표 안내드립니다.

수학 심화반 · 12차시 (03/24)

[시험]
- 단원평가 3회: 80/100 (80%) 합격
- 실전 모의고사: 55/100 (55%) 불합격

[과제]
- 교재 p.52~60: 85/100 (85%) 합격
- 오답노트 제출: 72/100 (72%) 불합격

클리닉 필요: 실전 모의고사, 오답노트 제출

상세 결과는 앱에서 확인하실 수 있습니다.
https://hakwonplus.com
```

### 클리닉 결과 안내 (승인 후)

```
안녕하세요, HakwonPlus입니다.
홍길학생님, 클리닉 결과 안내드립니다.

수학 보충
결과: 합격

상세 내용은 앱에서 확인하실 수 있습니다.
https://hakwonplus.com
```

### 결석 발생 알림 (자동발송, 승인 후)

```
안녕하세요, HakwonPlus입니다.
홍길학생님의 수업에 결석이 발생하였습니다.

수학 심화반 · 12차시 (03/24)

사유가 있으시면 학원으로 연락 부탁드립니다.
https://hakwonplus.com
```

### 공지사항 안내 (자유양식, 승인 후)

```
안녕하세요, HakwonPlus입니다.
홍길학생님, 안내 말씀드립니다.

내일(3/25) 수학 수업은 강사 사정으로
오후 3시에서 오후 5시로 변경됩니다.

https://hakwonplus.com
```

---

## 6. 자동발송 On/Off 토글 (구현 예정)

### 설계

선생앱 각 이벤트 발생 지점(출결, 시험, 과제, 클리닉)에서:
- `AutoSendConfig`의 `enabled` 필드와 연동
- 토글 ON → 해당 이벤트 발생 시 자동 알림톡 발송
- 토글 OFF → 발송 안 함
- 토글 옆 미리보기 버튼 → 실제 변수 치환된 메시지 팝업

### AutoSendConfig 확장 필요

현재 동작 중인 트리거: 5개 (가입/비밀번호)
구현 필요 트리거: 15개 (출결/시험/과제/성적/클리닉/수납/공지)

---

## 7. 승인 후 즉시 사용 체크리스트

### 통합 템플릿 활성화 (출결/시험/과제/클리닉)

1. [x] Solapi에서 4개 통합 템플릿 APPROVED 확인 (API 조회) — 2026-04-08 완료
2. [x] `alimtalk_content_builders.py`에서 `UNIFIED_TEMPLATES_ENABLED = True` 변경 — 2026-04-08 완료
3. [ ] 배포 (git push → CI/CD)
4. [ ] 테스트 발송 → 수신 확인 (출결, 클리닉, 성적 각 1건)
5. [ ] 레거시 개별 템플릿(KA01TP260324...) 솔라피에서 삭제 (선택)

### 수동 발송 (자유양식)

1. [ ] Solapi 검수 승인 확인
2. [ ] DB `solapi_status` → `APPROVED` 업데이트
3. [ ] `resolve_freeform_template()` 반환 확인
4. [ ] 프론트 SendMessageModal 알림톡 모드 발송 테스트

---

## 8. 외부 의존성

| 항목 | 상태 | 필요 조치 |
|------|------|----------|
| 통합 템플릿 4종 카카오 검수 | **APPROVED** (2026-04-08) | `UNIFIED_TEMPLATES_ENABLED=True` 활성화 완료 |
| 자유양식 카테고리 템플릿 | APPROVED (15개) / INSPECTING (5개) | — |
| 자동발송 코드 | 출결/클리닉 **구현 완료**, 시험/과제/결제 미구현 | 트리거별 구현 |
| 딥링크 | 미구현 | 이벤트별 경로 추가 |
