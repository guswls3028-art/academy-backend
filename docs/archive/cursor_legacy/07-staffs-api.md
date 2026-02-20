# Staffs 도메인 API (실제 코드 기준)

**기준**: `apps/domains/staffs/urls.py`, `apps/domains/staffs/views.py`, `apps/domains/staffs/serializers.py`.  
Base path: `/api/v1/staffs/` (ROOT_URLCONF에서 prefix).

---

## 1. WorkType

| Method | Path | ViewSet | 권한 | 비고 |
|--------|------|---------|------|------|
| GET | `work-types/` | WorkTypeViewSet | IsAuthenticated, IsPayrollManager | 목록. filters: is_active 등. |
| POST | `work-types/` | WorkTypeViewSet | 동일 | 시급태그(유형) 생성. tenant 자동. |

---

## 2. StaffWorkType (직원–시급태그 연결)

| Method | Path | ViewSet | 권한 | 비고 |
|--------|------|---------|------|------|
| GET | `staff-work-types/` | StaffWorkTypeViewSet | IsAuthenticated, IsPayrollManager | 목록. filters: staff, work_type. |
| POST | `staff-work-types/` | StaffWorkTypeViewSet | 동일 | 직원에 시급태그 추가. **body 필수: staff, work_type_id**. |
| PATCH | `staff-work-types/<id>/` | StaffWorkTypeViewSet | 동일 | hourly_wage 등 수정. |
| DELETE | `staff-work-types/<id>/` | StaffWorkTypeViewSet | 동일 | 연결 삭제. |

### POST staff-work-types/ 요청·응답

- **요청 body**: `{ "staff": <int>, "work_type_id": <int>, "hourly_wage": <int|null>? }`
  - `staff`: 해당 테넌트 소속 Staff PK. 직렬화기에서 `staff_repo.staff_queryset_tenant(tenant)` 로 검증.
  - `work_type_id`: 해당 테넌트 소속 WorkType PK.
  - `hourly_wage`: 선택. 비우면 WorkType.base_hourly_wage 사용.
- **perform_create**: `serializer.save(tenant=self.request.tenant)` 로 tenant 주입.
- **응답**: 201, 생성된 StaffWorkType 단일 객체 (id, staff, work_type 중첩, hourly_wage, effective_hourly_wage, created_at, updated_at).

---

## 3. Staff (루트)

| Method | Path | ViewSet | 비고 |
|--------|------|---------|------|
| GET | `` | StaffViewSet | 목록. 응답에 owner(원장) 포함. |
| GET | `<id>/` | StaffViewSet | 상세. |
| GET | `<id>/summary/` | StaffViewSet.summary | 기간 집계. 쿼리: date_from, date_to (YYYY-MM-DD). 응답: staff_id, work_hours, work_amount, expense_amount, total_amount. |
| POST | `` | StaffViewSet | 직원 등록. |
| PATCH | `<id>/` | StaffViewSet | 수정. |
| GET | `<id>/work-records/current` | work_current | 실시간 근무 상태 (OFF / WORKING / BREAK). |
| POST | `<id>/work-records/start-work/` | start_work | 근무 시작. body: work_type (필수). |

상세 라우트는 `apps/domains/staffs/urls.py` + router 등록 참고.

---

## 4. WorkMonthLock create 검증

- POST `work-month-locks/` body: staff, year, month 필수. staff 없거나 해당 테넌트 직원이 아니면 400. year/month 숫자·month 1~12 검증.

---

## 5. 직원 목록 "대표" 행 (owner) — 1번 테넌트에서 안 뜰 때

- **표시 소스**: 목록 상단 "대표" 행은 **Staff 테이블이 아님**. `_owner_display_for_tenant(tenant, request)` 결과로 채움.
- **우선순위**: (1) TenantMembership(tenant, role=owner, is_active=True) → (2) tenant.owner_name → (3) 현재 요청 사용자가 해당 테넌트 owner → (4) 현재 사용자가 Django is_superuser 또는 is_staff(개발자용).
- **로컬(9999)에서만 보이는 이유**: 같은 계정이 로컬에서만 `is_staff`/`is_superuser`이면 (4) 폴백으로 표시됨. 1번(운영)에서는 보통 is_staff=False라 (4) 미적용.
- **해결**: 해당 테넌트에 **Owner 멤버십**이 있어야 함. 서버에서 실행:
  - `python manage.py list_tenant_owners hakwonplus` → owner 수 확인 (0이면 원인).
  - `python manage.py ensure_tenant_owner hakwonplus --username=로그인아이디` (기존 유저를 오너로 등록).
  - 유저 없으면: `python manage.py ensure_tenant_owner hakwonplus --username=원장아이디 --password=비밀번호 --name=원장이름`

**1번(개발자용) vs 2·3·4번(실제 이용자) 동일 조건**: 로직은 테넌트 ID/코드에 따라 분기하지 않음. `request.tenant` 기준으로만 동작하므로, 2·3·4번도 같은 방식으로 확인·등록하면 됨.
- **전체 테넌트 owner 현황 한 번에 확인**: `python manage.py list_tenant_owners` (인자 없음) → 활성 테넌트별로 owner 유무 출력.
- **2·3·4번 각각 확인**: `python manage.py list_tenant_owners tchul`, `list_tenant_owners limglish`, `list_tenant_owners ymath`.
- **2·3·4번 오너 등록**: `python manage.py ensure_tenant_owner tchul --username=...` (코드만 hakwonplus → tchul/limglish/ymath 로 변경).
