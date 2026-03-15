# 직원관리(Staff) 도메인 기능 보고서

> **목적**: 직원관리 관련 구현 현황, 백엔드 반영 여부, 이용 가능 기능을 정리하여 다른 개발자 인수·인계 및 코드 리뷰에 활용할 수 있도록 함.  
> **대상**: 프론트엔드(React) + 백엔드(Django REST, 테넌트 격리)

---

## 1. 개요

| 항목 | 내용 |
|------|------|
| **도메인** | 직원관리(Staff) — 급여·근태·경비·월 마감·리포트 |
| **진입 경로** | 관리자 앱 → 좌측 네비 **「직원관리」** → `/admin/staff/*` |
| **접근 권한** | `owner` / `admin` / `teacher` / `staff` 중 하나여야 접근 가능. 세부 기능은 **급여 관리자(is_payroll_manager)** 여부로 제어 |
| **백엔드 prefix** | `GET/POST /api/v1/staffs/` (테넌트별 격리) |

---

## 2. 이용자가 사용할 수 있는 기능 (화면·기능 단위)

### 2.1 홈 (`/admin/staff/home`)

| 기능 | 설명 | 백엔드 반영 | 비고 |
|------|------|-------------|------|
| 직원 목록 조회 | 이름/전화번호 검색, 활성/비활성, 역할(대표·강사·조교) 표시 | ✅ `GET /staffs/` | 목록에 원장(owner) 행 포함 |
| 직원 등록 | 이름, 전화번호, 역할(강사/조교), 로그인 계정(선택) 생성 | ✅ `POST /staffs/` | TEACHER 시 Teacher 레코드·is_staff 부여 동시 생성 |
| 직원 삭제 | 선택 직원 일괄 삭제 (대표는 선택 불가) | ✅ `DELETE /staffs/{id}/` | Staff + Teacher + User 연쇄 삭제 |
| 시급태그(WorkType) 생성 | 급여 블록(이름, 기본 시급, 색상 등) 생성 | ✅ `POST /staffs/work-types/` | 관리자만 버튼 노출 |
| 직원별 운영 이동 | 「근태」 버튼 → `/admin/staff/attendance?staffId={id}` | — | 라우팅만 |
| 직원 상세 이동 | 행 클릭 → `/admin/staff/{id}` 오버레이 | ✅ `GET /staffs/{id}/` | — |
| 엑셀 다운로드(홈 툴바) | 선택 직원 기준 엑셀 | ❌ | 현재 "준비 중" 토스트만 표시 |
| 시급 태그 추가 / 비밀번호 변경 | 툴바 버튼 | ❌ | "준비 중" 토스트만 |

### 2.2 근태 (`/admin/staff/attendance`)

| 기능 | 설명 | 백엔드 반영 | 비고 |
|------|------|-------------|------|
| 직원 선택(좌측) | 테넌트 직원 목록, 선택 시 우측 패널 활성화 | ✅ `GET /staffs/` | — |
| 근무 기록 조회 | 선택 직원·기준월 기준 근무 기록 목록 | ✅ `GET /staffs/work-records/?staff=&date_from=&date_to=` | — |
| 근무 시작 | 실시간 근무 시작 (work_type 필수) | ✅ `POST /staffs/{id}/work-records/start-work/` | 마감된 월이면 400 |
| 휴게 시작/종료 | 진행 중 근무에 휴게 | ✅ `POST /work-records/{id}/start_break/`, `end_break/` | — |
| 근무 종료 | 진행 중 근무 종료 → work_hours·amount 자동 계산 | ✅ `POST /work-records/{id}/end_work/` | — |
| 근무 수동 추가 | 날짜·시작/종료·시급유형·휴게분·메모로 기록 추가 | ✅ `POST /staffs/work-records/` | 종료 시각 있으면 저장 시 자동 정산 |
| 근무 수정/삭제 | 기존 기록 수정·삭제 | ✅ PATCH/DELETE `/staffs/work-records/{id}/` | 마감된 월이면 400 |

### 2.3 비용/경비 (`/admin/staff/expenses`)

| 기능 | 설명 | 백엔드 반영 | 비고 |
|------|------|-------------|------|
| 직원 선택(좌측) | 동일 패턴 | ✅ `GET /staffs/` | — |
| 경비 목록 조회 | 선택 직원·기간·상태 필터 | ✅ `GET /staffs/expense-records/` | — |
| 경비 등록 | 날짜·제목·금액·메모 | ✅ `POST /staffs/expense-records/` | 상태 기본 PENDING |
| 경비 승인/반려 | PENDING → APPROVED/REJECTED | ✅ `PATCH /staffs/expense-records/{id}/` (status) | is_payroll_manager 필요, 승인 후 수정 불가 |
| 경비 수정 | 승인 전 메모 등 수정 | ✅ 동일 PATCH | — |

### 2.4 월 마감 (`/admin/staff/month-lock`)

| 기능 | 설명 | 백엔드 반영 | 비고 |
|------|------|-------------|------|
| 월별 마감 실행 | 직원·년·월 선택 후 마감 → 해당 월 급여 스냅샷 자동 생성 | ✅ `POST /staffs/work-month-locks/` | 동일 (staff,year,month) 재요청 시 스냅샷 중복 방지(이미 있으면 에러) |
| 마감 목록 조회 | 테넌트 전체 마감 이력 | ✅ `GET /staffs/work-month-locks/` | 프론트에서 staff/year/month 필터링 (백엔드 필터 미지원) |

### 2.5 급여 스냅샷 (`/admin/staff/payroll-snapshot`)

| 기능 | 설명 | 백엔드 반영 | 비고 |
|------|------|-------------|------|
| 기준월별 스냅샷 목록 | 년·월 선택 시 해당 월 직원별 확정 급여 요약 | ✅ `GET /staffs/payroll-snapshots/` | — |
| 엑셀 일괄 내보내기 | 기준월 전체 직원 급여 엑셀 (비동기 job → 폴링 후 다운로드) | ✅ (경로 수정 반영됨) | 백엔드 구현됨 |
| 직원별 PDF 명세서 | 직원·년·월 선택 시 PDF 다운로드 | ✅ (경로 수정 반영됨) | 백엔드 구현됨 |

### 2.6 리포트/명세 (`/admin/staff/reports`)

| 기능 | 설명 | 백엔드 반영 | 비고 |
|------|------|-------------|------|
| 직원 선택(좌측) | 동일 패턴 | ✅ `GET /staffs/` | — |
| 직원별 급여 이력 테이블 | 선택 직원의 월별 PayrollSnapshot 목록 | ✅ `GET /staffs/payroll-snapshots/?staff=` | — |
| 월별 마감 이력 | 년·월 선택 시 마감 목록 | ✅ `GET /staffs/work-month-locks/` + 프론트 필터 | — |
| 엑셀/PDF 다운로드 | 스냅샷 탭과 동일 (직원·월 선택 후 버튼) | ✅ (경로 수정 반영됨) | — |

### 2.7 직원 상세 오버레이 (`/admin/staff/:staffId`)

| 기능 | 설명 | 백엔드 반영 | 비고 |
|------|------|-------------|------|
| 상세 조회 | 기본 정보·계정·시급유형·이번달 요약·마감 여부 | ✅ `GET /staffs/{id}/`, `summary/`, work-month-locks | — |
| 탭: 요약 | 기간별 집계 (date_from, date_to) | ✅ `GET /staffs/{id}/summary/?date_from=&date_to=` | — |
| 탭: 시급·근무유형 | StaffWorkType 목록, 추가/수정/삭제 | ✅ CRUD `/staffs/staff-work-types/` | — |
| 탭: 근무기록 | 동일 WorkRecord 목록·추가·수정·삭제 | ✅ 동일 work-records API | — |
| 탭: 비용 | 동일 ExpenseRecord 목록·추가·승인/반려 | ✅ 동일 expense-records API | — |
| 탭: 급여 히스토리 | PayrollSnapshot 목록 | ✅ `GET /staffs/payroll-snapshots/?staff=` | — |
| 탭: 리포트 | 엑셀/PDF 다운로드 링크 | ✅ (경로 수정 반영됨) | — |
| 탭: 설정 | 관리자만 표시, 수정·삭제 | ✅ PATCH /staffs/{id}/, 삭제는 목록 쪽에서만 연동 | — |
| 수정 모달 | 이름·전화번호·활성·관리자·급여유형 등 | ✅ `PATCH /staffs/{id}/` | role은 create 전용, 수정 시 무시 |
| 관리자 ON/OFF 토글 | is_manager 변경 | ✅ `PATCH /staffs/{id}/` | — |
| 삭제 버튼 | 상세 내 삭제 | ❌ 미연동 | 클릭 시 안내 알림만, 실제 삭제는 홈 목록에서만 가능 |

---

## 3. 백엔드 API 정리 (실제 반영 여부)

### 3.1 라우팅

- **진입점**: `apps/api/v1/urls.py` → `path("staffs/", include("apps.domains.staffs.urls"))`
- **서브 라우트** (`apps/domains/staffs/urls.py`):
  - `work-types` → WorkTypeViewSet
  - `staff-work-types` → StaffWorkTypeViewSet
  - `work-records` → WorkRecordViewSet
  - `expense-records` → ExpenseRecordViewSet
  - `work-month-locks` → WorkMonthLockViewSet
  - `payroll-snapshots` → PayrollSnapshotViewSet
  - `""` (루트) → StaffViewSet → list/retrieve/create/update/destroy + custom actions

### 3.2 주요 엔드포인트와 프론트 사용 여부

| 메서드 | 경로 | 용도 | 프론트 사용 |
|--------|------|------|-------------|
| GET | `/staffs/` | 직원 목록(+ owner) | ✅ 홈·근태·비용·리포트 좌측 목록 |
| GET | `/staffs/me/` | 현재 사용자 급여관리 권한·원장 표시용 | ✅ 권한 판단·원장 행 표시 |
| POST | `/staffs/` | 직원 생성(role, username, password 포함) | ✅ 직원 등록 모달 |
| GET | `/staffs/{id}/` | 직원 상세 | ✅ 상세 오버레이 |
| PATCH | `/staffs/{id}/` | 직원 수정 | ✅ 수정 모달·관리자 토글 |
| DELETE | `/staffs/{id}/` | 직원 삭제 | ✅ 홈 목록 일괄 삭제 (상세 내 삭제 버튼은 미연동) |
| GET | `/staffs/{id}/summary/` | 기간 집계 | ✅ 상세 요약·탭 |
| GET | `/staffs/{id}/work-records/current/` | 실시간 근무 상태 | 사용처 있으면 근태 패널 |
| POST | `/staffs/{id}/work-records/start-work/` | 근무 시작 | ✅ 근태 |
| GET | `/staffs/work-records/` | 근무 기록 목록(필터) | ✅ 근태·상세 근무 탭 |
| POST | `/staffs/work-records/` | 근무 기록 생성(수동) | ✅ 근태 수동 추가 |
| PATCH/DELETE | `/staffs/work-records/{id}/` | 근무 기록 수정/삭제 | ✅ |
| POST | `/staffs/work-records/{id}/start_break/` 등 | 휴게/종료 | ✅ |
| GET/POST/PATCH | `/staffs/expense-records/` | 경비 CRUD | ✅ 비용 탭·상세 비용 탭 |
| GET/POST | `/staffs/work-month-locks/` | 월 마감 조회·생성 | ✅ 월 마감·리포트·상세 |
| GET | `/staffs/payroll-snapshots/` | 급여 스냅샷 목록 | ✅ 스냅샷·리포트·상세 급여 히스토리 |
| POST | `/staffs/payroll-snapshots/export-excel/` | 엑셀 내보내기(job) | ✅ (경로 수정 반영됨) |
| GET | `/staffs/payroll-snapshots/export-pdf/` | PDF 명세서 | ✅ (경로 수정 반영됨) |
| CRUD | `/staffs/work-types/`, `/staffs/staff-work-types/` | 시급태그·직원별 시급 | ✅ |

---

## 4. 이슈·수정 권고 사항

### 4.1 [해결] 급여 엑셀/PDF export API 경로 (수정 반영됨)

- **백엔드 실제 경로**
  - 엑셀: `POST /api/v1/staffs/payroll-snapshots/export-excel/`
  - PDF: `GET /api/v1/staffs/payroll-snapshots/export-pdf/`
- **조치 완료**: 프론트에서 아래 prefix로 수정해 두었음.
  - `payrollSnapshots.api.ts`: `"/staffs/payroll-snapshots/export-excel/"`
  - `payrollSnapshotPdf.api.ts`: `"/staffs/payroll-snapshots/export-pdf/"`

### 4.2 [참고] 월 마감 목록 필터

- 백엔드 `WorkMonthLockViewSet`에는 `staff`/`year`/`month` 쿼리 필터가 없음.
- 프론트 `workMonthLocks.api.ts`에서 전체 목록 조회 후 클라이언트에서 `year`, `month`, `staff` 필터링 중. 데이터 많아지면 백엔드 필터 추가 권장.

### 4.3 [참고] 직원 상세 내 삭제 버튼

- 상세 오버레이의 「삭제」 버튼은 클릭 시 “삭제 API 연결 필요” 안내만 띄우고, 실제 삭제는 **홈 목록에서만** `deleteStaff(id)`로 수행됨. 의도적 미연동일 수 있으나, 상세에서도 삭제를 허용할 경우 `deleteStaff(staff.id)` 후 navigate(-1) 등으로 연동 가능.

### 4.4 [미구현] 홈 툴바 기능

- 「엑셀 다운로드」「시급 태그 추가」「비밀번호 변경」: 현재 "준비 중" 토스트만 있음. 백엔드에 비밀번호 변경 전용 API는 없음.

---

## 5. 권한 정리

| 구분 | 조건 | 비고 |
|------|------|------|
| 직원관리 메뉴 접근 | `ProtectedRoute`에서 `ADMIN_ROLES` (owner, admin, teacher, staff) | AppRouter 상 admin 레이아웃 내 |
| 급여 관리자(is_payroll_manager) | 백엔드 `can_manage_payroll()`: 슈퍼유저/스태프/테넌트 오너 또는 `staff_profile.is_manager` | `GET /staffs/me/`로 전달, 직원 등록·시급태그·수정·삭제·승인·마감 등에 사용 |
| 원장(owner) 표시 | TenantMembership role=owner 또는 tenant.owner_name 등 | 목록 상단 원장 행, 삭제 선택에서 제외 |

---

## 6. 프론트 구조 요약

- **라우트**: `AdminRouter` → `/admin/staff/*` → `StaffRoutes` (StaffLayout + 홈/근태/비용/월마감/스냅샷/리포트, 상세 오버레이 `:staffId/*`).
- **레이아웃**: `StaffLayout` — "직원 관리" 도메인 헤더 + 탭(홈, 근태, 비용/경비, 월 마감, 급여 스냅샷, 리포트/명세).
- **API 레이어**: `frontend/src/features/staff/api/` — staff, staff.detail, staffMe, workRecords, expenses, workMonthLocks, payrollSnapshots, payrollSnapshotPdf, staffWorkType.
- **주요 훅**: useStaffs, useStaffDetail, useWorkRecords, useExpenses, usePayrollSnapshots, useWorkMonthLock (operations/context WorkMonthContext 포함).

---

## 7. 리뷰 시 체크 포인트

1. **엑셀/PDF export**: 4.1 경로 수정 반영 완료. 실제 요청이 `payroll-snapshots` 하위로 가는지 한 번 확인 권장.
2. **테넌트 격리**: 모든 스태프 API가 `request.tenant` 기준 쿼리(repositories_staffs 등)만 사용하는지.
3. **월 마감 후 제한**: 마감된 월에 근무/경비 생성·수정·삭제 시 백엔드 400 및 프론트 메시지 노출 여부.
4. **직원 생성 시 역할**: TEACHER일 때 Teacher 생성·is_staff 부여, ASSISTANT일 때 membership role=staff만 부여되는지.
5. **삭제 연쇄**: 직원 삭제 시 Staff → Teacher(이름+전화번호 매칭) → User 삭제 순서 및 무결성.

---

*문서 생성일: 2025-03-09. 코드 기준: backend `apps/domains/staffs`, frontend `src/features/staff`.*
