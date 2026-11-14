# 강의 수업 유형과 이름

> 강의를 종료해 `Lecture.is_active=False`가 되면 기존 수강·성적·출결·영상 진도는
> 삭제하지 않는다. 학생앱은 현재 강의 뒤의 접힌 종료 강의 영역에서 성적과 영상
> 진도 기준 시청 분량, 활동 감사 도입 이후의 재생 횟수만 읽을 수 있다. 종료 강의의 차시·시험 응시·과제 제출 대상·영상 목록과
> 재생 URL은 `Enrollment.status=ACTIVE`가 남아 있어도 서버가 거부한다. 복원하면
> 같은 수강 데이터로 현재 학습 접근이 다시 열린다.

## 목적과 사용자 흐름

원장과 선생님은 한 강의 안의 정규 진도 수업과 주말 클리닉 같은 보강 수업을
서로 다른 운영 범위로 관리한다. 관리자 앱의 **강의 → 정규 수업 / 보강**
진입점에서 유형을 고른 뒤 해당 수업의 출결·성적·시험·과제·영상을 연다.

프런트엔드 상호작용과 반응형 계약은
[frontend/docs/LECTURE-SESSION-SCOPES.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/LECTURE-SESSION-SCOPES.md)가
소유한다.

## 학생 일정의 입력 경계

### 같은 차시의 여러 수업일

한 차시를 수요일·목요일 등으로 나눠 운영할 때는 선택적으로 `SessionSchedule`을
추가한다. 차시와 영상·시험·과제 ID는 하나이며 수업일 때문에 복제하지 않는다.
`SessionScheduleMembership`은 기존 `SessionEnrollment`에 한 번만 연결되므로
같은 학생을 같은 차시의 두 수업일에 중복 배정할 수 없다. 기존 강의는 수업일을
설정하지 않으면 `Session.date`를 그대로 사용한다. 기존 반(`Section`) 설정과 독립적이다.

- 직원은 차시의 **수업일 관리**에서 이름·날짜·선택적 시작/종료 시각과 학생을
  저장한다. 출결·성적 탭에서 전체 / 개별 수업일 / 미배정을 고른다. 자료·영상·시험·과제는
  공통이며 수업일 선택이 콘텐츠 접근 권한을 새로 만들거나 제거하지 않는다.
- `GET /lectures/sessions/{id}/schedules/`는 `version`, `schedules`, 차시 `roster`,
  `unassigned_enrollment_ids`를 반환한다. `PUT`은 같은 version과 전체 schedules를
  받아 한 트랜잭션으로 저장한다. 각 schedule은 id(신규 생략), label, date,
  start_time/end_time(선택), enrollment_ids를 갖는다. 최대 20개이며 현재 차시·tenant의
  명단만 배정한다. 이름/학생 중복·잘못된 날짜/시간/ID는 400, 오래된 version은 409다.
  409에서는 입력을 자동 덮어쓰지 않고 최신 상태를 불러온 뒤 재시도한다.
- `POST`에 `version`, `source_session`을 보내면 같은 강의의 이전 설정을 가져온다.
  비어 있는 대상 차시에서만 가능하다. 두 차시 기준일의 간격(기준일이 없으면 7일)만큼
  날짜를 이동하고 활성 학생을 등록한다. 대상에서 퇴원 처리한 학생은 되살리지 않는다.
  새 출결만 UNSET으로 만들며 원래 출결·성적·진도는 유지한다.
- 출결 조회·전체 현장 출석 및 차시 성적 조회는 `schedule=<id>`로 학생 범위를 좁힌다.
  `schedule=0`은 미배정, 매개변수 생략은 전체다. 삭제되었거나 다른 차시·tenant의
  수업일 또는 잘못된 값은 오류이며 전체 명단으로 확대하지 않는다. 전체 출석의
  되돌리기는 기존 서명 토큰에 기록된 실제 변경 행만 되돌린다.
- 학생/학부모 일정·오늘 수업·최근 출결·영상 목록의 날짜 및 QR 출석 대상은 학생에게
  배정한 날짜를 사용한다. 수업일이 있는데 미배정이면 date=null,
  schedule_configured=true로 반환하며 차시 기준일로 잘못 안내하지 않는다.
  학습 자료 접근은 기존 수강 권한을 유지한다.
  명시적으로 배정한 수업일은 강의의 명목상 기간을 넘어도 활성 강의의 일정에 표시한다
  (예: 수요일 기준 강의의 마지막 목요일 수업). 강의 종료 처리(`is_active=False`)와
  수강 권한은 계속 우선하며 강의 기간 데이터를 자동 수정하지 않는다.
- 직원의 `GET /lectures/sessions/?date=YYYY-MM-DD`도 실제 수업일로 차시를 찾고,
  `include_progress=1`의 출결 인원은 그 날짜에 배정한 학생 범위다. 읽기 전용
  `schedule_dates`는 해당 차시의 수업일 ID·이름·날짜·시간을 제공한다.
- 수업일 편집/삭제는 기존 학습 기록을 지우지 않는다. 마지막 수업일을 지우면 기존
  단일 날짜 운영으로 돌아간다. 변경 전후와 직원 ID를 `SessionScheduleChange`에
  남긴다. 마이그레이션은 세 테이블만 추가하며 기존 행을 일괄 변환하지 않는다.
  유일성 제약은 새 테이블 생성에 포함한다. 구 API의 기존 쓰기는 그대로 유효하다.
  배포는 backend schema/API를 먼저 검증·반영한 뒤 frontend를 승격한다. 신규 UI와
  구 API가 만났을 때는 수업일 조회 오류와 재시도를 표시하며 정상 저장으로 꾸미지 않는다.

검증 소유자는 `tests/test_session_schedules.py`다. 배정→재조회,
기존 영상 진도/출결 보존, 필터·전체 출석·되돌리기, 학생 일정/Today/QR, 이전 차시
가져오기, 중복·오래된 버전·다른 tenant 거부를 확인한다. 실제 사용과 데스크톱/390px
화면 검증은 연결된 프런트엔드 소유 문서 및 동일 배포 후보의 격리 QA에서 수행한다.

`GET /student/sessions/me/`는 강의의 자유 입력 `lecture_time`에서 유효한 24시간제
시작 시각을 찾으면 `start_time`으로 반환한다. `25:00`, `12:99`, 자릿수가 잘못된
시간처럼 해석할 수 없는 값은 `null`로 반환하며 해당 차시와 다른 일정을 계속
표시한다. 저장된 강의 시간 문자열은 자동으로 고치거나 삭제하지 않는다. 화면은
시각이 없을 때 잘못된 시각을 추정하지 않고 기존 날짜·수업 제목을 표시한다.

`/student/sessions/hide/`와 `/unhide/`의 `id`는 0이 아닌 정수 또는 해당 십진수
문자열만 허용한다. 양수는 차시, 음수는 클리닉 예약을 나타내며 절댓값은 DB ID의
최댓값 `2^63-1` 이하다. 소수·불리언·범위 초과·잘못된 입력은 400과 무변경을
반환하며 인접한 일정 ID로 잘라서 처리하지 않는다. 정상 숨김·되돌리기·반복 요청,
학생/자녀 및 학원 권한 경계와 실제 수업·예약 데이터 보존은 유지한다.

검증: `apps/domains/student_app/tests/test_schedule_input_boundaries.py`의 정상·잘못된
강의 시간, 숨김·되돌리기 입력 경계, 문자열 ID·음수 ID·반복과 저장 데이터 보존.

## 데이터 소유권

`Lecture.display_order`는 tenant 안의 강의가 공유하는 유일한 영구 수동 순서다.
활성/지난 강의 화면은 각각 자기 scope의 ID 전체만 다시 배열하며, 다른 scope와
시스템 강의가 차지한 순서 슬롯은 그대로 둔다. 신규 강의는 tenant 잠금 안에서
마지막 순서에 추가된다. 이 필드는 롤링 배포의 expand 단계에서는 nullable이다.
필드를 모르는 구버전 API가 배포 중 만든 `null` 행은 목록의 마지막에 안정적으로
보이고, 신버전의 첫 재정렬이 tenant 잠금 안에서 유일한 양수 순서로 정규화한다.
신버전의 생성 경로는 caller가 보낸 순서를 사용하지 않고 tenant 잠금 아래 마지막
값을 계산하므로 같은 tenant의 동시 생성도 서로 다른 순서를 받는다.

강의 목록의 `active_enrollment_count`는 해당 강의에 연결된 `ACTIVE`
`Enrollment` 행의 수다. 학생 계정 전체 수나 tenant 전체의 중복 제거 학생 수가
아니며, 목록 queryset의 tenant 범위를 유지한 상관 subquery로 한 번에 계산한다.
따라서 다른 tenant의 등록이나 `INACTIVE`·`PENDING` 등록은 포함하지 않는다.

관리자 목록의 drag·키보드·touch 조작과 검색/정렬·rollback UI 계약은
[frontend/docs/LECTURE-SESSION-SCOPES.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/LECTURE-SESSION-SCOPES.md)가
소유한다.

별도 보강 모델을 만들지 않는다. `apps.domains.lectures.models.Session`이 한
수업의 공통 생명주기를 소유하고 다음 필드로 유형과 이름을 표현한다.

| 필드 | 규칙 |
|------|------|
| `session_type` | `REGULAR` 또는 `SUPPLEMENT`; 제목 문자열로 새 데이터를 추론하지 않는다. |
| `regular_order` | 정규 수업의 n차시 번호. 보강은 반드시 `null`이다. |
| `order` | 같은 강의·반 안의 화면 배치 순서. 두 유형을 모두 포함한다. |
| `title` | 사용자 저장 이름. 정규·보강 모두 카드·경로의 사용자 지정 이름으로 쓸 수 있다. |
| `display_label` | 정규의 기본 제목(`N차시`, 숫자만 있는 레거시 값, 또는 `N차시 (시각~시각)`)은 `{regular_order}차시`, 사용자 지정 제목은 그 제목을 표시한다. 보강은 저장된 `title`; 빈 레거시 값만 `보강`으로 폴백한다. |

정규 번호의 유일성과 유형별 `regular_order` null 여부는 DB 제약으로 지킨다.
기존 `Session`, 시험, 과제, 출결, 영상, 수강 연결은 그대로 유지되며 유형을
나누기 위해 데이터를 복제하거나 이동하지 않는다.

## API와 권한

- `GET /lectures/lectures/`는 요청 tenant의 시스템 강의를 제외한 전체 강의를
  `display_order`, `id` 순서로 안정되게 반환한다. 전역 20건 페이지 제한을 적용하지 않아
  강의가 많은 학원에서도 시험·채점·오답노트 선택기가 같은 전체 목록을 사용한다.
  각 행은 현재 수강등록 요약인 read-only `active_enrollment_count`를 함께 반환한다.
- `POST /lectures/lectures/reorder/`는 staff 권한과 요청 tenant를 그대로 적용한다.
  요청은 `{"scope":"ACTIVE|PAST","ordered_ids":[...]}`이며, 해당 scope의
  시스템 강의를 제외한 ID 전체를 중복 없이 정확히 보내야 한다. 서버는 tenant와
  강의 행을 잠근 한 트랜잭션에서 순서를 교체하고 저장된 scope 목록을 반환한다.
- `GET /lectures/sessions/?lecture={lecture_id}`는 강의의 전체 수업을 반환한다.
  클라이언트는 `session_type`으로 두 진입 범위를 구성한다.
- `POST /lectures/sessions/`에서 정규는 `regular_order`, 보강은
  `session_type=SUPPLEMENT`와 사용자 이름인 `title`을 받는다.
- `PATCH /lectures/sessions/{id}/`의 `title` 수정은 정규·보강 모두 같은 수업 ID와
  연결 데이터를 유지한 채 표시 이름을 바꾼다. 정규의 `regular_order`와
  `session_type=REGULAR`는 이름에 `직보`가 들어가도 유지한다.
- 부분 수정의 `start_date`·`end_date` 검증은 요청에 실제 포함된 필드를 우선한다.
  nullable 시작일을 `null`로 지우면서 종료일을 함께 옮기는 요청은 이전 시작일을
  되살려 비교하지 않으며, 최종 두 값이 모두 있을 때만 순서를 검사한다.
- 모든 조회·생성·수정·삭제는 요청의 tenant와 직원 권한으로 제한한다. 다른
  tenant 강의·반은 조회하거나 연결할 수 없다.
- 강의 부분 수정은 잠근 최신 행에서 시작·종료일을 검증한다. 다른 담당자가
  종료일을 먼저 변경한 경우 그 값과 맞지 않는 시작일 수정은 400으로 거부하고
  기존 기간을 보존한다. 같은 날짜·날짜 비우기 등 기존 정상 수정은 유지한다.
- 차시 날짜 필터와 반별 일괄 생성의 `2026-02-29`, `2026-04-31` 같은 없는 날짜는
  400과 입력 사유를 반환한다. 일괄 생성은 모든 날짜가 검증된 뒤 저장하므로
  일부 반만 생성되지 않으며, 정정 재시도와 유효한 윤년 날짜는 정상 처리한다.
  `test_lecture_stabilization`의 달력 경계·동시 기간 수정 회귀로 확인한다.
- 수강 일괄 등록의 `lecture`, `students`와 차시 일괄 등록의 `session`,
  `enrollments`는 양의 64비트 정수 ID 또는 같은 값을 나타내는 ASCII 숫자
  문자열만 허용한다. 소수·boolean·0/음수·범위 초과·객체는 조회나 상태 변경 전에
  400으로 거부하여 다른 ID로 잘려 수강·차시 명단·수납에 반영되지 않게 한다.
  정상 정수 문자열, 기존 수강 재활성화 및 출결 기록 보존은 유지한다.
  `test_single_route_scope_guards`에서 실제 API의 거부·무변경과 정상 복구를 검사한다.

성적과 출결은 선택한 `Session` ID에 귀속된다. 누적 성적의 정규·보강 분류는
`session_type`을 사용하며 제목을 분류 근거로 사용하지 않는다. 자세한 누적
성적 규칙은 [student-performance-console.md](student-performance-console.md)가
소유한다.

## 실패와 호환성

- 중복 정규 번호, 다른 강의의 반, 다른 tenant 대상은 `400/403/404`로
  fail-close한다.
- 강의 순서 요청에 중복 ID가 있으면 `400`이다. 새 강의 생성·종료·복원 등으로
  scope가 달라졌거나 다른 tenant ID가 섞였으면 정보 노출 없이
  `409 LECTURE_ORDER_STALE`로 전체 요청을 rollback한다. 클라이언트는 낙관 순서를
  이전 snapshot으로 되돌리고 목록을 refetch한다.
- `0008_lecture_display_order`는 구버전 API와 신버전 API가 겹치는 동안 필드를
  생략한 INSERT를 허용한다. 기존 행은 migration에서 backfill하고, 배포 창에
  생긴 nullable 행은 위 재정렬 경로가 잠금 후 정규화한다. 따라서 production
  롤링 교체 중 강의 생성이 중단되거나 동일한 `0` 순서로 충돌하지 않는다.
- 이름 저장 실패 시 기존 이름과 연결 데이터는 유지된다.
- `0007_session_regular_order_session_session_type_and_more` 이전 데이터는 당시
  제목의 `보강` 포함 여부로 한 번 backfill되었다. 이후 런타임은 명시적
  `session_type`을 우선하며 새 제목 추론을 하지 않는다.
- 이름 변경은 ID나 성적·출결 범위를 바꾸지 않으므로 별도 데이터 이전이
  없다.

## 집중 검증

```powershell
python -m pytest apps/domains/lectures/tests/test_lecture_stabilization.py -q
python -m pytest apps/domains/lectures/tests/test_lecture_reorder.py -q
python -m pytest apps/domains/lectures/tests/test_lecture_order_concurrency_pg.py -q
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
```

핵심 회귀는 20개를 넘는 강의·차시의 전체 목록과 안정된 순서, 보강 사용자
이름의 생성·응답 보존, 정규 번호 유일성, 보강 삽입 시 화면 순서 이동,
tenant·반 경계다.


## 삭제 시 커뮤니티 공개 범위 보존

연결 게시물이 있는 강의·차시는 기존 직원 DELETE에서도 403과 원인·복구 안내를
반환한다. 하위 차시 게시물도 강의 삭제 보호 대상이며 콘텐츠와 매핑을 그대로
유지한다. 불필요한 연결 글을 사용자가 먼저 삭제한 뒤 재시도할 수 있고, 글을
보존하려면 강의·차시도 유지한다. 다른 기록이 없는 빈 대상의 삭제204는 유지한다.
대상 변경·명시적 전체 공개, ORM 참조 보호, tenant 전체 정리와 회귀의 상세 소유자는
[커뮤니티 정책](community.md#강의차시-삭제와-공개-범위-보존-2026-11-05)이다.

## 강의·차시 삭제 시 운영 설정과 수업일 이력 보존

강의에 연결된 수납 비목, 출결 QR 지점 또는 시험의 강의별 합격 기준이 있으면
직원의 강의 삭제 요청에 403을 반환한다. 비활성 비목·QR 지점도 포함한다.
비목의 강의 범위를 독립 비목으로 바꾸거나 QR 토큰·합격 기준을 삭제하지 않는다.
차시에 수업일 변경 이력이 있으면 현재 수업일이 비어 있어도 차시와 상위 강의의
삭제를 거부한다. 기록을 보관하려면 강의를 종료한다. 보호 대상과 기존 빈 강의·
차시 삭제 성공은 `test_lecture_session_delete_guards.py`에서 확인한다.
