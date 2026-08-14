# 데이터 목록 정렬·필터 계약

## 목적과 범위

교직원·학생·학부모·플랫폼 운영 화면의 테이블, 그리드, 선택 모달이 같은
데이터를 새로고침하거나 페이지를 이동해도 순서를 안정적으로 유지하는 현재
계약이다. 필터와 정렬은 표시 순서만 바꾸며 tenant, 역할, 학생·자녀 선택 범위를
넓히지 않는다.

프론트엔드 상호작용과 반응형 계약은
[frontend DATA-LIST-CONTRACT.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/DATA-LIST-CONTRACT.md)가 소유한다.

## 서버 불변 규칙

1. tenant·권한·삭제 상태와 도메인 필터를 먼저 적용한다.
2. 페이지네이션하는 API는 필터된 전체 queryset을 서버에서 정렬한 뒤 자른다.
   클라이언트가 현재 페이지만 다시 정렬해 전역 순서를 흉내 내지 않는다.
3. 날짜·시각·점수·상태처럼 동률이 가능한 키 뒤에는 안정적인 `id` 보조키를 둔다.
   방향은 주 정렬 방향과 맞춘다.
4. 화면의 기본 정렬 키가 화면에 표시하는 점수·날짜·상태와 의미가 달라서는 안
   된다. 서로 다른 업무 값이면 API가 둘을 별도 필드로 내려 화면이 함께 밝힌다.
5. 요청 정렬값은 허용 목록으로 제한한다. 잘못된 값은 도메인의 안전한 기본
   정렬로 복구하고 ORM 필드명을 그대로 실행하지 않는다.
6. null·미입력·미제출 행의 위치를 명시하고, 0을 null로 취급하지 않는다.

## 현재 주요 목록

| 목록 | 서버 기본 정렬 | 필터·페이지 경계 |
|------|----------------|------------------|
| 시험 학생별 결과 | 1차점수 표준 공동 순위, 학생명, enrollment ID | 전체 응시 결과를 반환하며 결시는 석차 모집단 밖 |
| 차시 성적·과제 결과 | 학생명, enrollment ID | 차시 roster·tenant 범위를 먼저 확정 |
| 출결 명단 | 요청한 허용 키와 이름·ID 보조키 | 전체 queryset 정렬 후 50명 페이지 |
| 학생 목록 | 허용 ordering, 기본 최신 ID | 검색·삭제 탭·tenant 필터 후 페이지 |
| 클리닉 세션·예약·제출 | 날짜/시각 또는 생성시각과 ID | 상태·월·학생 범위 후 페이지 |
| 수납·청구서 | 청구월·상태·학생명과 ID | tenant·상태·강의·비목 필터 후 페이지 |
| 시험·과제 문항 | 문항 번호, ID | 시험·과제 소유권 확인 후 전체 반환 |

## 시험 결과 점수와 상태

`GET /results/admin/exams/{exam_id}/results/`는 다음 값을 분리한다.

- `ranking_score`: 석차 계산에 사용한 1차 점수
- `final_score`: 대표 결과에 저장된 현재 최종점수
- `rank`: `ranking_score`의 표준 공동 순위(competition rank). 동점자는 같은
  등수이고 다음 등수는 동점 인원만큼 건너뛴다. 예를 들어 점수가
  `19, 14, 14, 12`이면 등수는 `1, 2, 2, 4`다.
- `result_status`: `NOT_SUBMITTED`, `PROCESSING`, `PARTIAL`, `DONE`, `FAILED`

수동 입력·엑셀 반영처럼 Submission 행이 없어도 확정 점수가 있으면 `DONE`이다.
`NOT_SUBMITTED` attempt는 점수·석차·평균에서 제외한다. 재시험 때문에
`ranking_score`와 `final_score`가 다르면 둘 다 내려 화면이 기준을 숨기지 않는다.
`percentile`은 이 공동 순위를 실제 `cohort_size`로 나눠 계산하므로 동점 뒤 학생의
전체 응시자 대비 위치도 건너뛴 등수를 반영한다.

## 검증

```powershell
C:\academy\backend\.venv\Scripts\python.exe -m pytest `
  apps/domains/results/tests/test_admin_exam_results_scope.py `
  apps/domains/results/tests/test_session_scores_roster_scope.py -q
```

클리닉·청구·출결의 목록 회귀에서는 같은 날짜·시각·이름을 가진 행을 둘 이상
만들고 ID 보조키까지 검증한다. PostgreSQL 운영 경로에서는 정렬이 페이지 분할
전에 적용되는지도 확인한다.
