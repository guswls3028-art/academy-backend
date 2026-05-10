# Identifier SSOT (식별자 단일 진실)

> 최종 갱신: 2026-03-20
> 상태: FK 전환 20필드 완료, DB constraint 19건 적용, 운영 E2E 검증 통과

## 식별자 목록

| 식별자 | 소속 테이블 | 테넌트 스코프 | FK 상태 |
|--------|------------|-------------|---------|
| `student_id` | `students_student` | YES | FK |
| `enrollment_id` | `enrollment_enrollment` | YES | FK (전환 완료) |
| `lecture_id` | `lectures_lecture` | YES | FK |
| `session_id` (lectures) | `lectures_session` | YES | FK |
| `session_id` (clinic) | `clinic_session` | YES | FK |
| `exam_id` | `exams_exam` | YES | FK (전환 완료) |
| `tenant_id` | `core_tenant` | - | FK (전환 완료) |
| `attempt_id` | `results_exam_attempt` | - | FK (전환 완료) |
| `question_id` | `exams_question` | - | FK (전환 완료) |

## 금지 패턴 (CI 강제)

### 1. ID 도메인 혼동 — 프론트 discriminated union 필수
```typescript
// FORBIDDEN
selected.map(id => createParticipant({ enrollment_id: id }))

// REQUIRED
selected.map(id => createParticipant(
  buildParticipantPayload(sessionId, id, selection, reason)
))
```
**강제:** `shared/types/selection.ts`의 `SelectionResult` discriminated union 사용.
**탐지:** `scripts/lint-id-safety.cjs` (UNTYPED_ID_ARRAY, UNQUALIFIED_ID_CALLBACK)

### 2. 암묵적 자동 추론 — 백엔드 에러 반환 필수
```python
# FORBIDDEN: 유효하지 않은 enrollment_id를 조용히 다른 것으로 대체
if enrollment_obj is None:
    enrollment_obj = Enrollment.objects.filter(student=student).first()

# REQUIRED: 명시적 입력 실패 시 에러
if enrollment_id and enrollment_obj is None:
    return Response({"detail": "수강 정보를 찾을 수 없습니다."}, status=400)
```
**탐지:** `scripts/lint/check_id_domain_safety.py` (SILENT_FALLBACK)

### 3. 새 정수 FK 추가 금지
```python
# FORBIDDEN: IntegerField로 FK 흉내
enrollment_id = models.PositiveIntegerField()

# REQUIRED: 실제 ForeignKey
enrollment = models.ForeignKey("enrollment.Enrollment", on_delete=models.CASCADE)
```
**강제:** CI exit code 1 (allowlist에 없는 새 IntegerField `_id` 필드)

### 4. `.first()` 사용 시 ordering 필수
```python
# FORBIDDEN
Enrollment.objects.filter(student=s, tenant=t).first()

# REQUIRED (PK/unique 조회 제외)
Enrollment.objects.filter(student=s, tenant=t).order_by("-enrolled_at", "-id").first()
```
**탐지:** UNORDERED_FIRST (PK/unique 조회는 자동 제외)

## enrollment_id 사용 규칙

1. URL path로 받은 enrollment_id는 **`enrollment_tenant_guard` 필수** 적용
2. enrollment_id와 student_id가 함께 제공되면 **교차검증** 필수
3. 명시적 enrollment_id가 유효하지 않으면 **에러 반환** (자동 대체 금지)
4. enrollment_id 없이 student_id만 있을 때는 auto-match 허용 (ordering 명시)

## FK 전환 현황

### 전환 완료 (20필드, DB constraint 19건)
| 도메인 | 필드 | on_delete | Migration |
|--------|------|-----------|-----------|
| video.TranscodeJob | tenant | CASCADE | 0010 |
| video.OpsEvent | tenant | SET_NULL | 0010 |
| video.VideoLike | tenant | CASCADE | 0010 |
| video.VideoComment | tenant | CASCADE | 0010 |
| clinic.SessionParticipant | enrollment | SET_NULL | 0008 |
| results.Result | enrollment, attempt | SET_NULL | 0006 |
| results.ResultFact | enrollment, attempt | CASCADE | 0006 |
| results.ExamAttempt | exam, enrollment | CASCADE | 0006 |
| results.ResultItem | question | CASCADE | 0006 |
| homework.HomeworkEnrollment | session, enrollment | CASCADE | 0003 |
| homework.HomeworkAssignment | enrollment | CASCADE | 0003 |
| homework_results.HomeworkScore | enrollment | CASCADE | 0005 |
| progress.SessionProgress | enrollment | CASCADE | 0002 |
| progress.LectureProgress | enrollment | CASCADE | 0002 |
| progress.ClinicLink | enrollment | CASCADE | 0003 |
| progress.RiskLog | enrollment | CASCADE | 0003 |

### 미전환 (allowlist 관리, CI 차단)
| 분류 | 건수 | 이유 |
|------|------|------|
| polymorphic FK (target_id) | 3 | target_type+target_id 패턴, ContentType 전환 필요 |
| full orphan (submission_id 등) | 4 | ID 체계 구조 불일치 |
| temp/draft | 6 | 임시 데이터, 낮은 위험 |
| video ops/perf | 2 | 로그 보존, 대량 insert 성능 |
| 기타 | 5 | AI view 로컬 변수, 감사용 user_id 등 |

## lint 도구

| 도구 | 위치 | 역할 |
|------|------|------|
| `check_id_domain_safety.py` | `backend/scripts/lint/` | INTEGER_FK_CANDIDATE(error), UNORDERED_FIRST(warn), SILENT_FALLBACK(warn) |
| `integer_fk_allowlist.txt` | `backend/scripts/lint/` | 기존 정수FK 허용 목록 (신규 추가 시 CI 실패) |
| `lint-id-safety.cjs` | `frontend/scripts/` | UNTYPED_ID_ARRAY(warn), GENERIC_IDS_RETURN(warn), UNQUALIFIED_ID_CALLBACK(warn) |
