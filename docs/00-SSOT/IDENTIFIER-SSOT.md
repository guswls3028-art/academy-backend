# Identifier SSOT (식별자 단일 진실)

> 프로젝트 전체에서 사용하는 식별자의 도메인, 의미, 사용 규칙을 정의한다.

## 식별자 목록

| 식별자 | 타입 | 소속 테이블 | 의미 | 테넌트 스코프 |
|--------|------|------------|------|-------------|
| `student_id` | PK | `students_student` | 학생 고유 ID | YES (tenant FK) |
| `enrollment_id` | PK | `enrollment_enrollment` | 수강등록 고유 ID | YES (tenant FK) |
| `lecture_id` | PK | `lectures_lecture` | 강의 고유 ID | YES (tenant FK) |
| `session_id` (lectures) | PK | `lectures_session` | 강의 차시 고유 ID | YES (lecture→tenant) |
| `session_id` (clinic) | PK | `clinic_session` | 클리닉 세션 고유 ID | YES (tenant FK) |
| `exam_id` | PK | `exams_exam` | 시험 고유 ID | YES (sessions→lecture→tenant) |
| `post_id` | PK | `community_postentity` | 게시물 고유 ID | YES (tenant FK) |
| `tenant_id` | PK | `core_tenant` | 테넌트 고유 ID | - |
| `user_id` | PK | `auth_user` | 사용자 고유 ID | NO (글로벌) |

## 금지 패턴

### 1. ID 도메인 혼동 (ID Domain Confusion)
서로 다른 테이블의 PK를 구분 없이 같은 변수/파라미터로 사용하는 것.

**금지 예시:**
```typescript
// BAD: student.id를 enrollment_id로 전달
selected.map(id => createParticipant({ enrollment_id: id }))
// id가 mode에 따라 student.id일 수도 enrollment_id일 수도 있음
```

**올바른 예시:**
```typescript
// GOOD: mode에 따라 명확히 구분
selected.map(id => createParticipant(
  mode === "targets" ? { enrollment_id: id } : { student: id }
))
```

### 2. 암묵적 자동 추론 (Implicit Auto-Resolution)
백엔드에서 제공된 ID가 유효하지 않을 때 다른 ID로 자동 대체하는 것.

**금지 예시:**
```python
# BAD: enrollment_id가 유효하지 않으면 조용히 다른 enrollment로 대체
if enrollment_obj is None:
    enrollment_obj = Enrollment.objects.filter(student=student).first()
```

**올바른 예시:**
```python
# GOOD: 명시적 입력이 유효하지 않으면 에러 반환
if enrollment_id and enrollment_obj is None:
    return Response({"detail": "수강 정보를 찾을 수 없습니다."}, status=400)
# enrollment_id가 아예 없을 때만 auto-match
if not enrollment_id:
    enrollment_obj = Enrollment.objects.filter(student=student).first()
```

### 3. 느슨한 optional 조합 (Loose Optional Combination)
API payload에서 여러 optional ID 중 하나만 필요한데, 어떤 것을 보내야 하는지 문서화되지 않은 것.

**금지 예시:**
```python
# BAD: student, enrollment_id 둘 다 optional, 백엔드가 알아서 추론
class CreateSerializer:
    student = PrimaryKeyRelatedField(required=False)
    enrollment_id = IntegerField(required=False)
```

### 4. 정수 FK (Integer Foreign Key)
`ForeignKey` 대신 `IntegerField`로 다른 테이블의 PK를 저장하는 것.

**현황:** 28+ 필드가 이 패턴 사용 중 (results, progress, submissions, homework, clinic, video 도메인).
**위험:** DB 수준 참조 무결성 없음, CASCADE/SET_NULL 보호 없음.
**대응:** V1.2.0에서 단계적 마이그레이션 계획.

## enrollment_id 사용 규칙

1. URL path로 받은 enrollment_id는 **반드시 tenant 검증** 수행
2. enrollment_id와 student_id가 함께 제공되면 **교차검증** 필수
3. enrollment_id가 명시적으로 제공되었는데 유효하지 않으면 **에러 반환** (자동 대체 금지)
4. enrollment_id 없이 student_id만 있을 때는 **auto-match 허용** (단, `.first()` 사용 시 ordering 명시)

## 테넌트 격리 검증 필수 지점

admin 뷰에서 URL path로 받는 모든 ID는 tenant 교차검증 필수:
- `enrollment_id` → `Enrollment.objects.get(id=X, tenant=tenant)`
- `exam_id` → `Exam.objects.filter(sessions__lecture__tenant=tenant)`
- `student_id` → `Student.objects.get(id=X, tenant=tenant)`
- `session_id` → `Session.objects.get(id=X, tenant=tenant)` 또는 `lecture__tenant=tenant`
