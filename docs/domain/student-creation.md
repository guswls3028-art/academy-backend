# 학생 생성 SSOT

**상태:** Active  
**최종 점검:** 2026-05-23  
**코드 기준:** `apps/domains/students/services/creation.py`, `apps/domains/students/services/registration_approval.py`, `apps/domains/students/services/import_students.py`, `apps/domains/students/views/student_views.py`, `apps/domains/students/views/registration_views.py`, `apps/domains/students/services/lecture_enroll.py`, `apps/domains/students/services/bulk_from_excel.py`

## 1. 책임 경계

학생 생성의 계정 그래프 SSOT는 `create_student_account()`다.

이 서비스가 소유하는 것:

- `ensure_parent_account_for_student()` 호출과 Parent 연결
- 학생 `User` 생성
- 학생 비밀번호 설정 또는 가입 신청의 기존 password hash 이전
- `Student` 생성
- `TenantMembership(role="student")` 활성화
- 학부모 안내용 비밀번호 문구 반환

이 서비스가 소유하지 않는 것:

- serializer/API 입력 검증
- 활성/삭제 학생 중복 정책
- 삭제 학생 복원 또는 delete-and-recreate 결정
- 가입 신청 상태 전이(`approve_registration_request()`가 소유)
- Excel/R2/AI job dispatch
- 알림톡 발송
- HTTP 응답 모양

가입 신청 승인의 durable orchestration SSOT는 `approve_registration_request()`다. 이 서비스는 `pending -> approved` 전이와 학생 계정 생성 그래프 호출을 하나의 트랜잭션으로 처리한다. HTTP 응답 모양과 알림톡 발송은 여전히 view compatibility boundary다.

Excel/import row orchestration SSOT는 `import_students_from_rows()`와 `resolve_student_import_row()`다. 이 서비스는 학생 import 행의 중복/복원/생성 판단, school_level_mode 검증, 계정 그래프 호출, 학생-only Excel welcome dispatch를 소유한다. R2 업로드, AI job dispatch, HTTP 응답 모양은 여전히 view/worker compatibility boundary다.

알림톡 outbox화와 JSON bulk/충돌해결 표면의 row orchestration 수렴은 별도 슬라이스다.

## 2. 현재 진입점

| 진입점 | 위치 | 생성 그래프 처리 |
|--------|------|----------------|
| 단건 생성 | `StudentViewSet.create` | `create_student_account(password=...)` |
| JSON 일괄 생성 | `StudentViewSet.bulk_create` | 행 정책 처리 후 `create_student_account(password=...)` |
| 충돌 delete-and-recreate | `StudentViewSet.bulk_resolve_conflicts` | 영구삭제 후 `create_student_account(password=...)` |
| 가입 신청 승인 | `approve_registration_request` + view facade | `pending -> approved`와 `create_student_account(password_hash=reg.initial_password)`를 atomic 처리 |
| 강의/수강 Excel 신규 학생 | `lecture_enroll_from_excel_rows` -> `resolve_student_import_row` | 학생 도메인 import row SSOT로 중복/복원/생성 판단 |
| 학생 Excel worker | `ExcelParsingService` -> `import_students_from_rows` | 학생 도메인 import row SSOT로 생성, 신규 학생만 welcome 발송 |

## 3. 불변 조건

- `tenant`는 반드시 caller가 resolve해서 전달한다. tenant fallback은 만들지 않는다.
- `student_data.ps_number`는 caller 또는 serializer가 확정한다.
- `password`와 `password_hash` 중 정확히 하나만 전달한다.
- 학부모가 새로 생성되면 안내 비밀번호는 `parent_initial_password(parent_phone)`이다.
- 기존 학부모 계정이면 안내 문구는 `변경되지 않음`이다.
- welcome/approval 알림톡은 caller가 서비스 결과의 `parent_password_by_phone` 또는 `parent_password_for_notice`를 사용한다.
- 복원은 생성이 아니므로 비밀번호를 재발급하지 않고 welcome 알림톡도 새 비밀번호처럼 보내지 않는다.
- 가입 신청 승인 알림톡 실패는 이미 커밋된 승인/학생 생성을 API 500으로 되돌리지 않는다. 발송 장애는 운영 로그/알림 재처리 대상이다.

## 4. Frontend 계약

- 학생 생성 API 호출은 `src/shared/api/contracts/students.ts`의 `createStudent()`가 canonical mapper다.
- teacher 모바일 생성 시트는 role-local raw `/students/` POST를 쓰지 않고 shared contract를 호출한다.
- admin Excel 업로드의 `sendWelcomeMessage` 토글은 multipart `send_welcome_message`로 worker payload까지 전달된다.
- worker payload boolean은 `academy.application.services.excel_parsing_service._payload_bool()`로 명시 파싱한다. 문자열 `"false"`는 false다.
- teacher 모바일 Excel 업로드도 파일 선택 직후 즉시 업로드하지 않는다. `StudentListPage`의 Excel import bottom sheet에서 초기 비밀번호와 welcome 알림톡 여부를 명시 확정한 뒤 shared upload contract를 호출한다.

## 5. 검증 기준

학생 생성 경로 변경 시 최소 검증:

- `python -m pytest apps\domains\students\tests -q`
- `python -m pytest apps\domains\messaging\tests\test_messaging_service.py -q`
- `python manage.py check --settings apps.api.config.settings.test`
- `python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test`
- frontend focused ESLint for touched student files
- `pnpm typecheck`
- `pnpm build`
- `pnpm guard:legacy-api`

운영 QA는 최소 하나의 disposable 학생 생성, 로그인 가능성, 알림톡 전송 여부, cleanup(soft delete + permanent delete)을 포함한다.
