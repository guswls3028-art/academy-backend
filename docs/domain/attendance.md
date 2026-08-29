# 출결 명단

## 목적과 진입점

교직원은 강의의 차시 상세 `출결` 탭에서 현재 테넌트 학생의 출결 상태를
조회·수정한다. 목록 API는 `GET /api/v1/lectures/attendance/`이며
`apps/domains/attendance/views.py`의 `AttendanceViewSet`이 소유한다. 화면 동작은
프런트엔드 [`docs/ATTENDANCE-ROSTER-SAFETY.md`](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/ATTENDANCE-ROSTER-SAFETY.md)에 둔다.

## 목록 정렬과 페이지네이션

목록은 항상 현재 요청의 테넌트와 삭제되지 않은 학생으로 먼저 범위를 제한한다.
그 범위 전체를 정렬한 뒤 페이지네이션하므로 50명을 넘는 차시에서도 페이지 간
순서가 이어진다. `ordering`이 없거나 허용되지 않은 값이면 이름 가나다순을
적용한다.

허용 값은 `name`, `status`, `parent_phone`, `phone`, `id`와 각각의 내림차순
형태(`-` 접두사)다. 상태는 화면의 운영 순서인 미입력, 현장, 영상, 보강, 지각,
조퇴, 결석, 출튀, 자료, 부재, 퇴원 순으로 정렬한다. 같은 값은 이름과 ID를
보조 키로 사용해 페이지 이동과 재조회에서도 순서가 흔들리지 않게 한다.

정렬은 조회 표현만 바꾸고 출결 행이나 학생 데이터를 수정하지 않는다. 검색과
상태 필터는 동일한 테넌트 범위 안에서 정렬과 함께 적용된다. 수정·삭제 요청의
행 잠금 쿼리에는 목록 정렬을 적용하지 않는다.

## 강의 명단 Excel 다운로드

강의 수강생 화면의 `엑셀 다운로드`는
`POST /api/v1/lectures/attendance/excel/`로 현재 테넌트와 강의를 검증한 뒤,
Tools worker가 요청 시점의 명단·차시·출결을 새 `.xlsx` 스냅샷으로 만든다.
완료 작업의 `result.download_url`은 1시간 동안만 유효하다. 따라서 다운로드를
다시 누르면 과거 완료 작업이나 만료 URL을 재사용하지 않고 새 작업과 새 URL을
발급해야 한다.

R2 서명 URL은 `Content-Disposition: attachment`와 RFC 5987 UTF-8 파일명,
XLSX MIME(`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`)을
응답 오버라이드로 포함한다. 교차 출처 링크의 `download` 속성을 무시하는 iOS
Safari에서도 XML 응답 화면으로 이동하지 않고 파일 다운로드로 처리하기 위한
서버 계약이다. 작업 실패, 만료 링크, 또는 결과 URL 누락은 성공 파일로 취급하지
않으며 클라이언트는 실패로 표시한다.

## 권한과 실패 경계

- 인증된 현재 테넌트 교직원만 목록을 조회할 수 있다.
- 다른 테넌트의 출결은 정렬·검색 결과와 전체 개수에 포함하지 않는다.
- 허용되지 않은 정렬 문자열은 ORM 필드로 전달하지 않고 기본 이름순으로
  복구한다.
- 빈 목록은 정상적인 페이지 응답으로 반환하며 기존 출결 데이터는 보존한다.

## 검증

```powershell
python -m pytest apps/domains/attendance/tests/test_attendance_list_ordering.py
python -m pytest apps/domains/attendance/tests/test_attendance_excel_export.py
python -m ruff check apps/domains/attendance/views.py `
  apps/domains/attendance/tests/test_attendance_list_ordering.py `
  apps/domains/attendance/tests/test_attendance_excel_export.py `
  apps/infrastructure/storage/r2.py `
  academy/application/use_cases/ai/pipelines/excel_export_handler.py
```

핵심 회귀는 테넌트 격리, 페이지네이션 이전 전체 이름순, 상태 운영 순서,
오름·내림차순, 잘못된 정렬값의 안전한 기본값 복구와 반복 다운로드의 새 작업,
실제 XLSX 파싱, iOS 안전 다운로드 헤더다.
