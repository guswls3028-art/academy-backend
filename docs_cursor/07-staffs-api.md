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
| GET | `` | StaffViewSet | 목록. |
| GET | `<id>/` | StaffViewSet | 상세. |
| POST | `` | StaffViewSet | 직원 등록. |
| PATCH | `<id>/` | StaffViewSet | 수정. |
| ... | `<id>/work-records/start-work/` 등 | StaffViewSet action | 근무 시작 등. |

상세 라우트는 `apps/domains/staffs/urls.py` + router 등록 참고.
