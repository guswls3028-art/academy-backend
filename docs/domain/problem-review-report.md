# 문제 리뷰 리포트

Last verified: 2026-08-06

## 목적과 사용자

문제 리뷰 리포트는 학원 선생님이 직접 만든 시험지나 문제지를 업로드하고,
문항별 출제 포인트와 학생이 흔들릴 지점을 검수한 뒤 학부모·학생 설명에도 쓸
수 있는 PDF 또는 PPTX를 만드는 도구다. 관리자·원장·강사 등 현재 테넌트의
staff 역할만 사용할 수 있다.

프런트 진입점은 `/workspace/tools/problem-review`이며 도구 탭에는
`문제 리뷰 리포트`로 표시한다. Problem Studio의 원본 타이핑/이관과 달리 이
도구의 주 결과는 편집 가능한 분석 초안과 검수 후 리포트다.

## 정상 흐름과 상태

1. 선생님이 PDF, HWP/HWPX, DOC/DOCX, 이미지 또는 ZIP을 최대 6개 등록하고
   시험 기본 정보를 입력한다.
2. 화면은 외부 AI 처리 범위와 개인정보 마스킹 안내에 대한 명시적 확인을
   요구한다. 확인 전에는 업로드하지 않는다.
3. API는 tenant/user 범위의 임시 ZIP을 R2에 올리고
   `problem_review_analysis` basic AI 작업을 보낸다.
4. AI worker는 기존 Problem Studio 추출·OCR·문항 구조 분석기를 재사용한다.
   최대 80문항을 전사 근거와 함께 정규화하고, 자료에 근거한 분석 초안을
   작성한다. 외부 분석이 실패하면 원문 전사 초안을 열어 선생님 작업을 막지
   않는다.
5. 상태는 `analyzing -> draft` 또는 `analyzing -> failed`다. 브라우저를 닫아도
   DB의 `ProblemReviewReport`와 job 상태로 다시 이어서 열 수 있다.
6. 선생님은 기본 정보, 총평, 출제 기조, 모든 문항의 난이도·핵심·함정·메모,
   핵심 변별 문항과 결론을 수정한다. 저장은 정수 `version`을 사용한 낙관적
   잠금이며, 오래된 화면은 `409`로 실패하고 최신 리포트를 함께 받는다.
7. PDF 또는 PPTX 다운로드는 저장된 정확한 리포트 버전을 snapshot으로 보내
   `problem_review_export` deterministic tools 작업이 조판한다. 완료 상태를
   다시 읽을 때마다 15분짜리 새 presigned URL을 발급한다.

## 권한·데이터 불변 규칙

- 모든 조회·저장·분석·다운로드는 요청의 정확한 `tenant`와
  `requested_by`에 묶인다. 같은 테넌트의 다른 선생님에게도 리포트가 보이지
  않는다.
- 분석 job은 tenant, request user, report UUID와 임시 R2 prefix를 모두
  검증한다. export 결과 key도
  `tenants/{tenant}/tools/problem-review/{report}/{job}/` 아래만 허용한다.
- 원문 발췌는 숨은 `source_number`에 연결된 문항 근거로만 보존한다.
  클라이언트/AI가 `source_excerpt`를 덮어써도 서버 정규화가 기존 전사 근거를
  유지한다. 선생님이 번호를 고쳐도 근거 연결은 유지되며, 오인식 문항을
  삭제한 경우에는 검수본에서 실제로 제외한다.
- AI 출력은 검수 초안이다. 자료에 없는 공식 정답, 실제 정답률, 등급 컷,
  출제 의도나 배점을 추측해 확정하지 않는다. 불확실하면 `검수 필요`로 둔다.
- 정답·배점·난이도와 표현은 선생님 검수 전 canonical 결과가 아니다.
- 분석 입력의 전화번호 형태는 외부 분석 prompt 전에 마스킹한다. 사용자는
  업로드 전에 불필요한 개인정보를 직접 가려야 한다.
- 임시 원본 ZIP은 worker terminal cleanup에서 정확한 tenant/report prefix만
  삭제한다. PDF/PPTX 산출물은 tenant/report/job별 immutable key에 둔다.

## 소유 경계

| 책임 | 구현 |
|------|------|
| DB 상태와 버전 | `apps/domains/tools/problem_studio/models.py`의 `ProblemReviewReport` |
| API와 소유권 검사 | `apps/domains/tools/problem_review/views.py` |
| 초안 schema와 원문 근거 보존 | `apps/domains/tools/problem_review/schema.py` |
| 전사·구조·분석 worker | `apps/domains/tools/problem_review/worker.py` |
| AI 분석 adapter | `academy/adapters/ai/problem/reviewer.py` |
| PDF/PPTX 조판 | `apps/domains/tools/problem_review/renderers.py` |
| queue routing | `academy/application/use_cases/ai/pipelines/dispatcher.py`, `academy/application/use_cases/tools/worker_dispatcher.py` |
| 프런트 계약 | `frontend/docs/PROBLEM-REVIEW-REPORT.md` |

API:

- `GET|POST /api/v1/tools/problem-review/reports/`
- `GET|PATCH /api/v1/tools/problem-review/reports/<report_id>/`
- `POST /api/v1/tools/problem-review/reports/<report_id>/exports/`
- `GET /api/v1/tools/problem-review/reports/<report_id>/exports/<job_id>/`

## 실패·재시도 경계

- 파일 없음, 6개 초과, 잘못된 metadata JSON, 외부 AI 확인 누락은 업로드 전에
  `400`으로 거절한다.
- 문항을 찾지 못하면 `failed`와 재업로드 안내를 남긴다. 분석 provider가
  실패했지만 문항 전사가 있으면 source draft fallback을 `draft`로 연다.
- dispatch 실패와 upload 뒤 예외는 임시 R2 원본을 즉시 지우고 report를
  `failed`로 표시한다.
- export는 `report_id + version + format` idempotency key를 써서 같은 검수본의
  중복 생성 요청을 합친다. PDF/PPTX 외 형식은 거절한다.
- PDF와 PPTX는 동일한 normalized snapshot을 사용하므로 화면 저장 전 변경은
  다운로드에 포함되지 않는다. 프런트는 다운로드 직전 변경을 먼저 저장한다.

## 검증

```powershell
$env:DJANGO_SETTINGS_MODULE = 'apps.api.config.settings.test'
C:\academy\backend\.venv\Scripts\python.exe -m pytest tests/test_problem_review_report.py -q --reuse-db
C:\academy\backend\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
C:\academy\backend\.venv\Scripts\python.exe -m ruff check apps/domains/tools/problem_review academy/adapters/ai/problem/reviewer.py tests/test_problem_review_report.py
```

배포 후에는 실제 tenant의 안전한 시험 fixture로 API upload → AI worker → draft
저장 → tools worker PDF/PPTX → fresh download URL을 확인하고, 생성 PDF/PPTX를
각각 다시 열어 페이지/슬라이드와 한글 조판을 점검한다.
