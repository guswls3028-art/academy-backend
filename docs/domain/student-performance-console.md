# 학생별 누적 성적 콘솔

관리자와 강사가 학생별 성적 흐름을 출처와 수업 유형별로 비교하는 현재 계약이다.
프론트엔드 사용자 동선은 `frontend/docs/USER-GUIDE-ADMIN.md`의
`학생별 누적 추이 확인`을 함께 따른다.

## 사용자 흐름

1. `성적 → 성적 콘솔`에서 `학원 시험`을 선택한다.
2. 결과 범위 탭에서 `전체 결과`, `정규수업`, `보강수업` 중 하나를 선택한다.
3. 선택 범위는 요약, 학생 목록, 득점·변화 필터, 정렬, 선택 학생의 추이와 시험
   기록에 함께 적용된다. 학생 상세 안에서만 결과를 숨기는 로컬 필터가 아니다.
4. 시험 행은 `정규`, `보강`, `구분 필요` 배지로 분류 근거를 보여 준다.

## 분류 규칙

서버는 결과의 수강 등록 강의와 시험에 함께 연결된 차시를 조회한다.

- 연결된 차시 유형이 모두 `REGULAR`이면 `정규수업`이다.
- 연결된 차시 유형이 모두 `SUPPLEMENT`이면 `보강수업`이다.
- 차시가 없거나 같은 시험·강의에 두 유형이 함께 연결되면 `UNCLASSIFIED`다.
- `UNCLASSIFIED`는 `전체 결과`에만 포함한다. 정규나 보강으로 추측하지 않는다.
- 학생 상세 성적 API도 혼합 연결을 `session_type=null`로 반환한다. 같은 유형의
  여러 차시에 연결된 시험은 유형만 유지하고, 특정 차시 ID·제목·순서·날짜는
  하나를 임의 선택하지 않고 `null`로 반환한다.

현재 `Result`는 차시 외래키를 직접 갖지 않으므로 `(시험, 결과 수강 강의)`의 차시
연결이 분류 근거다. 한 시험을 두 유형에 함께 연결해야 한다면 결과 단위 차시를 저장하는
별도 데이터 계약을 먼저 도입해야 한다.

## API 계약

`GET /api/v1/results/admin/student-performance/`

- 권한: 인증된 동일 테넌트 `teacher` 또는 `admin`
- `session_type`: `all`(기본), `REGULAR`, `SUPPLEMENT`
- 지원하지 않는 값은 `400 session_type invalid`로 실패한다.
- 정규·보강 값은 `source=academy`에서만 유효하다. 다른 출처와 조합하면 화면에
  보이지 않는 부분 필터가 생기지 않도록 `400`으로 실패한다.
- `summary.session_type_result_count`는 현재 기간·강의·학생 검색·학년 범위 안의
  `all`, `REGULAR`, `SUPPLEMENT`, `UNCLASSIFIED` 최신 결과 건수를 반환한다. 탭을
  바꿔도 세 탭의 기준 건수는 유지된다.
- 선택한 유형은 학원 시험 point를 거른 뒤 학생별 평균, 최근값, 변화, 득점 구간,
  정렬, 페이지와 전체 요약을 다시 계산한다.
- `filter_options.grades`는 현재 테넌트의 관리 학생에게 실제 존재하는 학년을
  오름차순 고유값으로 한 번씩만 반환한다.

테넌트가 없으면 `403`, 다른 테넌트 강의는 `404`이며 다른 테넌트의 학생·시험·차시·
결과는 어떤 집계에도 포함하지 않는다. `NOT_SUBMITTED`와 유효 득점률이 없는 결과는
추이와 유형별 결과 건수에서 제외한다.

## 캐시와 변경 반영

콘솔 응답은 필터 조합별로 5분 캐시한다. 학생, 수강, 정규 시험, 차시, 시험-차시 연결,
결과, 제출 성적표의 테넌트 버전이 바뀌면 새 캐시 키를 사용한다. 시험-차시 연결을
추가하거나 제거한 뒤에도 기존 유형 분류를 재사용하지 않는다.

제출 성적표 승인·반려·무효화는 학생 행을 먼저 잠그고 해당
`StudentReportedScore` 행만 잠근 뒤 처리한다. 선택적인 증빙 파일은 읽기 위해
JOIN하지만 잠금 대상에는 넣지 않는다. 따라서 PostgreSQL의 nullable outer join
잠금 제한을 피하면서도 같은 학생의 동시 검수 순서와 테넌트 격리를 유지한다.

## 검증

- `apps/domains/results/tests/test_student_performance_console.py`
  - 정규·보강 선택 시 전체 콘솔 재계산
  - 혼합 연결의 `UNCLASSIFIED` 실패 폐쇄
  - 잘못된 필터, 학년 옵션 고유성, 테넌트 격리, 캐시와 쿼리 수
- `apps/domains/results/tests/test_admin_student_grades_scope.py`
  - 학생 상세의 정규·보강 메타데이터, 혼합 연결 `null`, 같은 유형의 여러 차시
    연결에서 특정 차시를 추정하지 않는 계약
- `frontend/e2e/admin/student-score-trend.spec.ts`
  - 상단 탭 접근성, 탭별 건수, 요약·명단·상세 동시 변경, 관리자 1366/1100px
  - 390px에서는 기존 반응형 라우팅에 따라 선생 모바일 학생 상세 추이 검증

## Generic progress write boundary

`ProgressPolicy`, `SessionProgress`, `LectureProgress`, `RiskLog`의 일반 API는
tenant 안의 현재 상태를 조회하는 용도다. 정책 적용·수업 진행·위험 판정은 각 owning
서비스가 검증한 입력으로만 기록하며 generic POST/PATCH/DELETE로 FK나 상태를 만들거나
옮기지 않는다. 따라서 member가 임의 enrollment·session·lecture ID를 보낸 쓰기는
`405`로 실패하고, `ClinicLink`의 별도 lifecycle API는 이 조회 전용 경계와 분리한다.

공유 시험에서 진척을 다시 계산할 때도 `Enrollment.lecture_id`가 강의 소유권 정본이다.
시험 결과 수강생은 같은 tenant의 실제 시험 응시 대상이어야 하며, 시험에 연결된 차시 중
그 수강생 강의와 일치하는 차시에만 `SessionProgress`가 만들어진다. `LectureProgress`의
수강 등록당 하나인 계약을 피하려고 오류를 삼키거나 강의 소유권을 바꾸지 않는다. 맞는
연결 차시가 없거나 tenant·시험 대상 경계가 맞지 않으면 다른 차시를 추정하지 않는다.
