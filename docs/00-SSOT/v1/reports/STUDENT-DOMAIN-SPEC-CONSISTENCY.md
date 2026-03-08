# 학생 도메인 스펙 정합성 (사실 기준)

**생성일:** 2026-03-09  
**범위:** Student / StudentRegistrationRequest 모델 ↔ 강의·클리닉·숙제·영상·출결·API·시리얼라이저 필드 정합성

---

## 1. Truth source (진실의 원천)

- **모델:** `apps/domains/students/models.py` — `Student`, `StudentRegistrationRequest`
- **회원가입신청:** `StudentRegistrationRequest` 승인 시 `Student` + User + TenantMembership 생성. 필드 매핑은 `views._approve_registration_request` 및 `RegistrationRequestCreateSerializer` ↔ 모델 일치 유지.

---

## 2. Student 모델 필드 (SSOT)

| 필드 | 타입 | nullable | 비고 |
|------|------|----------|------|
| tenant, user | FK | N | 필수 |
| ps_number, omr_code | str | N | 학원 학생 ID, OMR 식별자 |
| name, parent_phone | str | N | 필수 |
| phone | str | Y | 학생 전화(없으면 식별자 가입) |
| uses_identifier | bool | N | True면 전화 없음, 식별자 표시 |
| school_type | str | N | HIGH / MIDDLE, default HIGH |
| high_school, middle_school, high_school_class, major | str | Y | 학교/반/전공 |
| origin_middle_school | str | Y | 출신중학교(고등 선택) |
| grade, gender | smallint/str | Y | |
| memo, address | str | Y | address 회원가입신청 추가분 |
| is_managed, profile_photo, deleted_at | bool/file/datetime | Y | |
| tags | M2M | - | through StudentTag |

---

## 3. StudentRegistrationRequest 모델 필드 (SSOT)

승인 전 신청 데이터. Student와 동일한 이름 필드 사용: name, username(희망 아이디), parent_phone, phone, school_type, high_school, middle_school, high_school_class, major, grade, gender, memo, address, origin_middle_school.  
그 외: status, initial_password, student(승인 후 생성된 Student FK).

---

## 4. 사용처별 스펙 정합성 (그랩 기준)

### 4.1 시리얼라이저

| 위치 | 용도 | 필드 정합성 |
|------|------|-------------|
| **students/serializers.py** | StudentListSerializer, StudentDetailSerializer | `fields = "__all__"` → 모델 전체 노출, 정합 |
| **students/serializers.py** | StudentCreateSerializer, StudentUpdateSerializer | `exclude = ("tenant","user")` → address, origin_middle_school 포함, 정합 |
| **students/serializers.py** | StudentBulkItemSerializer | 입력용 `school` → 서비스층에서 normalize_school_from_name으로 high_school/middle_school 변환, 정합 |
| **students/serializers.py** | RegistrationRequestCreateSerializer / ListSerializer | Create: high_school, middle_school, origin_middle_school, address 포함. List: exclude initial_password만, 정합 |
| **enrollment/serializers.py** | StudentShortSerializer | **수정 반영:** school_type, middle_school, origin_middle_school 추가, phone allow_null. 모델·프론트 mapStudent와 정합 |
| **attendance/serializers.py** | AttendanceSerializer | **수정 반영:** phone allow_null=True (Student.phone nullable과 일치) |
| **clinic/serializers.py** | ClinicSessionParticipantSerializer | student_name = source="student.name" 만 사용, 정합 |
| **video/serializers.py** | VideoAccessSerializer, VideoProgressSerializer, VideoPlaybackEventListSerializer | enrollment.student.name 만 사용, 정합 |

### 4.2 서비스/뷰

| 위치 | 용도 | 스펙 |
|------|------|------|
| **students/services/lecture_enroll.py** | get_or_create_student_for_lecture_enroll | item: name, parent_phone, phone, **school**, school_type, grade, high_school_class, major, memo, uses_identifier, gender. **school** → normalize_school_from_name → high_school/middle_school, 정합 |
| **students/views.py** | bulk conflict resolution, registration approve | student_data.get("school") → normalize_school_from_name; reg 필드 → Student 생성 시 high_school, middle_school, origin_middle_school 등 동일 이름 매핑, 정합 |

### 4.3 프론트 (academyfront)

- **mapStudent:** high_school, middle_school, school_type, origin_middle_school, parent_phone, phone(null) 사용. 백엔드 `__all__` 및 StudentShortSerializer 보강 후 일치.
- **createStudent / updateStudent:** payload에 school_type, high_school, middle_school, high_school_class, origin_middle_school 전달. 백엔드 수신 필드와 일치.
- **Registration request:** high_school, middle_school, high_school_class, origin_middle_school 등 동일 키 사용, 정합.

---

## 5. 이번 조치 요약

1. **attendance/serializers.py** — `AttendanceSerializer.phone`에 `allow_null=True` 추가 (Student.phone nullable).
2. **enrollment/serializers.py** — `StudentShortSerializer`에 `school_type`, `middle_school`, `origin_middle_school` 추가, `phone` allow_null. 강의/수강 API에서 학생 정보 노출 시 모델·프론트와 동일 스펙 유지.

---

## 6. 참고

- **학교 입력:** 엑셀/일괄 입력은 단일 필드 `school` 사용 → `normalize_school_from_name(school, school_type)` → high_school/middle_school 저장. (`apps/domains/students/services/school.py`)
- **리포지토리:** `academy/adapters/db/django/repositories_students.py` — student_create(tenant, **kwargs)는 모델 필드 그대로 전달.
