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

## 데이터 소유권

`Lecture.display_order`는 tenant 안의 강의가 공유하는 유일한 영구 수동 순서다.
활성/지난 강의 화면은 각각 자기 scope의 ID 전체만 다시 배열하며, 다른 scope와
시스템 강의가 차지한 순서 슬롯은 그대로 둔다. 신규 강의는 tenant 잠금 안에서
마지막 순서에 추가된다.

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
| `title` | 사용자 저장 이름. 보강에서는 카드·경로에 표시할 이름이다. |
| `display_label` | 정규는 `{regular_order}차시`, 보강은 저장된 `title`; 빈 레거시 값만 `보강`으로 폴백한다. |

정규 번호의 유일성과 유형별 `regular_order` null 여부는 DB 제약으로 지킨다.
기존 `Session`, 시험, 과제, 출결, 영상, 수강 연결은 그대로 유지되며 유형을
나누기 위해 데이터를 복제하거나 이동하지 않는다.

## API와 권한

- `GET /lectures/lectures/`는 요청 tenant의 시스템 강의를 제외한 전체 강의를
  `display_order`, `id` 순서로 안정되게 반환한다. 전역 20건 페이지 제한을 적용하지 않아
  강의가 많은 학원에서도 시험·채점·오답노트 선택기가 같은 전체 목록을 사용한다.
- `POST /lectures/lectures/reorder/`는 staff 권한과 요청 tenant를 그대로 적용한다.
  요청은 `{"scope":"ACTIVE|PAST","ordered_ids":[...]}`이며, 해당 scope의
  시스템 강의를 제외한 ID 전체를 중복 없이 정확히 보내야 한다. 서버는 tenant와
  강의 행을 잠근 한 트랜잭션에서 순서를 교체하고 저장된 scope 목록을 반환한다.
- `GET /lectures/sessions/?lecture={lecture_id}`는 강의의 전체 수업을 반환한다.
  클라이언트는 `session_type`으로 두 진입 범위를 구성한다.
- `POST /lectures/sessions/`에서 정규는 `regular_order`, 보강은
  `session_type=SUPPLEMENT`와 사용자 이름인 `title`을 받는다.
- `PATCH /lectures/sessions/{id}/`의 `title` 수정은 같은 수업 ID와 연결 데이터를
  유지한 채 보강 표시 이름을 바꾼다.
- 부분 수정의 `start_date`·`end_date` 검증은 요청에 실제 포함된 필드를 우선한다.
  nullable 시작일을 `null`로 지우면서 종료일을 함께 옮기는 요청은 이전 시작일을
  되살려 비교하지 않으며, 최종 두 값이 모두 있을 때만 순서를 검사한다.
- 모든 조회·생성·수정·삭제는 요청의 tenant와 직원 권한으로 제한한다. 다른
  tenant 강의·반은 조회하거나 연결할 수 없다.

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
- 보강 이름 저장 실패 시 기존 이름과 연결 데이터는 유지된다.
- `0007_session_regular_order_session_session_type_and_more` 이전 데이터는 당시
  제목의 `보강` 포함 여부로 한 번 backfill되었다. 이후 런타임은 명시적
  `session_type`을 우선하며 새 제목 추론을 하지 않는다.
- 보강 이름 변경은 ID나 성적·출결 범위를 바꾸지 않으므로 별도 데이터 이전이
  없다.

## 집중 검증

```powershell
python -m pytest apps/domains/lectures/tests/test_lecture_stabilization.py -q
python -m pytest apps/domains/lectures/tests/test_lecture_reorder.py -q
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
```

핵심 회귀는 20개를 넘는 강의·차시의 전체 목록과 안정된 순서, 보강 사용자
이름의 생성·응답 보존, 정규 번호 유일성, 보강 삽입 시 화면 순서 이동,
tenant·반 경계다.
