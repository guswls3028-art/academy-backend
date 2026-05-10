# State Transition SSOT (Single Source of Truth)

**Version:** V1.1.1
**Created:** 2026-03-19
**Status:** ACTIVE

이 문서는 Academy SaaS 프로젝트의 모든 핵심 도메인 상태 전이를 정의하는 실행 가능한 기준선이다.
코드와 이 문서가 불일치하면 **코드를 수정**한다.

---

## Table of Contents

- [A. 도메인 목록 및 우선순위](#a-도메인-목록-및-우선순위)
- [B. 도메인별 상태전이 SSOT](#b-도메인별-상태전이-ssot)
  - [B1. Submission (시험/숙제 제출)](#b1-submission)
  - [B2. Video (영상 인코딩)](#b2-video)
  - [B3. VideoTranscodeJob (인코딩 작업)](#b3-videotranscodejob)
  - [B4. ExamAttempt (시험 응시)](#b4-examattempt)
  - [B5. ExamResult (시험 결과)](#b5-examresult)
  - [B6. Clinic SessionParticipant (클리닉 예약)](#b6-clinic-sessionparticipant)
  - [B7. Exam (시험 라이프사이클)](#b7-exam)
  - [B8. Homework (숙제 라이프사이클)](#b8-homework)
  - [B9. Enrollment (수강 등록)](#b9-enrollment)
  - [B10. StudentRegistrationRequest (학생 등록 신청)](#b10-studentregistrationrequest)
  - [B11. AIJobModel (AI 작업)](#b11-aijobmodel)
  - [B12. Invoice (청구서)](#b12-invoice)
  - [B13. PaymentTransaction (결제)](#b13-paymenttransaction)
  - [B14. Program.subscription_status (구독)](#b14-subscription-status)
  - [B15. VideoPlaybackSession (재생 세션)](#b15-videoplaybacksession)
  - [B16. PostEntity (커뮤니티 게시글)](#b16-postentity)
  - [B17. WrongNotePDF (오답노트 PDF)](#b17-wrongnotepdf)
  - [B18. NotificationLog (알림 발송)](#b18-notificationlog)
  - [B19. Attendance (출석)](#b19-attendance)
- [C. 현재 구현 불일치 목록](#c-현재-구현-불일치-목록)
- [D. 우선 수정 대상](#d-우선-수정-대상)
- [E. 불변조건 (Cross-Domain)](#e-불변조건-cross-domain)
- [F. 미검증 리스크](#f-미검증-리스크)

---

## A. 도메인 목록 및 우선순위

| 우선순위 | 도메인 | 상태 수 | 위험도 | 근거 |
|---------|--------|---------|--------|------|
| P0 | Submission | 9 | CRITICAL | 상태기계 존재하나 미적용 (dead code) |
| P0 | ExamResult | 2 | CRITICAL | FINAL→DRAFT 회귀 가능 |
| P0 | StudentRegistrationRequest | 3 | HIGH | approve/reject 레이스 컨디션 |
| P1 | Video | 5 | HIGH | 관리 명령어 SFU 미사용 |
| P1 | VideoTranscodeJob | 7 | HIGH | cancel/mark_dead 가드 부재 |
| P1 | ExamAttempt | 4 | MEDIUM | sync_result 원자성 부재 |
| P1 | AIJobModel | 9 | MEDIUM | job_save_failed SFU 부재 |
| P2 | Clinic SessionParticipant | 6 | MEDIUM | change_booking 전이 우회 |
| P2 | Enrollment | 3 | LOW | 단순 3상태, 제한된 변이 |
| P2 | Invoice | 6 | N/A | 미구현 (모델만 존재) |
| P2 | PaymentTransaction | 5 | N/A | 미구현 |
| P3 | Exam | 3 | LOW | DRAFT 레거시, 실질 OPEN/CLOSED |
| P3 | Homework | 3 | LOW | Exam과 동일 패턴 |
| P3 | Program.subscription_status | 4 | LOW | 런타임 변이 코드 없음 |
| P3 | VideoPlaybackSession | 4 | LOW | 잘 구현됨 |
| P3 | PostEntity | 3 | LOW | 단순 발행 흐름 |
| P3 | WrongNotePDF | 4 | LOW | 비동기 생성 작업 |
| P3 | NotificationLog | 3 | LOW | SQS 워커 관리 |
| P3 | Attendance | 10 | LOW | 분류 값, 상태기계 아님 |

---

## B. 도메인별 상태전이 SSOT

---

### B1. Submission

**모델:** `apps/domains/submissions/models/submission.py`
**상태 필드:** `status` (CharField, indexed)

#### 상태 목록

| 상태 | 의미 | 진입 조건 | 이탈 조건 |
|------|------|----------|----------|
| `SUBMITTED` | 제출됨, 처리 대기 | 사용자 제출 / FAILED 재시도 | 디스패치 시작 |
| `DISPATCHED` | 처리 파이프라인에 전달됨 | dispatcher가 선점 | AI/OMR 처리 시작 |
| `EXTRACTING` | 답안 추출 중 | AI/OMR 이미지 분석 시작 | 추출 완료 또는 실패 |
| `ANSWERS_READY` | 답안 준비 완료, 채점 대기 | 추출 완료 / 수동 입력 / 식별 완료 | 채점 시작 |
| `GRADING` | 채점 진행 중 | 채점 서비스 시작 | 채점 완료 또는 실패 |
| `DONE` | 처리 완료 | 채점 성공 | 종단 상태 |
| `FAILED` | 처리 실패 | 파이프라인 어디서든 오류 | 재시도(→SUBMITTED) |
| `NEEDS_IDENTIFICATION` | 학생 식별 필요 | AI/OMR이 학생 매칭 실패 | 수동 매칭 완료(→ANSWERS_READY) |
| `SUPERSEDED` | 재응시로 대체됨 | 새 제출이 기존 제출을 대체 | 종단 상태 |

#### 허용 전이 (STATUS_FLOW) — SSOT: `transition.py`

```
SUBMITTED      → {DISPATCHED, ANSWERS_READY, GRADING, FAILED}
DISPATCHED     → {ANSWERS_READY, NEEDS_IDENTIFICATION, FAILED}
ANSWERS_READY  → {GRADING}
GRADING        → {DONE, FAILED}
FAILED         → {SUBMITTED}
NEEDS_IDENTIFICATION → {ANSWERS_READY}
DONE           → {SUPERSEDED}
SUPERSEDED     → {} (종단)
```

**EXTRACTING**: orphan 상태. choices에만 존재하며 STATUS_FLOW에서 제외.
코드에서 설정되지 않음. DB 호환성을 위해 enum에서 제거하지 않음.

**Admin Override (manual_edit 전용):**
```
DONE/FAILED/SUBMITTED/DISPATCHED/NEEDS_IDENTIFICATION → {ANSWERS_READY}
```
GRADING/SUPERSEDED에서는 admin_override도 차단.

#### 금지 전이

- `DONE → *` (종단 상태, 절대 변경 불가)
- `SUPERSEDED → *` (종단 상태)
- `SUBMITTED → GRADING` (중간 단계 생략 금지)
- `SUBMITTED → DONE` (채점 없이 완료 금지)
- `GRADING → SUBMITTED` (재시도는 FAILED 경유 필수)

#### 전이 주체

| 전이 | 주체 |
|------|------|
| → SUBMITTED | 사용자(제출) / 관리자(재시도) |
| → DISPATCHED | dispatcher 서비스 |
| → EXTRACTING | AI/OMR 워커 |
| → ANSWERS_READY | AI/OMR 워커 / 관리자(수동입력) |
| → GRADING | grader 서비스 |
| → DONE | grader 서비스 |
| → FAILED | 모든 처리 단계 (오류 시) |
| → NEEDS_IDENTIFICATION | AI/OMR 워커 |
| → SUPERSEDED | attempt 서비스 (재응시 시) |

#### 실패 상태 & 재시도

- **FAILED:** 재시도 가능. FAILED → SUBMITTED 전이 후 재처리.
- **재시도 주체:** 관리자 (수동)
- **재시도 제한:** 없음 (무한 재시도 가능 — 제한 추가 권장)
- **자동 재시도:** 없음 (수동만)

#### UI 허용 액션

| 상태 | Admin | Student |
|------|-------|---------|
| SUBMITTED | 취소 | 대기 표시 |
| DISPATCHED | 상태 확인 | (미노출) |
| EXTRACTING | 상태 확인 | (미노출) |
| ANSWERS_READY | 수동 채점, 답안 편집 | (미노출) |
| GRADING | 상태 확인 | (미노출) |
| DONE | 결과 확인, 재채점 | 결과 확인 |
| FAILED | 재시도, 상세 확인 | (미노출, 결과 없음으로 표시) |
| NEEDS_IDENTIFICATION | 학생 매칭 | (미노출) |
| SUPERSEDED | 이력 확인 | (미노출) |

#### 백엔드 불변조건

1. **can_transit() 강제:** 모든 상태 변경은 `Submission.can_transit(from, to)`를 통과해야 함
2. **원자성:** 모든 상태 변경은 `@transaction.atomic` + `select_for_update` 내에서 수행
3. **테넌트 격리:** `submission.tenant_id` 일치 검증 필수
4. **단일 활성 제출:** `unique_active_submission_per_target` 제약조건 준수
5. **DONE/SUPERSEDED 불변:** 종단 상태에 도달한 submission은 절대 상태 변경 불가

#### 로그/감사 포인트

- 모든 상태 전이 시 `error_message` 기록 (실패 시)
- ResultFact append-only 로그 (채점 결과)

#### E2E 테스트 시나리오

1. 정상 플로우: SUBMITTED → DISPATCHED → ANSWERS_READY → GRADING → DONE
2. AI 실패 → FAILED → 재시도 → SUBMITTED → ... → DONE
3. 식별 실패 → NEEDS_IDENTIFICATION → 수동 매칭 → ANSWERS_READY → DONE
4. 재응시: 기존 DONE → SUPERSEDED, 새 SUBMITTED 생성
5. 금지 전이 시도 시 ValidationError 발생 확인
6. 동시 상태 변경 시 select_for_update 경합 확인

---

### B2. Video

**모델:** `apps/support/video/models.py`
**상태 필드:** `status` (CharField, indexed)

#### 상태 목록

| 상태 | 의미 | 진입 조건 | 이탈 조건 |
|------|------|----------|----------|
| `PENDING` | 업로드 대기 | presigned URL 생성 | upload-complete 호출 |
| `UPLOADED` | 업로드 완료, 인코딩 대기 | S3 파일 확인 + ffprobe | 작업 생성 및 처리 시작 |
| `PROCESSING` | 인코딩 진행 중 | 워커가 작업 시작 | 인코딩 완료 또는 실패 |
| `READY` | 사용 가능 | HLS 출력 완료 | 종단 상태 (soft-delete만 가능) |
| `FAILED` | 실패 | 최대 재시도 초과 / 복구 불가 | 재시도(→UPLOADED) |

#### 허용 전이

```
PENDING    → {UPLOADED, FAILED}
UPLOADED   → {PROCESSING, READY, FAILED}
PROCESSING → {READY, FAILED, UPLOADED}
FAILED     → {UPLOADED}
READY      → {} (종단, soft-delete만)
```

#### 금지 전이

- `READY → *` (종단 상태)
- `FAILED → PROCESSING` (UPLOADED 경유 필수)
- `PENDING → PROCESSING` (UPLOADED 경유 필수)
- `PENDING → READY` (인코딩 없이 완료 불가)

#### 전이 주체

| 전이 | 주체 |
|------|------|
| → PENDING | upload/init API |
| → UPLOADED | upload/complete API / recover_stuck_videos / retry API |
| → PROCESSING | (레거시 — 현재 job.state=RUNNING으로 대체) |
| → READY | job_complete() |
| → FAILED | job_mark_dead() / recover_stuck_videos |

#### 실패 상태 & 재시도

- **FAILED:** 재시도 가능. retry API → UPLOADED → 새 작업 생성
- **재시도 제한:** `VIDEO_MAX_JOBS_PER_VIDEO` (기본 10)
- **자동 재시도:** `enqueue_uploaded_videos` cron (10분 간격)
- **자동 복구:** `recover_stuck_videos` cron (30분 간격, PENDING 정체 복구)

#### UI 허용 액션

| 상태 | Admin | Student |
|------|-------|---------|
| PENDING | 업로드 재시도 (file_key 있을 때) | (미노출 또는 "업로드 중") |
| UPLOADED | 대기 표시, 재시도 | "처리 중" 오버레이 필요 |
| PROCESSING | 진행률 표시, 재시도 | "처리 중" 오버레이 |
| READY | 재생, 삭제, 설정 변경 | 재생 |
| FAILED | 재시도, 삭제, 오류 상세 | "실패" 표시, 재생 불가 |

#### 백엔드 불변조건

1. **1 Video = 1 Active Job:** DynamoDB lock + DB unique constraint
2. **테넌트 동시 실행 제한:** `VIDEO_TENANT_MAX_CONCURRENT`
3. **원자성:** job_complete, job_mark_dead는 Video.status + Job.state를 동일 트랜잭션에서 변경
4. **Soft-delete 보존:** deleted_at ≠ null인 영상은 180일간 복구 가능

#### E2E 테스트 시나리오

1. 정상: PENDING → UPLOADED → (Job:QUEUED→RUNNING→SUCCEEDED) → READY
2. 업로드 실패: PENDING → (타임아웃) → recover_stuck_videos → FAILED
3. 인코딩 실패: PROCESSING → Job:RETRY_WAIT → (5회 실패) → Job:DEAD → FAILED
4. 재시도: FAILED → retry API → UPLOADED → 새 Job → READY
5. 동시성: 같은 영상에 동시 retry → DDB lock으로 1개만 성공

---

### B3. VideoTranscodeJob

**모델:** `apps/support/video/models.py`
**상태 필드:** `state` (CharField, indexed)

#### 상태 목록

| 상태 | 의미 | 진입 조건 | 이탈 조건 |
|------|------|----------|----------|
| `QUEUED` | 대기 중 | 작업 생성 | 워커 선점 |
| `RUNNING` | 실행 중 | 워커가 선점 | 완료/실패/하트비트 만료 |
| `SUCCEEDED` | 완료 | 인코딩 성공 | 종단 상태 |
| `FAILED` | 실패 (재시도 가능할 수 있음) | 인코딩 오류 | 종단 상태 |
| `RETRY_WAIT` | 재시도 대기 | 실패 + attempt_count < max | 다시 QUEUED로 선점 |
| `DEAD` | 격리 (최종 실패) | max attempts 초과 / 수동 | 종단 상태 |
| `CANCELLED` | 취소됨 | 사용자/시스템 취소 | 종단 상태 |

#### 허용 전이

```
QUEUED     → {RUNNING, CANCELLED, DEAD}
RUNNING    → {SUCCEEDED, FAILED, RETRY_WAIT, CANCELLED, DEAD}
RETRY_WAIT → {RUNNING, DEAD, CANCELLED}
SUCCEEDED  → {} (종단)
FAILED     → {} (종단)
DEAD       → {} (종단)
CANCELLED  → {} (종단)
```

#### 금지 전이

- `SUCCEEDED → *` (종단 상태, 절대 덮어쓰기 금지)
- `DEAD → *` (종단 상태)
- `CANCELLED → *` (종단 상태)
- `FAILED → *` (종단 상태)

#### 전이 주체

| 전이 | 주체 |
|------|------|
| → QUEUED | video encoding service |
| → RUNNING | daemon/batch worker (job_set_running) |
| → SUCCEEDED | worker (job_complete) |
| → RETRY_WAIT | worker (job_fail_retry) / scan_stuck_video_jobs |
| → DEAD | scan_stuck_video_jobs / retry API (stale detection) |
| → CANCELLED | retry API (기존 작업 취소) / 영상 삭제 |

#### 백엔드 불변조건

1. **종단 상태 불변:** SUCCEEDED, FAILED, DEAD, CANCELLED에 도달한 job은 state 변경 불가
2. **하트비트:** RUNNING 상태 job은 60초마다 하트비트 갱신 필수
3. **정체 감지:** scan_stuck_video_jobs가 하트비트 만료 RUNNING job을 RETRY_WAIT로 전이

---

### B4. ExamAttempt

**모델:** `apps/domains/results/models/exam_attempt.py`
**상태 필드:** `status` (CharField)

#### 상태 목록

| 상태 | 의미 | 진입 조건 | 이탈 조건 |
|------|------|----------|----------|
| `pending` | 생성됨, 채점 대기 | attempt 생성 | 채점 시작 |
| `grading` | 채점 진행 중 | grader가 채점 시작 | 채점 완료/실패 |
| `done` | 채점 완료 | 채점 성공 | 종단 상태 |
| `failed` | 채점 실패 | 채점 오류 | 재채점(→grading) |

#### 허용 전이

```
pending  → {grading}
grading  → {done, failed}
failed   → {grading}
done     → {} (종단)
```

#### 금지 전이

- `done → *` (종단 상태)
- `pending → done` (grading 경유 필수)
- `pending → failed` (grading 경유 필수)

#### 불변조건

1. **Append-only:** 기존 attempt 수정 금지 (새 attempt 생성)
2. **is_representative:** 하나의 (exam_id, enrollment_id)에 대해 정확히 1개만 True
3. **attempt_index:** 동일 (exam_id, enrollment_id)에서 단조 증가
4. **원자성:** 모든 상태 변경은 `@transaction.atomic` 내에서 수행

---

### B5. ExamResult

**모델:** `apps/domains/results/models/exam_result.py`
**상태 필드:** `status` (CharField)

#### 상태 목록

| 상태 | 의미 | 진입 조건 | 이탈 조건 |
|------|------|----------|----------|
| `DRAFT` | 초안 (편집 가능) | 채점 결과 생성 | 확정 |
| `FINAL` | 확정 (불변) | finalize() 호출 | 종단 상태 |

#### 허용 전이

```
DRAFT → {FINAL}
FINAL → {} (종단, 절대 불변)
```

#### 금지 전이

- **`FINAL → DRAFT` (절대 금지)** — 확정된 결과는 어떤 경우에도 DRAFT로 돌아갈 수 없음
- **`FINAL → FINAL` (멱등, 허용)** — finalize() 중복 호출은 무해

#### 불변조건

1. **FINAL 불변성:** status=FINAL인 ExamResult의 score, breakdown, manual_overrides는 수정 불가
2. **finalized_at:** FINAL 전이 시 반드시 설정
3. **학생 노출:** Student API는 status=FINAL인 결과만 반환해야 함

---

### B6. Clinic SessionParticipant

**모델:** `apps/domains/clinic/models.py`
**상태 필드:** `status` (CharField)

#### 상태 목록

| 상태 | 의미 | 진입 조건 | 이탈 조건 |
|------|------|----------|----------|
| `pending` | 예약 대기 | 학생 신청 | 승인/거절 |
| `booked` | 예약 확정 | 관리자 승인 / 직접 등록 | 출석/미출석/취소 |
| `attended` | 출석 완료 | 체크인 | 종단 상태 |
| `no_show` | 미출석 | 세션 종료 후 판정 | 종단 상태 |
| `cancelled` | 취소됨 | 학생/관리자 취소 | 종단 상태 |
| `rejected` | 거절됨 | 관리자 거절 | 종단 상태 |

#### 허용 전이 (VALID_TRANSITIONS)

```python
# apps/domains/clinic/views.py:647-662
VALID_TRANSITIONS = {
    "pending":   {"booked", "cancelled", "rejected"},
    "booked":    {"attended", "no_show", "cancelled"},
    "attended":  {"booked"},          # 출석 취소 (오입력 정정)
    "no_show":   {"booked", "attended"},  # 미출석 정정
    "cancelled": {"pending", "booked"},   # 취소 복원
    "rejected":  {"pending", "booked"},   # 거절 복원
}
```

#### 금지 전이

- `pending → attended` (booked 경유 필수)
- `pending → no_show` (booked 경유 필수)
- `booked → pending` (승인 취소 없음)
- `booked → rejected` (승인된 예약 거절 없음)

#### UI 허용 액션

| 상태 | Admin | Student |
|------|-------|---------|
| pending | 승인(→booked), 거절(→rejected), 취소(→cancelled) | 취소(→cancelled) |
| booked | 출석(→attended), 미출석(→no_show), 취소(→cancelled) | (변경 불가, "확정" 표시) |
| attended | 출석취소(→booked) | (미노출) |
| no_show | 정정(→booked, →attended) | (미노출) |
| cancelled | 복원(→pending, →booked) | (미노출) |
| rejected | 복원(→pending, →booked) | (미노출 — 현재 코드 갭) |

---

### B7. Exam

**모델:** `apps/domains/exams/models/exam.py`
**상태 필드:** `status` (CharField, indexed)

#### 상태 목록

| 상태 | 의미 |
|------|------|
| `DRAFT` | 초안 (레거시 — 사실상 사용 안 함) |
| `OPEN` | 진행 중 (기본값) |
| `CLOSED` | 마감 |

#### 허용 전이

```
DRAFT  → {OPEN}
OPEN   → {CLOSED}
CLOSED → {OPEN}  (재개방 허용)
```

#### 비고

- DRAFT는 레거시. 새 시험은 항상 OPEN으로 생성됨.
- 실질적 응시 가능 판단: `open_at`/`close_at` 시간 + status=OPEN
- `answer_visibility`: HIDDEN / AFTER_CLOSED / ALWAYS (시험 상태와 독립적으로 정답 공개 제어)

---

### B8. Homework

**모델:** `apps/domains/homework_results/models/homework.py`
**상태 필드:** `status` (CharField, indexed)

Exam과 동일한 DRAFT / OPEN / CLOSED 패턴. 전이 규칙 동일.

---

### B9. Enrollment

**모델:** `apps/domains/enrollment/models.py`
**상태 필드:** `status` (CharField)

#### 상태 목록

| 상태 | 의미 |
|------|------|
| `ACTIVE` | 수강 중 (기본값) |
| `INACTIVE` | 비활성 (퇴원/중단) |
| `PENDING` | 대기 (등록 승인 전) |

#### 허용 전이

```
PENDING  → {ACTIVE, INACTIVE}
ACTIVE   → {INACTIVE}
INACTIVE → {ACTIVE}  (재등록)
```

---

### B10. StudentRegistrationRequest

**모델:** `apps/domains/students/models.py`
**상태 필드:** `status` (CharField, indexed)

#### 상태 목록

| 상태 | 의미 |
|------|------|
| `pending` | 대기 (기본값) |
| `approved` | 승인됨 (학생 계정 생성) |
| `rejected` | 거절됨 |

#### 허용 전이

```
pending  → {approved, rejected}
approved → {} (종단)
rejected → {} (종단)
```

#### 금지 전이

- `approved → *` (이미 학생 생성됨, 롤백 불가)
- `rejected → approved` (재신청 필요)

#### 불변조건

1. **원자성 필수:** approve와 reject는 `select_for_update`로 동시 실행 방지
2. **approved → 학생 생성:** approve 시 학생 계정이 atomic하게 생성되어야 함

---

### B11. AIJobModel

**모델:** `apps/domains/ai/models.py`
**상태 필드:** `status` (CharField)

#### 상태 목록

| 상태 | 의미 | 활성 여부 |
|------|------|----------|
| `PENDING` | 대기 중 | 활성 |
| `VALIDATING` | 입력 검증 중 | (미사용 — orphan) |
| `RUNNING` | 실행 중 | 활성 |
| `DONE` | 완료 | 종단 |
| `FAILED` | 실패 | 종단 |
| `REJECTED_BAD_INPUT` | 입력 거부 | 종단 |
| `FALLBACK_TO_GPU` | GPU 폴백 | (미사용 — orphan) |
| `RETRYING` | 재시도 중 | (미사용 — orphan) |
| `REVIEW_REQUIRED` | 검토 필요 | (미사용 — orphan) |

#### 허용 전이 (실제 사용되는 것만)

```
PENDING → {RUNNING}
RUNNING → {DONE, FAILED}
```

#### 불변조건

1. **종단 상태 불변:** DONE, FAILED, REJECTED_BAD_INPUT에 도달한 job은 상태 변경 불가
2. **하트비트:** RUNNING 상태에서 lease 만료 감지

---

### B12. Invoice

**모델:** `apps/billing/models.py`
**상태 필드:** `status` (CharField, indexed)

#### 상태 목록 (설계, 미구현)

| 상태 | 의미 |
|------|------|
| `SCHEDULED` | 예정 |
| `PENDING` | 결제 대기 |
| `PAID` | 결제 완료 |
| `FAILED` | 결제 실패 |
| `OVERDUE` | 연체 |
| `VOID` | 무효 |

**현재 상태:** 런타임 코드에서 상태 변이 없음. 모델만 정의됨.

---

### B13. PaymentTransaction

**모델:** `apps/billing/models.py`
**현재 상태:** 런타임 코드에서 상태 변이 없음. 모델만 정의됨.

---

### B14. Subscription Status

**모델:** `apps/core/models/program.py`
**상태 필드:** `subscription_status` (CharField, indexed)

#### 상태 목록

| 상태 | 의미 |
|------|------|
| `active` | 활성 구독 |
| `expired` | 만료 |
| `grace` | 유예 기간 |
| `cancelled` | 해지 |

**현재 상태:** 마이그레이션에서만 초기값 설정. 런타임 변이 코드 없음. 402 오버레이가 `is_subscription_active` 기반으로 동작.

---

### B15. VideoPlaybackSession

**모델:** `apps/support/video/models.py`
**상태 필드:** `status` (CharField, indexed)

#### 상태 목록

| 상태 | 의미 |
|------|------|
| `ACTIVE` | 재생 중 |
| `ENDED` | 정상 종료 |
| `REVOKED` | 위반으로 차단 |
| `EXPIRED` | 타임아웃 만료 |

#### 허용 전이

```
ACTIVE  → {ENDED, REVOKED, EXPIRED}
ENDED   → {} (종단)
REVOKED → {} (종단)
EXPIRED → {} (종단)
```

---

### B16. PostEntity

**모델:** `apps/domains/community/models/post.py`
**상태 필드:** `status` (CharField)

| 상태 | 의미 |
|------|------|
| `draft` | 임시 저장 |
| `published` | 게시됨 |
| `archived` | 보관됨 |

---

### B17. WrongNotePDF

**모델:** `apps/domains/results/models/wrong_note_pdf.py`
**상태 필드:** `status` (CharField)

| 상태 | 의미 |
|------|------|
| `PENDING` | 생성 대기 |
| `RUNNING` | 생성 중 |
| `DONE` | 완료 |
| `FAILED` | 실패 |

---

### B18. NotificationLog

**모델:** `apps/support/messaging/models.py`
**상태 필드:** `status` (CharField)

| 상태 | 의미 |
|------|------|
| `processing` | 워커 선점, 발송 중 |
| `sent` | 발송 완료 |
| `failed` | 발송 실패 |

#### 불변조건

- 3-layer idempotency: Redis lock + DB UniqueConstraint + transport dedup

---

### B19. Attendance

**모델:** `apps/domains/attendance/models.py`
**상태 필드:** `status` (CharField)

| 상태 | 의미 |
|------|------|
| `PRESENT` | 출석 |
| `LATE` | 지각 |
| `ONLINE` | 온라인 출석 |
| `SUPPLEMENT` | 보강 |
| `EARLY_LEAVE` | 조퇴 |
| `ABSENT` | 결석 |
| `RUNAWAY` | 이탈 |
| `MATERIAL` | 교재만 수령 |
| `INACTIVE` | 비활성 |
| `SECESSION` | 탈퇴 |

**비고:** 상태기계가 아닌 분류 값. 관리자가 자유롭게 변경 가능.

---

## C. 현재 구현 불일치 목록

### C1. CRITICAL — Submission can_transit() 미적용 → **FIXED**

- **해결:** `apps/domains/submissions/services/transition.py` SSOT 모듈 생성
- **적용:** 16개 live write path 전수 리팩터링 → 모든 상태 변경이 `transit()`/`transit_save()`를 통과
- **추가 수정:**
  - grader.py FAILED no-op 버그 수정 (transaction rollback → FAILED 미persist 문제)
  - manual_edit SUPERSEDED→ANSWERS_READY 차단
  - STATUS_FLOW 현실화 (SUBMITTED→GRADING/ANSWERS_READY 추가, DONE→SUPERSEDED 추가)
  - EXTRACTING orphan 상태 STATUS_FLOW에서 제외
  - 데드코드 정리 (progress/ai_omr_result_mapper.py)
- **테스트:** 102개 (허용 12 + 금지 63 + 종단 7 + admin override 10 + 시나리오 8 + 구조 2)

### C2. CRITICAL — ExamResult FINAL→DRAFT 회귀

- **위치:** `apps/domains/results/services/exam_grading_service.py:131`
- **문제:** `auto_grade_objective()`가 기존 ExamResult를 로드할 때 FINAL 여부를 확인하지 않고 `status = DRAFT`로 설정
- **영향:** 이미 확정된(FINAL) 시험 결과가 채점 서비스 재호출 시 DRAFT로 회귀 가능
- **위반:** ExamResult 모델 docstring "finalized 되면 불변" 계약 위반

### C3. HIGH — StudentRegistrationRequest approve/reject 레이스

- **위치:** `apps/domains/students/views.py:1774`
- **문제:** reject 경로에 `select_for_update` 없음. approve와 reject가 동시 실행 가능.
- **영향:** 학생이 생성된 후 request가 rejected로 마킹될 수 있음 (데이터 불일치)

### C4. HIGH — VideoTranscodeJob 종단 상태 덮어쓰기

- **위치:** `academy/adapters/db/django/repositories_video.py`
- **문제:**
  - `job_cancel()` — 상태 확인 없이 `→ CANCELLED` (SUCCEEDED도 덮어씀)
  - `job_mark_dead()` — 상태 확인 없이 `→ DEAD` (SUCCEEDED도 덮어씀)
  - `job_fail_retry()` — 상태 확인 없이 `→ RETRY_WAIT` (SUCCEEDED도 덮어씀)
- **비고:** `job_mark_dead_if_active()`는 올바르게 가드됨. 하지만 `job_mark_dead()`도 여전히 가드 없이 호출됨.

### C5. MEDIUM — ExamAttempt sync_result 원자성 부재

- **위치:** `apps/domains/results/services/sync_result_from_submission.py:118`
- **문제:** `attempt.status = "done"` 설정 시 `select_for_update`도 `@transaction.atomic`도 없음
- **영향:** grader와 sync가 동시 실행 시 레이스 컨디션

### C6. MEDIUM — Clinic change_booking 전이 우회

- **위치:** `apps/domains/clinic/views.py:925`
- **문제:** 기존 예약을 CANCELLED로 변경할 때 `VALID_TRANSITIONS` 검증을 거치지 않음
- **영향:** ATTENDED/NO_SHOW 상태의 예약이 CANCELLED로 변경될 수 있음

### C7. MEDIUM — AIJobModel job_save_failed SFU 부재

- **위치:** `academy/adapters/db/django/repositories_ai.py:298`
- **문제:** `job_save_failed()`에 `select_for_update` 없음
- **영향:** DONE과 FAILED가 동시에 기록될 수 있음

### C8. LOW — Frontend UPLOADED 상태 미처리 (Student)

- **위치:** `frontend/src/student/` (비디오 관련 컴포넌트)
- **문제:** Student 앱에서 UPLOADED 상태 영상에 "처리 중" 오버레이가 표시되지 않음
- **영향:** 학생이 UPLOADED 영상을 재생 가능한 것으로 오인 (실제로는 isPlayable 체크로 차단)

### C9. LOW — Frontend 클리닉 rejected 미표시 (Student)

- **위치:** `frontend/src/student/domains/clinic/pages/ClinicPage.tsx`
- **문제:** rejected 예약이 API에서 반환되지만 UI에 표시되지 않음
- **영향:** 학생이 거절 사유를 확인할 수 없음

### C10. LOW — Frontend 402 SubscriptionExpiredOverlay (Student)

- **위치:** `frontend/src/student/`
- **문제:** 402 이벤트는 발생하지만 Student 앱에 오버레이 컴포넌트가 없음
- **영향:** 구독 만료 시 학생 앱에서 적절한 안내가 표시되지 않음

### C11. INFO — AIJobModel Orphan 상태

- **위치:** `apps/domains/ai/models.py`
- **Orphan:** VALIDATING, FALLBACK_TO_GPU, RETRYING, REVIEW_REQUIRED — 런타임 코드에서 설정되지 않음

---

## D. 우선 수정 대상

| # | 불일치 | 위험도 | 수정 상태 | 수정 내용 |
|---|--------|--------|----------|----------|
| 1 | C2: ExamResult FINAL→DRAFT | CRITICAL | **FIXED** | `exam_grading_service.py`: FINAL 결과 재채점 시 early return |
| 2 | C1: Submission can_transit | CRITICAL | **FIXED** | `transition.py` SSOT 생성, 16개 write path 전수 리팩터링, 102개 테스트 |
| 3 | C3: Registration race | HIGH | **FIXED** | `students/views.py`: approve/reject/bulk_reject에 select_for_update + 상태 재확인 |
| 4 | C4: Job terminal overwrite | HIGH | **FIXED** | `repositories_video.py`: job_cancel/mark_dead/fail_retry에 종단 상태 가드 |
| 5 | C5: ExamAttempt atomic | MEDIUM | **FIXED** | `sync_result_from_submission.py`: select_for_update 추가 |
| 6 | C6: Clinic booking bypass | MEDIUM | **FIXED** | `clinic/views.py`: change_booking에 CANCEL_ALLOWED_FROM 가드 |
| 7 | C7: AI job SFU | MEDIUM | **FIXED** | `repositories_ai.py`: job_save_failed에 atomic + select_for_update + 종단 상태 가드 |

---

## E. 불변조건 (Cross-Domain)

### E1. 테넌트 격리 (절대 불변)

- 모든 상태 변경 시 `obj.tenant_id == request.tenant.id` 검증 필수
- 상태 전이 실패 시 다른 테넌트/사용자/객체로 fallback 절대 금지
- 워커에서 tenant_id는 job/submission 생성 시 고정, 런타임 변경 불가

### E2. 종단 상태 보호

- 종단 상태에 도달한 객체는 상태 변경 불가:
  - Submission: DONE, SUPERSEDED
  - ExamResult: FINAL
  - VideoTranscodeJob: SUCCEEDED, FAILED, DEAD, CANCELLED
  - ExamAttempt: done
  - VideoPlaybackSession: ENDED, REVOKED, EXPIRED

### E3. 원자성

- 모든 상태 전이는 `@transaction.atomic` 내에서 수행
- 동시 변이 가능성이 있는 객체는 `select_for_update` 사용

### E4. 운영 데이터 보호

- Tenant 1 외의 모든 테넌트 데이터는 운영 데이터
- 상태 변이 실패 시에도 데이터 삭제/초기화 금지

---

## F. 미검증 리스크

### F1. Submission EXTRACTING 상태 (Orphan)

- STATUS_FLOW에 정의되어 있지만 코드에서 설정되지 않음
- AI/OMR 파이프라인이 이 상태를 건너뛰고 DISPATCHED → ANSWERS_READY로 직행
- **리스크:** EXTRACTING이 필요한 새 파이프라인이 추가되면 STATUS_FLOW와 코드가 불일치할 수 있음

### F2. Submission SUPERSEDED 전이 경로

- SUPERSEDED는 STATUS_FLOW에 항목이 없음 (진입 경로 미정의)
- 코드에서 SUPERSEDED로 설정되는 로직의 정확한 위치 확인 필요

### F3. Video 관리 명령어 SFU 부재

- `enqueue_uploaded_videos`, `recover_stuck_videos`, `force_complete_videos`가 `select_for_update` 없이 상태 변경
- 워커와 동시 실행 시 레이스 컨디션 가능
- **리스크:** 낮음 (cron 간격이 워커 처리 시간보다 길어 실제 충돌 확률 낮음)

### F4. Billing 도메인 미구현

- Invoice, PaymentTransaction, TaxInvoiceIssue 모델이 정의되어 있지만 런타임 상태 변이 코드 없음
- 구현 시 상태 전이 가드 필수

### F5. Subscription 라이프사이클 미구현

- `Program.subscription_status`가 마이그레이션에서만 설정됨
- active → expired, active → grace → expired 자동 전이 로직 미구현
- **리스크:** 구독 만료 자동 처리가 없어 수동 관리 필요

### F6. ExamResult DRAFT 노출 가능성

- Student API가 DRAFT 결과를 필터링하는지 확인 필요
- 만약 필터링하지 않으면 채점 중인 미확정 결과가 학생에게 노출될 수 있음

### F7. Homework/Exam status DRAFT 레거시

- DRAFT 상태가 "레거시"로 표기되어 있지만 enum에서 제거되지 않음
- 새로 생성되는 시험/숙제는 OPEN이 기본이므로 DRAFT가 불필요
- **리스크:** 없음 (하위 호환성), 정리 권장

### F8. Clinic Submission vs Domains Submission 혼동

- `apps/domains/clinic/models.py`에 독립적인 Submission 모델이 존재 (pending/passed/failed)
- `apps/domains/submissions/models/submission.py`의 Submission과 이름이 동일하지만 완전히 다른 모델
- **리스크:** 코드 가독성. 네이밍 정리 권장 (ClinicSubmission 등)
