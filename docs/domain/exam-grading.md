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
| `segmentation_status` | `none`, `processing`, `review_required`, `ready`, `failed`, `conversion_required` | 원본 문항 분리와 교직원 검수 상태다. |
| `student_results_published` | `true`, `false` | 학생·학부모 성적 공개 여부다. 기본값 `true`는 기존 시험 노출을 유지한다. |

문항이 생성된 뒤에도 `grading_mode`와 `manual_grading_method`는 시험
설정에서 바꿀 수 있다. 이 전환은 문항, 정답, 기존 OMR·직접 입력 결과를
삭제하거나 다시 계산하지 않고 이후 사용할 채점 화면과 수정 가능 범위만
바꾼다. `choice`에서 `written/correctness`로 바꾸면 기존 문항 전체가
정오표 입력 대상이 되고, 다시 `choice`로 바꾸면 기존 결과는 보존된 채
정오표가 잠기고 OMR 흐름을 사용한다.

반면 `choice_question_count`는 실제 혼합형 문항 구조의 경계이므로 문항
생성 뒤에는 바꿀 수 없다. 이미 문항 또는 성적이 있는 운영 시험에 새
원본을 올려 자동으로 덮어쓰는 것도 금지한다.

단, 과거 데이터에서 `choice_question_count=0`인데 이미 저장된 시험지의
`choice_count`가 1 이상이고 전체 문항 수보다 작은 경우는 실제 문항 구조와
같은 값으로 한 번 복구할 수 있다. 이 예외는 혼합형 전환의 막힌 상태를
해소하기 위한 것이며, 저장된 시험지와 다른 경계로 바꾸는 요청은 계속
거부한다.

기존 시험의 문항별 `question_kind`가 있으면 직접 채점 가능 여부는 실제
문항 유형을 기준으로 결정한다. 따라서 기존 답안 등록 화면에서 만든
임의 순서 혼합형도 번호와 유형을 유지한다.

## 학생 성적 공개

교직원은 시험 설정의 **학생 성적 공개**에서 시험별 공개 여부를 바꾼다.
비공개는 응시·제출·채점·교직원 성적표·통계를 그대로 유지하고 학생 및 학부모의
성적 목록, 누적 추이, 분석, 개별 결과에서만 해당 시험 결과를 제외한다. 정답 공개
정책인 `answer_visibility`와는 별도다. 성적이 비공개인 동안에는 점수, 문항별 정오,
석차와 분석을 직렬화하지 않는다.

비공개 시험의 개별 결과 API는 재응시 정책만 제한 응답으로 반환한다. 이를 통해
학생 화면이 결과 비공개를 미응시로 오인해 추가 제출을 허용하지 않는다. 학생·학부모
선택 범위와 tenant 검증은 공개 여부와 관계없이 먼저 실패 폐쇄한다. 공개로 다시
바꾸면 저장된 기존 결과를 재계산하거나 이동하지 않고 같은 결과가 즉시 조회된다.

## 원본 시험지 등록과 자동 분리

1. 정규 시험을 만들며 시험명, 만점, 커트라인, 채점 방식과 혼합형 선택
   문항 수를 확정한다.
2. 같은 tenant의 빈 시험 ID와 원본 파일을
   `POST /exams/pdf-extract/`에 보낸다.
3. 원본은 tenant 전용 R2 경로에 `problem_source` 자산으로 저장한다. PDF는 같은
   객체를 기존 배포용 `problem_pdf` 메타데이터에도 연결해 브라우저가 큰 파일을
   두 번 전송하지 않는다.
4. PDF, 이미지, HWP 5.x 또는 HWPX는 `question_segmentation` 작업으로 전달하고
   `segmentation_status=processing`으로 바꾼다.
5. 성공 콜백은 정본 문항을 바로 쓰지 않고 `ExamQuestionProposal`에 문제,
   원본 번호, 해설 이미지·텍스트와 출처를 저장한 뒤 `review_required`로 바꾼다.
6. 교직원은 **문항·해설 맞춤 확인**에서 문제와 선생님 원본 해설을 나란히
   보고 번호 수정, 수록/제외를 검수한다. 승인 전에는 채점 문항이 생기지 않는다.
7. 승인은 빈 운영 시험을 다시 잠그고 선택 번호가 양수·고유한지 확인한 뒤
   문항, 배점과 `source_file` 해설을 한 transaction에서 만든다. 그때만
   `ready`와 유사문항 인덱싱으로 진행한다.

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
콜백 결과에 포함하며 번호가 있는 해설 영역은 이미지로도 보존한다. legacy
`boxes`에는 제외된 해설 박스를 섞지 않는다. 추출 텍스트는 교사가 쓴 내용을
읽은 것이며 새 해설을 생성하지 않는다.

허용 확장자는 PDF, PNG, JPG/JPEG, HWP/HWPX이고 최대 크기는 50MB다. 업로드
화면은 한 진입점에서 다음 세 자료 형태를 받는다.

- **문제+해설 한 파일**: 뒤쪽 번호별 해설 구간이 있는 PDF, 또는 번호별 미주에
  문제와 필기 해설 원본 그림이 모두 있는 HWP/HWPX 한 개
- **문제 파일만**: 답 표시가 없는 PDF/PNG/JPG/JPEG 한 개
- **문제·해설 두 파일**: 답 표시가 없는 PDF/이미지와 같은 번호의 선생님 해설
  HWP/HWPX

HWP 5.x는 OLE 레코드와 BinData를, HWPX는 ZIP 패키지의 미주와 원본 BinData
참조를 직접 읽는다. 압축 BMP/JPEG/PNG/GIF를 제한 크기로 해제하고, 위쪽 문제
영역과 전체 원본 해설 이미지를 각각 저장한다. 여러 그림인 미주는 원래 순서대로
세로 결합한다. 단일 HWP/HWPX는 번호가 있는 **모든 미주**에서 원본 그림을 읽은
경우에만 문제+해설 통합 자료로 처리한다. 일부 미주에 그림이 없으면 도형 같은
부분 그림을 전체 해설로 오인하지 않고 작업 결과를 `conversion_required`로 닫아,
같은 문제지를 PDF로 저장해 다시 올리도록 안내한다. 원본은 이미 tenant 전용
자산으로 보존하며 문장·수식·필기 내용을 AI가 다시 쓰거나 생성하지 않는다.

문제지와 해설지가 분리된 Ymath 자료는 **답 표시가 없는 학생용 문제지
PDF/이미지**를 주 파일로, 같은 시험의 **선생님 해설 HWP/HWPX**를 선택 파일로
함께 올린다.
두 원본은 각각 `problem_source`, `teacher_explanation_source` 자산으로 보존하고,
worker는 학생용 원본에서 자른 문제와 HWP 미주 해설을 원본 문항 번호로만
연결한다. 한 문항도 번호가 맞지 않으면 성공으로 가장하지 않고 작업을
실패시킨다. 일부만 맞으면 맞은 해설만 후보에 붙이며 최종 확정 전 검수 화면에서
해설 유무를 확인한다. 문제 파일에 필기 정답이 직접 그려진 경우에는 크롭으로
깨끗한 문제를 복원할 수 없으므로 이 짝 파일 경로를 사용해야 한다.

단일 HWP/HWPX 업로드는 문제와 필기 해설이 각 미주 원본 그림에 함께 있는 자료를
위한 경로다. 이때 각 미주의 위쪽 30%를 초기 문제 영역으로 제안하지만, 교직원은
검수 화면에서 원본 높이의
8~98% 범위로 문항별 경계를 조절할 수 있다. 조절한 파생 이미지는 tenant 전용
키로 새로 저장하고 승인 transaction이 실패하면 제거한다. 원본 해설 이미지는
바꾸지 않으며, 짝 파일에서 온 깨끗한 PDF 문제에는 이 크롭 조절을 적용하지
않는다.

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
최대 100문항의 PDF 또는 HWPX를 만든다. 시작과 종료 회차는 모두 포함하며 종료 회차를
비우면 시작 회차부터 현재까지 누적한다. 조회와 PDF 생성은 동일한
`from_session_order`/`to_session_order` 규칙을 사용하고 `1 <= from <= to`를
검증한다. 같은 시험이 여러 회차에 연결되어도 문항은 한 번만 싣고, 선택
범위 안의 가장 이른 회차를 표시한다.

학생 상세의 **통합 오답노트**는 같은 학생의 여러 수강 강의에서 시험과
워크북을 각각 선택해 한 문서로 묶는다. 선택 원본은
`{type: exam|homework, id, enrollment_id}`로 고정하며, 서버가 모든 enrollment의
student·tenant와 각 시험의 강의 연결 또는 과제 배정을 다시 검증한다. 워크북은
`HomeworkScore.meta.question_marks`에서 X 또는 O·복습 문항을 읽고 연결된 비노출
원본 시험의 문제·선생님 해설 이미지를 사용한다. 기존 단일 시험·회차 API는
`source_selection=[]`일 때 그대로 동작한다.

여기서 회차는 화면 배치 순서인 `Session.order`가 아니라 정규 수업 번호인
`Session.regular_order`다. 보강(`session_type=SUPPLEMENT`)은 정규 회차 범위에
포함하지 않는다. 따라서 정규 수업 사이에 보강을 삽입하거나 카드를
재배치해도 `2~3회차`는 정규 2차시와 3차시만 뜻하며, 조회 결과의
`session_order`도 같은 정규 수업 번호를 반환한다.

API는 호환 이름을 유지한 `WrongNotePDF`와 tools worker job을 tenant 범위에서
기록해 비동기로 생성하고, 완료 뒤 형식별 R2 attachment URL을 반환한다. 출력
job에는 선택한 시작·종료 회차, `pdf|hwpx` 형식과 요청 시점의
`source_fingerprint`를 저장한다. 선택형 job은 정확한 `source_selection`도 저장한다.
fingerprint는 원본 유형·ID·수강 등록, 대표 오답, 답안·점수, 문항·해설
내용과 저장 객체 식별자를 SHA-256으로 묶으며 만료형 조회 URL은 제외한다.
기존 단일 수강 job에는 선택형 출처 필드를 추가하지 않아 롤링 배포 전후의
fingerprint가 동일하며, 새 통합 선택 job만 원본 유형·ID·수강 등록을 포함한다.
조회 응답의 fingerprint를 생성 요청에 보내면 서버가 최신 목록과 비교하고,
이미 재채점되었거나 문항·해설이 바뀌었으면 job을 만들지 않고 `409`로 최신
조회부터 다시 하도록 안내한다. queue payload도 같은 fingerprint를 담고 worker는
렌더 직전에 다시 계산해 불일치하면 파일을 만들지 않는다. 배포 전 생성된 빈
fingerprint job은 기존 동작으로 처리한다. 두 형식 모두
앞쪽은 답이 없는 문제와 풀이 공간, 뒤쪽은 분리 표지 뒤의 정답 및 선생님 원본
해설이다. HWPX는 문제·선생님 필기 해설 원본을 이미지로 보존하면서 제목, 정답,
`내 풀이 메모`, `추가 메모`를 한글 문단으로 제공한다. 이 문단은 한글에서 직접
편집할 수 있다. 픽셀 원본의 수식·필기는 충실도를 훼손할 수 있는 OCR 변환을 하지
않으므로 편집 가능한 한글 수식·필기 개체로 재구성한다고 약속하지 않는다.
HWPX는 세로 A4를 한 문제/해설 조각당 한 구역·한 쪽으로 만들고, 모든 원본 이미지를
`Contents/content.hpf`의 embedded BinData로 등록해 두 번째 이미지부터 누락되는
한글 렌더 실패를 막는다. 기존 PDF API는 호환 별칭으로 유지한다.

문제 쪽은 원본 종횡비에 따라 이미지 높이를 결정하고 남은 면적을 줄이 있는
풀이 공간으로 확장한다. 해설 쪽은 흰 여백을 제거한 뒤 A4에서 읽을 수 없는
세로로 긴 원본을 저밀도 행 경계에서 여러 쪽으로 나누며, 내용이 거의 없는
조각은 버리고 작은 마지막 조각은 앞쪽과 다시 합친다. PDF와 HWPX 모두 원본
문제·필기 해설을 임의 생성하거나 재서술하지 않는다.

## API 요약

시험 운영 설정을 수정하는 현재 화면은 조회 응답의 `updated_at`을
`X-Expected-Updated-At` 헤더로 보낸다. 서버는 해당 시험 행을 잠근 뒤 같은
버전일 때만 저장하고, 다른 화면이 먼저 저장했으면 `409`와
`code=stale_resource`, 현재 `updated_at`을 반환한다. 헤더가 없는 기존
클라이언트는 호환을 위해 기존 동작을 유지한다. 성공 응답은 부분 수정
필드만이 아니라 전체 `Exam` 표현을 반환하므로 클라이언트 캐시가 누락
필드를 기본값으로 오인하지 않는다.

| Method | Path | 역할 |
|--------|------|------|
| POST | `/exams/` | 시험과 채점 계약 생성 |
| PATCH | `/exams/{id}/` | 채점 방식·학생 성적 공개 전환. 문항·정답·기존 결과는 보존 |
| POST | `/exams/pdf-extract/` | 주 원본 PDF/이미지/HWP/HWPX와 선택 `explanation_file` HWP/HWPX 보관, 문항·원본 해설 분리 요청 |
| GET | `/exams/{id}/segmentation-review/` | 문항·해설 검수 후보와 만료형 이미지 URL 조회 |
| POST | `/exams/{id}/segmentation-review/approve/` | 번호·제외를 반영해 빈 시험 문항과 원본 해설 확정 |
| GET | `/results/admin/exams/{id}/manual-grading/` | 직접 채점 표와 버전 조회 |
| POST | `/results/admin/exams/{id}/manual-grading/` | 직접 채점 미리보기 또는 원자적 확정 |
| GET | `/results/wrong-notes/sources/?student_id=` | 학생의 모든 강의에서 선택 가능한 시험·워크북과 수록 문항 수 조회 |
| POST | `/results/wrong-notes/preview/` | 선택 원본의 통합 문항과 fingerprint 미리보기 |
| POST | `/results/wrong-notes/documents/` | 기존 enrollment 범위 또는 학생 `source_selection` 통합 문서 job 생성 |
| GET | `/results/admin/exams/{id}/result-import/template/` | 시험 전용 엑셀 양식 다운로드 |
| POST | `/results/admin/exams/{id}/result-import/` | 엑셀 미리보기 또는 원자적 확정 |
| GET | `/results/wrong-notes` | 학생의 현재 대표 오답·복습 문항과 안정적인 `source_fingerprint` 조회. `from_session_order`~선택적 `to_session_order` 포함 |
| POST | `/results/wrong-notes/documents/` | `output_format=pdf|hwpx`, 회차 범위와 선택적 `source_fingerprint` 검증 뒤 비동기 문서 job 생성 |
| GET | `/results/wrong-notes/documents/{job_id}/` | 형식·파일명·상태와 attachment URL 조회 |
| POST/GET | `/results/wrong-notes/pdf/...` | 기존 PDF 클라이언트 호환 별칭 |

## 집중 검증

```powershell
python manage.py test `
  apps.domains.exams.tests.test_exam_policy_update `
  apps.domains.exams.tests.test_guided_exam_source_workflow `
  apps.support.results.tests.test_manual_exam_grading `
  --settings apps.api.config.settings.test

python -m pytest tests/test_pdf_question_pipeline_regression.py tests/test_hwp_endnote_images.py -q

python -m pytest tests/results/test_exam_result_excel_import.py -q

python manage.py test `
  apps.domains.results.tests.test_wrong_note_service `
  apps.domains.results.tests.test_security_regression `
  --settings apps.api.config.settings.test
```

검증은 PDF/HWP/HWPX 처리 상태, 검수 전 proposal, 승인·번호 변경·제외·tenant 차단,
압축 HWP 이미지, HWPX 미주 원본과 일부 미주 그림 누락의 실패 폐쇄, Ymath
실자료, 잠긴 시험 보호, 정오·부분점수,
공통 dispatcher 크롭 재사용, 짧은 복습지 표지 제외,
후행 정답·해설의 문항 제외와 연속 해설 추출,
오답노트와 기존 `0` 호환 의미, 선택형 자동채점 정오 조회·직접 수정 차단과 OMR 보정 경계,
객관식·숫자 단답형이 섞인 원래 순서와 `answer_type`, 문항 배점
합계·stale 배점 거부, 과거 혼합형 경계 복구, 시험 설정 stale version 거부와
전체 PATCH 응답, 혼합형 OMR 보존, stale result version 거부,
다중 시트 선택, tenant 차단, 양끝을 포함하는 회차 범위, 다중 회차 시험의
중복 제거, 오답노트 포함과 PDF/HWPX 문제·해설 분리, worker/R2 상태를 포함한다.
