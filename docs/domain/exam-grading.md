# 시험 생성·혼합 채점·오답노트 — SSOT

## 목적과 사용자

원장·선생님·채점 권한이 있는 직원이 원본 시험지와 채점 방식을 한 번에
등록하고, 선택형 OMR과 답변형 직접 채점을 같은 시험 결과로 확정하는
현재 계약이다. 저장된 문항 결과는 성적 통계, 합격 판정, 클리닉 진행도,
학생별 오답노트가 함께 사용한다.

- 관리자 화면 사용법:
  [frontend/docs/USER-GUIDE-ADMIN.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/USER-GUIDE-ADMIN.md)
- OMR 출력·인식 계약: [omr.md](omr.md)
- 주요 진입점: **차시 → 시험 → 시험 추가**, **시험 → 채점·결과**

## 시험 채점 계약

`Exam`이 시험 생성 시 아래 계약을 소유한다.

| 필드 | 값 | 의미 |
|------|----|------|
| `grading_mode` | `choice` | 선택형. OMR 결과가 주 채점 경로다. |
|  | `written` | 답변형. 모든 문항을 직접 채점한다. |
|  | `mixed` | 혼합형. 선택형 결과는 OMR로 보존하고 답변형만 직접 채점한다. |
| `manual_grading_method` | `correctness` | 답변형을 정오로 입력한다. |
|  | `score` | 답변형을 문항별 부분점수로 입력한다. |
| `choice_question_count` | 0 이상의 정수 | 원본 자동 분리 시 앞에서부터 선택형으로 만들 문항 수. 혼합형은 1 이상이어야 한다. |
| `segmentation_status` | `none`, `processing`, `ready`, `failed`, `conversion_required` | 원본 문항 분리 상태다. |

문항이 생성된 뒤에도 `grading_mode`와 `manual_grading_method`는 시험
설정에서 바꿀 수 있다. 이 전환은 문항, 정답, 기존 OMR·직접 입력 결과를
삭제하거나 다시 계산하지 않고 이후 사용할 채점 화면과 수정 가능 범위만
바꾼다. `choice`에서 `written/correctness`로 바꾸면 기존 문항 전체가
정오표 입력 대상이 되고, 다시 `choice`로 바꾸면 기존 결과는 보존된 채
정오표가 잠기고 OMR 흐름을 사용한다.

반면 `choice_question_count`는 실제 혼합형 문항 구조의 경계이므로 문항
생성 뒤에는 바꿀 수 없다. 이미 문항 또는 성적이 있는 운영 시험에 새
원본을 올려 자동으로 덮어쓰는 것도 금지한다.

기존 시험의 문항별 `question_kind`가 있으면 직접 채점 가능 여부는 실제
문항 유형을 기준으로 결정한다. 따라서 기존 답안 등록 화면에서 만든
임의 순서 혼합형도 번호와 유형을 유지한다.

## 원본 시험지 등록과 자동 분리

1. 정규 시험을 만들며 시험명, 만점, 커트라인, 채점 방식과 혼합형 선택
   문항 수를 확정한다.
2. 같은 tenant의 빈 시험 ID와 원본 파일을
   `POST /exams/pdf-extract/`에 보낸다.
3. 원본은 tenant 전용 R2 경로에 `problem_source` 자산으로 저장한다.
4. PDF 또는 이미지는 `question_segmentation` 작업으로 전달하고
   `segmentation_status=processing`으로 바꾼다.
5. 성공 콜백은 문항 구조와 이미지를 연결하고 시험 만점을 문항 배점으로
   분배한다. 실패 시 `failed`로 남겨 사용자가 다시 시도할 수 있게 한다.

문항 작업은 공통 dispatcher가 확정한 페이지별 박스와 원본 문항 번호를
그대로 사용한다. worker pipeline이 같은 PDF를 별도 `question_splitter`로
다시 자르지 않으므로 그래프·도형까지 확장한 표시 영역, cross-page 번호
검증과 scan fallback 결과가 실제 저장 문항 이미지에도 동일하게 반영된다.
날짜와 `Hyper`/`Routine|Remake`/`복습 Test` 표제가 함께 있는 짧은 학원
복습지 표지는 범위 회차(`1. 평면좌표` 등)를 문항 번호로 오인하지 않고
비문항 페이지로 제외한다. 같은 헤더가 반복되더라도 실제 문제 본문이 있는
페이지는 이 짧은 표지 조건에 포함되지 않는다.

문제 뒤에 `정답 및 해설`, `정답과 풀이`처럼 명시적인 교사용 구간이 붙은
PDF는 앞선 페이지에서 문항이 확인된 경우 그 표제 페이지부터 문서 끝까지를
문항 후보에서 제외한다. 이 구간은 버리지 않고 표제가 반복되지 않는 다음
페이지까지 번호별 해설로 읽는다. 분리된 실제 문항 번호와 일치하는 해설만
콜백 결과에 포함하며, legacy `boxes`에도 제외된 해설 박스를 섞지 않는다.

허용 확장자는 PDF, PNG, JPG/JPEG, HWP/HWPX이고 최대 크기는 50MB다.
HWP/HWPX 원본은 보관하지만 운영 Linux에서 수식과 쪽 배치를 안전하게
재현하지 않는다. 이 경우 `202 Accepted`와
`status=conversion_required`를 반환하고, 같은 문서를 PDF로 저장해 다시
올리도록 안내한다. HWP/HWPX를 성공적으로 자동 분리했다고 가장하지 않는다.

tenant가 없거나 다른 tenant의 시험이면 거부한다. 이미 분리 중이면
`409`, 문항 또는 성적이 있어 잠긴 운영 시험이면 `409`, 지원하지 않는
파일이나 50MB 초과 파일이면 `400`을 반환한다.

## 직접 채점 표

### 정오 입력

| 화면 표시 | 저장 의미 | 점수 | 오답노트 |
|-----------|-----------|------|----------|
| `O` | 정답 | 문항 만점 | 제외 |
| `X` | 오답 | 0점 | 포함 |
| `오답노트` | 정답이지만 복습 지정 | 문항 만점 | 포함 |

일반 화면은 세 번째 상태를 `오답노트` 또는 좁은 셀에서 `노트`로 표시한다.
기본 단축키와 기존 Ymath 엑셀의 숫자 `0`은 호환 입력이며 숫자 점수가
아니다.
`ResultItem.is_correct=true`,
`ResultItem.include_in_wrong_note=true`로 저장한다.

**전원 결시로 설정**은 조회된 채점표의 로컬 초안만 일괄 변경한다. 조교는
일부 답안만 먼저 받은 경우 전원을 결시로 놓고 제출한 학생만 응시로 되돌린 뒤
정오를 입력할 수 있다. 이 동작은 실행 취소와 전체 초기화가 가능하며,
`apply=false` 미리보기를 거쳐 `apply=true`를 누르기 전에는 결과를 쓰지 않는다.
확정된 결시는 `NOT_SUBMITTED`로 남고 점수·석차·백분위·응시자 평균과 추이에서
제외된다.

### 점수 입력

- 0점부터 해당 문항 만점까지 입력한다.
- 문항 만점과 같으면 정답, 그보다 낮으면 오답으로 판정한다.
- 부분점수와 0점 문항은 오답노트에 포함한다.
- 만점 문항도 **복습**을 켜면 정답으로 유지하면서 오답노트에 포함한다.

### 선택형·혼합형과 결시

- `choice` 시험의 정오와 점수는 OMR 자동채점 결과로 조회할 수 있지만
  직접 채점 표에서는 수정할 수 없다. 인식 오류는 OMR 검토에서 학생 답안을
  보정한 뒤 기존 재채점 경로로 정오·점수·통계를 다시 계산한다.
- `written` 시험은 모든 문항을 수정할 수 있다.
- `mixed` 시험은 `question_kind=essay` 문항만 수정할 수 있다. 선택형
  `ResultItem`은 OMR 값으로 잠기며, 선택형 OMR 결과가 완전하지 않으면
  답변형 성적 확정을 거부한다.
- 학생을 `absent`로 확정하면 `NOT_SUBMITTED` attempt로 저장하고 점수,
  평균, 석차, 합불, 문항 통계에서 0점 응시자로 계산하지 않는다.

문항 순서는 유형별 블록으로 재정렬하지 않는다. 예를 들어
`1 객관식 / 2 숫자 단답형 / 3 객관식`은 그대로 반환한다. 각 문항에는
`kind`와 함께 다음 `answer_type`을 제공한다.

- `choice`: 선택지 답안을 쓰는 객관식
- `numeric_short_answer`: 수학 시험에서 정답지가 `0~999` 정수인 단답형
- `written`: 그 밖의 답변형·서술형

`answer_type`은 표시와 입력 안내용이며, 수정 가능 여부는 기존
`editable`과 `entry_method` 계약을 따른다. 따라서 자동채점된 문항도
정오표에서 결과를 볼 수 있고, `choice` 전체와 `mixed` 선택형은 조회만
가능하다. 자동채점 답안 보정은 직접 채점 표가 아니라 OMR 검토가 소유한다.

채점 표의 문항 머리글에서는 직접 채점 가능한 문항의 배점을 함께
수정할 수 있다. 요청은 현재 배점을 `expected_question_scores`, 변경
배점을 `question_scores`로 함께 보내며, 유효 배점 합계와 시험 단위
가감점 합계가 시험 만점과 0.01점 이내로 일치해야 한다. 미리보기는
변경 배점으로 점수만 다시 계산하고 문항을 쓰지 않으며, 확정 때 학생
결과와 배점을 같은 transaction에서 반영한다. 현재 배점이 기대값과
다르면 stale 변경으로 거부한다.

문항이 하나도 없는 시험의 조회는 오류 대신 빈 `questions`를 반환한다.
관리자 화면은 이 상태에서 기존 객관식 답안 등록과 문항 수 기반 빠른
시작을 제공한다. 빠른 시작은 시험 자체 문항 구조를 먼저 만든 뒤 같은
채점 표를 다시 불러온다.

## 확인과 확정의 분리

`GET /results/admin/exams/{exam_id}/manual-grading/`은 tenant 안의 시험
대상 학생, 실제 문항, 기존 결과와 `expected_version`, 시험 만점,
현재 문항 배점 합계와 가감점 합계를 읽어 채점 표를 만든다.

같은 URL의 `POST`는 두 단계다.

1. `apply`가 없거나 false면 학생·문항·점수·결시·덮어쓰기 여부를
   검증하고 예상 결과만 반환한다. 이 단계는 DB를 변경하지 않는다.
2. 오류가 없는 동일 payload에 `apply=true`를 보내면 한 transaction에서
   전부 확정한다.

확정 시 `Result`, `ResultItem`, `ExamAttempt`를 갱신하고 실제 변경 문항은
append-only `ResultFact(source=manual_grid)`로 남긴다. transaction
commit 후 진행도 파이프라인을 요청한다.

각 학생의 `expected_version`이 현재 `Result.updated_at`과 다르면 다른
화면에서 결과가 바뀐 것으로 보고 전체 확정을 중단한다. 성적 편집 lease가
충돌하거나 한 학생이라도 대상·문항·값 검증에 실패해도 일부 행만 저장하지
않는다.

성적 편집 lease는 동일 시험을 공유하는 세션 묶음을 ID 순서로 잠가 서로
다른 화면의 쓰기를 직렬화한다. 세션 PK를 바꾸지 않으므로 PostgreSQL
`FOR NO KEY UPDATE`를 사용한다. 이는 편집 충돌 차단은 유지하면서
`SessionProgress`와 임시저장처럼 세션 FK를 쓰는 transaction의 지연 FK
검사와 교착하지 않게 한다. 운영 회귀 검증은 실제 PostgreSQL에서 첫
편집자가 세션 잠금과 FK 쓰기를 보유한 동안 두 번째 편집자가 같은 잠금을
기다리는 두-thread 시나리오로 수행한다.

권한은 인증된 같은 tenant의 teacher/admin으로 제한한다. 시험과 학생
후보 조회는 tenant와 차시 roster를 벗어나지 않으며 기본 tenant나
cross-tenant fallback을 사용하지 않는다.

## 기존 엑셀 채점표 호환

`GET /results/admin/exams/{exam_id}/result-import/template/`에서 전용 양식을
받고, `POST /results/admin/exams/{exam_id}/result-import/`에서 미리보기
후 `apply=true`로 확정한다. 직접 채점 표와 동일한 결과·통계 경로를 쓴다.

- 일반 양식은 빈칸 또는 `O`를 정답, `X`를 오답으로 읽는다.
- Ymath 양식은 문항 셀의 `.`을 오답, 숫자 `0`을 정답·복습 지정으로
  읽는다. 응시 여부 열의 `.`은 결시다.
- 모든 문항이 빈 행은 응시 여부가 확인되어야 만점과 결시를 구분한다.
- 여러 시트가 서로 다른 학생 집합이면 안전하게 합친다. 같은 학생이
  겹치는 후보 시트가 둘 이상이면 임의 선택하지 않고 오류로 중단한다.
- 동명이인이나 공용 연락처처럼 한 학생으로 확정할 수 없으면 전용 양식의
  `수강등록ID`를 요구한다.
- 미리보기는 쓰지 않고, 확정은 전체 transaction으로 반영한다.

## 오답노트와 통계

오답노트 대상은 `ResultItem.is_correct=false` 또는
`include_in_wrong_note=true`다. 따라서 오답노트/복습 지정 문항은 점수와
정답률에는 정답으로 남으면서 학생 오답노트에는 포함된다. 재채점으로
오답도 아니고 복습 지정도 아닌 상태가 되면 누적 오답노트에서 빠진다.

결시를 제외한 확정 결과는 기존 시험 요약, 문항 통계, 합격 판정과
진행도 파이프라인이 읽는다. 선택형·답변형·혼합형이 별도 통계 저장소를
만들지 않는다.

현재 교직원 화면은 단일 시험 또는 수강 강의의 시작~종료 회차를 선택하고
최대 100문항의 PDF를 만든다. 시작과 종료 회차는 모두 포함하며 종료 회차를
비우면 시작 회차부터 현재까지 누적한다. 조회와 PDF 생성은 동일한
`from_session_order`/`to_session_order` 규칙을 사용하고 `1 <= from <= to`를
검증한다. 같은 시험이 여러 회차에 연결되어도 문항은 한 번만 싣고, 선택
범위 안의 가장 이른 회차를 표시한다.

API는 `WrongNotePDF`와 tools worker job을 tenant 범위에서 기록해 비동기로
생성하고, 완료 뒤 R2 attachment URL을 반환한다. 출력 job에도 선택한 시작·
종료 회차를 저장하므로 미리보기와 worker 조회 범위가 달라지지 않는다.
HWPX 출력은 아직 없다.

Problem Studio의 HWPX 검수본 생성은 별도 교사 보조 흐름이다. 그 결과가
시험 문항에 자동 저장되거나 오답노트 HWPX로 직접 이어진다고 안내하지
않는다. 검수된 원본 문항 저장과 편집 가능한 HWPX 출력의 제안 계약은
[시험 원본 → 회차 범위 오답노트 HWPX](../refactor/exam-wrong-note-hwpx-plan.md)에
둔다.

## API 요약

| Method | Path | 역할 |
|--------|------|------|
| POST | `/exams/` | 시험과 채점 계약 생성 |
| PATCH | `/exams/{id}/` | 채점 방식 전환. 문항·정답·기존 결과는 보존 |
| POST | `/exams/pdf-extract/` | 원본 보관과 PDF/이미지 문항 분리 요청 |
| GET | `/results/admin/exams/{id}/manual-grading/` | 직접 채점 표와 버전 조회 |
| POST | `/results/admin/exams/{id}/manual-grading/` | 직접 채점 미리보기 또는 원자적 확정 |
| GET | `/results/admin/exams/{id}/result-import/template/` | 시험 전용 엑셀 양식 다운로드 |
| POST | `/results/admin/exams/{id}/result-import/` | 엑셀 미리보기 또는 원자적 확정 |
| GET | `/results/wrong-notes` | 학생의 현재 대표 오답·복습 문항 조회. `from_session_order`~선택적 `to_session_order` 포함 |
| POST | `/results/wrong-notes/pdf/` | 같은 회차 범위의 비동기 PDF job 생성·tools worker 발행 |
| GET | `/results/wrong-notes/pdf/{job_id}/` | job 상태와 완료된 attachment URL 조회 |

## 집중 검증

```powershell
python manage.py test `
  apps.domains.exams.tests.test_guided_exam_source_workflow `
  apps.support.results.tests.test_manual_exam_grading `
  --settings apps.api.config.settings.test

python -m pytest tests/test_pdf_question_pipeline_regression.py -q

python -m pytest tests/results/test_exam_result_excel_import.py -q

python manage.py test `
  apps.domains.results.tests.test_wrong_note_service `
  apps.domains.results.tests.test_security_regression `
  --settings apps.api.config.settings.test
```

검증은 PDF 처리 상태, HWP 변환 안내, 잠긴 시험 보호, 정오·부분점수,
공통 dispatcher 크롭 재사용, 짧은 복습지 표지 제외,
후행 정답·해설의 문항 제외와 연속 해설 추출,
오답노트와 기존 `0` 호환 의미, 선택형 자동채점 정오 조회·직접 수정 차단과 OMR 보정 경계,
객관식·숫자 단답형이 섞인 원래 순서와 `answer_type`, 문항 배점
합계·stale 배점 거부, 혼합형 OMR 보존, stale result version 거부,
다중 시트 선택, tenant 차단, 양끝을 포함하는 회차 범위, 다중 회차 시험의
중복 제거, 오답노트 포함과 PDF worker/R2 상태를 포함한다.
