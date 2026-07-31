# [PROPOSED] 시험 원본 → 회차 범위 오답노트 HWPX

## 1. 목표

강사가 올린 원본 시험지를 검수 가능한 문항 단위로 시험에 저장하고, 조교가
확정한 학생별 오답·복습 기록에서 원하는 `N회차~N'회차`를 골라 편집 가능한
HWPX 시험지를 만드는 것이 목표다.

최종 사용자 흐름은 다음과 같다.

1. 강사가 원본 시험지를 시험에 올린다.
2. 시스템이 문항 영역 후보를 만들고 강사가 누락·합침·잘림을 검수한다.
3. 승인한 문항과 이미지를 해당 시험의 정본으로 저장한다.
4. 조교가 정오표 또는 OMR 검토로 학생별 결과를 확정한다.
5. 학생과 시작·종료 회차를 고르고 오답·오답노트 지정 문항을 미리 본다.
6. 편집 가능한 HWPX를 만들어 한글에서 수정·인쇄한다.

이 문서는 아직 구현되지 않은 범위를 현재 기능처럼 표현하지 않기 위해
`refactor/`에 둔다. 현재 채점·PDF 계약은
[시험 생성·혼합 채점·오답노트](../domain/exam-grading.md), 문항 분리 품질은
[매치업](../domain/matchup.md), HWPX 검수본의 현재 약속은
[Problem Studio](../domain/problem-studio.md)가 소유한다.

## 2. 현재 확인된 기준선

### 2.1 시험과 문항

- `POST /exams/pdf-extract/`는 빈 시험에 원본을 보관하고 PDF·이미지의
  문항 분리 작업을 시작한다.
- HWP/HWPX 원본은 보관하지만 Linux에서 수식·쪽 배치를 안전하게 재현하지
  않으므로 PDF 변환 안내 상태로 끝난다.
- 자동 분리는 텍스트 PDF, OCR/OpenCV, YOLO, 설정에 따른 VLM 후보 경로가
  공존한다. 모든 양식에 대해 무검수 자동 확정을 보장하지 않는다.
- 수동 박스 보정 경로는 자동 후보가 부정확할 때의 현재 안전망이다.
- 문항 또는 성적이 있는 시험의 원본을 자동으로 덮어쓰지 않는다.

### 2.2 채점과 오답 선택

- 정오표의 오답은 `ResultItem.is_correct=false`, 맞았지만 다시 볼 문제는
  `include_in_wrong_note=true`로 저장한다.
- 오답 조회는 append-only `ResultFact`가 아니라 현재 대표 결과의
  `ResultItem`을 읽는다. 재채점 또는 대표 시도 변경으로 맞은 문항을 과거
  이벤트 때문에 다시 싣지 않는다.
- 단일 시험 조회 또는 `lecture_id + from_session_order` 누적 조회를
  지원한다. `to_session_order`는 현재 없다.
- 관리자 UI의 강의 누적은 1회차부터 현재까지이며 한 번에 최대
  100문항이다.

### 2.3 출력과 작업 경계

- 현재 오답노트 출력은 tools worker가 비동기로 만드는 PDF다.
- API는 tenant 범위 안에 `WrongNotePDF`와 AI job을 기록한 뒤 큐 발행에
  성공하면 `202 PENDING`을 반환한다. 발행 실패는 두 job을 `FAILED`로
  닫고 `503`을 반환한다.
- worker는 문항 이미지와 답안 정보를 읽어 PDF를 R2에 저장하고,
  상태 API는 완료 뒤 만료형 attachment URL을 반환한다.
- Problem Studio는 업로드 원본을 편집 가능한 HWPX 검수본으로 옮기는 별도
  교사 보조 도구다. 현재 Problem Studio 결과가 `ExamQuestion` 정본에
  자동 저장되거나 오답노트 HWPX로 바로 이어지지는 않는다.

### 2.4 확인되지 않은 주장

대화에서 “제공한 시험지로 엔진 학습 중”이라고 설명한 기록만으로
tenant별 모델 학습 파이프라인이 현재 실행 중이라고 문서화하지 않는다.
현재 코드로 확인되는 것은 휴리스틱·OCR·YOLO·설정형 VLM 후보 생성과
교사 검수 경로다. 재학습을 제품 약속으로 바꾸려면 최소한 다음 증거가
필요하다.

- 승인된 학습 데이터 저장 위치와 개인정보·저작권 경계
- 모델 버전, 학습 실행, 평가 세트와 재현 가능한 지표
- tenant 간 데이터 격리와 삭제·철회 절차
- 운영 추론 버전의 배포·rollback·감사 기록

그 전에는 “자동 문항 분리 후보를 만들고 교사가 검수한다”가 안전한
현재 표현이다.

## 3. 소유권과 불변 조건

| 책임 | 소유 경계 |
|------|-----------|
| 원본, 시험, 승인된 문항 구조·이미지 | `apps/domains/exams/` |
| 대표 결과, 오답·복습 선택, 출력 job | `apps/domains/results/` |
| 문항 분리 후보와 worker dispatch | `academy/domain/tools/`, `academy/application/use_cases/` |
| HWPX 렌더링 | `apps/domains/tools/problem_studio/`의 호환 writer 재사용 후보 |
| 교차 도메인 조합 | `apps/support/`의 명시적 orchestration |

다음 조건은 구현 편의를 위해 약화하지 않는다.

- 모든 시험·수강·문항·job 조회는 명시적인 tenant에서 실패 폐쇄한다.
- 자동 분리 결과는 proposal이다. 교사가 승인하기 전 사용자 작성 문항과
  이미지를 덮어쓰지 않는다.
- 수동 크롭과 수동 이미지 등록은 자동화 추가 뒤에도 보존한다.
- 문항 분리 실패를 “문항 없음”이나 “페이지 전체가 한 문항”으로 조용히
  성공 처리하지 않는다.
- HWPX는 편집 가능한 검수본이다. 원본 HWP와 완전 동일한 레이아웃이나
  binary `.hwp` 생성을 약속하지 않는다.
- PDF 출력은 HWPX 도입 뒤에도 안정적인 fallback으로 유지한다.

## 4. 구현 단계

### Slice A — 정확한 회차 범위

현재 `from_session_order` 계약에 선택적 `to_session_order`를 추가한다.

- 조회와 PDF 생성이 동일한 범위 규칙을 사용한다.
- `1 <= from <= to`를 검증한다.
- 해당 수강의 강의·정규 차시만 포함한다.
- 같은 시험이 여러 차시에 연결되어도 문항을 중복하지 않는다.
- UI는 **이번 시험 / 강의 누적** 빠른 선택을 유지하면서 시작·종료 회차를
  추가한다.

수용 기준:

- 1~1, 2~4, 첫 회차~현재, 빈 범위가 각각 예상 문항만 반환한다.
- 다른 강의나 tenant의 회차·시험 ID는 거부한다.
- 최대 100문항 안내와 범위 축소 동선이 유지된다.

### Slice B — 검수된 문항을 시험 정본으로 저장

자동 분리 job 결과를 곧바로 canonical question으로 쓰지 않고 검수
proposal로 저장한다.

- 원본 asset ID, 페이지, bbox, 분리 엔진·버전, 신뢰도와 상태를 보존한다.
- 교사는 후보를 승인, 박스 수정, 분할, 병합, 제외할 수 있다.
- 승인 명령은 명시한 빈 시험 또는 호환 가능한 시험에만 원자적으로 문항과
  이미지를 만든다.
- 기존 문항·답안·성적이 있으면 자동 overwrite 대신 충돌을 반환한다.
- 재실행은 idempotency key 또는 source/version 비교로 중복 문항을 만들지
  않는다.

수용 기준:

- 원본 업로드 → 후보 → 수동 보정 → 승인 → 시험 설정 이미지 재열기가
  왕복한다.
- 누락, 과잉 박스, 잘린 문항을 UI에서 식별할 수 있다.
- 수동 승인 문항은 재분석해도 유지된다.
- 실패 job과 부분 asset은 운영 cleanup 대상이 명확하다.

### Slice C — 학생별 HWPX 출력

오답 조회 결과를 Problem Studio의 HWPX writer에 전달하는 전용 출력
contract를 추가한다.

- 입력은 확정된 question ID, 시험·차시 제목, 문항 이미지, 필요 시 정답·해설
  분리 옵션을 포함한다.
- worker payload에는 tenant, enrollment, 정확한 회차 범위, 대표 결과
  fingerprint를 저장한다.
- 결과가 바뀐 오래된 미리보기로 생성하지 않도록 fingerprint를 검증한다.
- PDF/HWPX는 같은 문항 집합을 사용하되 서로 독립적으로 실패·재시도할 수
  있다.
- R2 key, content type, attachment filename과 만료 URL을 형식별로 분리한다.

수용 기준:

- 1, 20, 100문항 HWPX가 package/schema 검증을 통과한다.
- 한글 2024가 있는 통제 Windows 환경에서 열기·편집·저장·재열기를
  수동 확인한다.
- 수식, 표, 이미지, 긴 선지의 품질 한계를 명확히 표시하고 원본 참조를
  잃지 않는다.
- 정답·해설 포함/분리 옵션이 학생용 문제지와 교사용 정답지를 섞지 않는다.

### Slice D — 통제된 운영 검증

- 비운영 PostgreSQL과 전용 R2/queue에서 source → proposal → approval →
  grading → range selection → HWPX round-trip을 통과한다.
- production candidate는 개발 환경과 preproduction canary를 먼저 통과한다.
- 운영 smoke는 명시적인 합성 시험·학생만 사용하고 생성한 DB/R2/job을 exact
  marker로 정리한다.
- 운영에 존재하는 교사 작성 문항이나 성적을 cleanup 대상으로 잡지 않는다.

## 5. 테스트 위치와 최소 검증

현재 기준선:

```powershell
python manage.py test `
  apps.domains.exams.tests.test_guided_exam_source_workflow `
  apps.support.results.tests.test_manual_exam_grading `
  apps.domains.results.tests.test_wrong_note_service `
  apps.domains.results.tests.test_security_regression `
  --settings apps.api.config.settings.test
```

구현 시 추가할 집중 테스트:

- `apps/domains/results/tests/`: `to_session_order`, representative result,
  100문항 제한, tenant/lecture scope, job idempotency
- `apps/domains/exams/tests/`: proposal 승인, 수동 bbox 보존, locked exam 충돌,
  source provenance
- `apps/domains/tools/problem_studio/tests.py`: HWPX package와
  문항 이미지·수식·표 배치
- frontend `e2e/admin/`: 회차 범위 선택, 미리보기, 비동기 상태 복원,
  PDF/HWPX 선택, 좁은 화면

## 6. 다음 세션 시작 순서

1. 이 문서와 현재 정본 세 문서를 읽고 실제 코드·마이그레이션을 다시
   측정한다.
2. 가장 작은 독립 단위인 Slice A의 `to_session_order`부터 구현한다.
3. 현재 문항 분리 callback이 canonical write를 어디서 수행하는지와 수동
   보정 데이터 모델을 감사한다.
4. Problem Studio HWPX writer가 시험 문항 이미지 입력을 받을 때 필요한
   최소 adapter를 설계한다.
5. 각 slice의 focused test를 통과한 뒤에만 다음 slice로 간다.

완료 선언은 “파일이 만들어졌다”가 아니라 실제 조교 흐름에서 원본, 승인한
문항, 정오, 회차 범위, 다운로드 결과가 재열기까지 같은 데이터로 이어지고
tenant 격리·실패 복구·cleanup 증거가 있을 때만 가능하다.
