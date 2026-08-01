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

## 권한과 실패 경계

- 인증된 현재 테넌트 교직원만 목록을 조회할 수 있다.
- 다른 테넌트의 출결은 정렬·검색 결과와 전체 개수에 포함하지 않는다.
- 허용되지 않은 정렬 문자열은 ORM 필드로 전달하지 않고 기본 이름순으로
  복구한다.
- 빈 목록은 정상적인 페이지 응답으로 반환하며 기존 출결 데이터는 보존한다.

## 검증

```powershell
python -m pytest apps/domains/attendance/tests/test_attendance_list_ordering.py
python -m ruff check apps/domains/attendance/views.py `
  apps/domains/attendance/tests/test_attendance_list_ordering.py
```

핵심 회귀는 테넌트 격리, 페이지네이션 이전 전체 이름순, 상태 운영 순서,
오름·내림차순, 잘못된 정렬값의 안전한 기본값 복구다.
