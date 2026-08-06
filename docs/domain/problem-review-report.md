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
6. 선생님은 기본 정보, 리포트 목적(자체 문항 검토/학교 시험 분석), 총평,
   출제 기조, 모든 문항의 사고행동·난이도·핵심·함정·메모, 핵심 변별 문항의
   정답 예시·타당성, 증거·붕괴 분기·4단계 복구, 학생 실패 패턴의 증상·학습
   원인·수업 처방과 결론을 수정한다. 저장은 정수 `version`을
   사용한 낙관적 잠금이며, 오래된 화면은 `409`로 실패하고 최신 리포트를 함께
   받는다. 문항 추가·삭제·번호 수정 뒤에는 실제 검수본 문항 수와 난도별 문항
   번호를 서버가 다시 계산한다.
7. PDF 또는 PPTX 다운로드는 저장된 정확한 리포트 버전을 정규화하고 SHA-256
   fingerprint를 고정해 `problem_review_export` 작업이 조판한다. 각 요청은
   `ProblemReviewArtifact`에 pending/ready/failed 상태, 실제 파일명·MIME·크기·
   SHA-256과 R2 key를 남긴다. 같은 version+format+fingerprint는 재사용하고
   실패 건만 같은 artifact로 재시도한다. 완료 상태를 다시 읽을 때마다 15분짜리
   새 presigned URL을 발급한다.
8. 선생님이 `홈페이지 공개`를 확인하면 저장된 정확한 버전만 공개 스냅샷으로
   복제한다. 같은 리포트를 다시 공개하면 기존 게시물 ID는 유지하고 내용과 PDF를
   최신 검수본으로 교체한다. 새 스냅샷 저장이 끝난 뒤에만 이전 PDF를 지운다.

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
- 교사가 새로 추가한 문항은 `source_number=0`인 별도 검수 항목이다. 표시 번호가
  기존 원문 번호와 같아지는 편집 과정에서도 원문 문항과 합쳐 버리지 않으며,
  저장된 배열 순서와 항목 수를 그대로 보존한다. 공개 스냅샷과 PDF/PPTX
  내보내기도 저장된 검수본 배열을 기준으로 다시 정규화해 같은 충돌에서 문항을
  누락하거나 오래된 문항 수를 표시하지 않는다.
- AI 출력은 검수 초안이다. 자료에 없는 공식 정답, 실제 정답률, 등급 컷,
  출제 의도나 배점을 추측해 확정하지 않는다. 불확실하면 `검수 필요`로 둔다.
- 정답·배점·난이도와 표현은 선생님 검수 전 canonical 결과가 아니다.
- 자동 분석은 `문항 근거 -> 학생이 멈추는 지점 -> 다음 수업 처방`의 연결을
  우선한다. 출제 기조는 문항 구조를 인용한 서로 다른 축으로, 핵심 변별은 같은
  풀이 병목을 공유하는 문항 군으로, 실패 패턴은 시험지 정보 누락이 아닌 학생의
  실제 풀이 행동으로 작성한다. 근거가 부족하면 항목을 억지로 채우지 않는다.
- 다운로드 첫 요약에는 선택형·서답형 구조와 분석 근거 범위를 함께 표시한다.
  업로드 시험지에서 읽은 문항·배점·자료 구조와 실제 정답률·학교 성적 분포를
  명시적으로 구분해 추정값이 관측값처럼 보이지 않게 한다.
- PPTX는 학부모 설명과 실패 패턴 진단을 분리한다. 실패 패턴은 4개씩 별도
  슬라이드에 모두 배치해 공개 스냅샷·PDF·PPTX 사이에서 근거가 누락되지 않게
  한다.
- 분석 입력의 전화번호 형태는 외부 분석 prompt 전에 마스킹한다. 사용자는
  업로드 전에 불필요한 개인정보를 직접 가려야 한다.
- 임시 원본 ZIP은 worker terminal cleanup에서 정확한 tenant/report prefix만
  삭제한다. PDF/PPTX 산출물은 tenant/report/job별 immutable key에 둔다.
- 공개 스냅샷은 `source_excerpt`, `review_note`, `validity`, `confidence`,
  `warnings`를 제외한 허용 목록만 저장한다. 공개 PDF도 이 정제된 스냅샷으로
  다시 조판하므로 내부 메모가 빈 핵심 포인트를 대신해 노출되지 않는다.
- 공개 게시 권한도 정확한 `tenant + requested_by + report UUID + version`으로
  검사한다. 익명 목록·상세·PDF는 요청에서 해석된 단일 tenant의 `published`
  자료만 반환하고, hidden/다른 tenant 자료로 폴백하지 않는다.

## 산출물 내용·디자인 계약

PDF와 PPTX는 같은 normalized snapshot, report version, source fingerprint를
사용하며 문항 번호·배점·단원·난도·사고행동이 일치해야 한다. 레이아웃은 매체에
맞게 다르다.

- PPTX는 `관측 표지 → 3분 브리핑 → 평가 DNA → 출제 지형 → EXAM SPECTRUM →
  전 문항 EVIDENCE LEDGER → 조건 누적 지도 → 핵심 문항 X-RAY → ERROR GENOME →
  RECOVERY PROTOCOL → 보호자 대화 메모 → NEXT SIGNAL` 흐름이다. 전 문항 원장은
  입력 문항 수에 따라 8~10행 단위로 동적 분할한다. 25문항·핵심 3문항이면
  대표적으로 16장이지만 16장을 하드코딩하지 않는다.
- PDF는 슬라이드 이미지를 붙이지 않고 A4 세로 편집물로 따로 조판한다. 같은
  수치와 문항을 요약, DNA/지형, 전 문항 원장, X-ray, 회복 행동과 보호자 메모로
  나눈다.
- 시각 서명은 `EXAM SPECTRUM`이다. Deep Ink `#09162F`, Plasma Blue
  `#37B7FF`, Signal Coral `#FF526F`, Ion Amber `#F4B746`, Lab Paper
  `#F5F7FB`, Carbon `#172033`을 사용하고, 관측 rail·스펙트럼 바·조건 연결선·
  표 기준선으로 정보를 조직한다.
- 참고 완성본의 학교/강사 identity, 남색/빨강 보고서 문법, 도넛+4칸 표와
  섹션 순서는 복제하지 않는다. 자료의 분석 깊이와 전 문항 커버리지만 비교한다.
- 일부 배점을 모르면 합계·비중을 숨기고 한계를 적는다. 실제 점수 분포가 없으면
  등급컷을 만들지 않는다. 누락된 X-ray 근거는 `선생님 검수 필요`로 명시한다.
- PPTX 모든 슬라이드는 report version/fingerprint와 `[Sources]` 발표자 노트를
  갖는다. 렌더 QA는 전 페이지/슬라이드 재오픈, visible ellipsis·겹침·overflow
  0, 문항 누락 0, 배점 합계 교차검산을 하드 게이트로 삼는다.

교사 제공 원본과 보안·품질 경계는
[교사 제공 자료 인벤토리](teacher-provided-source-materials.md)를 따른다.

## 소유 경계

| 책임 | 구현 |
|------|------|
| DB 상태와 버전 | `apps/domains/tools/problem_studio/models.py`의 `ProblemReviewReport` |
| API와 소유권 검사 | `apps/domains/tools/problem_review/views.py` |
| 초안 schema와 원문 근거 보존 | `apps/domains/tools/problem_review/schema.py` |
| 전사·구조·분석 worker | `apps/domains/tools/problem_review/worker.py` |
| AI 분석 adapter | `academy/adapters/ai/problem/reviewer.py` |
| PDF/PPTX 조판 | `apps/domains/tools/problem_review/renderers.py` |
| EXAM SPECTRUM PPTX/A4 PDF 조판 | `apps/domains/tools/problem_review/spectrum_renderers.py` |
| 산출물 이력·스냅샷 identity | `apps/domains/tools/problem_studio/models.py`의 `ProblemReviewArtifact` |
| 공개 스냅샷과 익명 API | `apps/domains/landing_public/contracts/problem_review_showcase.py`, `apps/domains/landing_public/models/problem_review_showcase.py`, `apps/domains/landing_public/api/views/problem_review_showcase_views.py` |
| queue routing | `academy/application/use_cases/ai/pipelines/dispatcher.py`, `academy/application/use_cases/tools/worker_dispatcher.py` |
| 프런트 계약 | `frontend/docs/PROBLEM-REVIEW-REPORT.md` |

API:

- `GET|POST /api/v1/tools/problem-review/reports/`
- `GET|PATCH /api/v1/tools/problem-review/reports/<report_id>/`
- `POST /api/v1/tools/problem-review/reports/<report_id>/exports/`
- `GET /api/v1/tools/problem-review/reports/<report_id>/exports/<job_id>/`
- `POST|DELETE /api/v1/tools/problem-review/reports/<report_id>/publication/`
- `GET /api/v1/landing-public/problem-review-showcase/`
- `GET /api/v1/landing-public/problem-review-showcase/<id>/`
- `GET /api/v1/landing-public/problem-review-showcase/<id>/pdf/`

## 실패·재시도 경계

- 파일 없음, 6개 초과, 잘못된 metadata JSON, 외부 AI 확인 누락은 업로드 전에
  `400`으로 거절한다.
- 문항을 찾지 못하면 `failed`와 재업로드 안내를 남긴다. 분석 provider가
  실패했지만 문항 전사가 있으면 source draft fallback을 `draft`로 연다.
- dispatch 실패와 upload 뒤 예외는 임시 R2 원본을 즉시 지우고 report를
  `failed`로 표시한다.
- export는 `report + version + format + normalized snapshot fingerprint`의
  unique artifact를 써서 같은 검수본의 중복 생성 요청을 합친다. PDF/PPTX 외
  형식은 거절한다. 진행 중 artifact는 재전송하지 않고, 실패 artifact만 오류를
  지우고 재시도한다.
- PDF와 PPTX는 동일한 normalized snapshot을 사용하므로 화면 저장 전 변경은
  다운로드에 포함되지 않는다. 프런트는 다운로드 직전 변경을 먼저 저장한다.
- 공개 PDF 생성·업로드가 실패하면 공개 DB 상태를 바꾸지 않는다. DB 저장이
  실패하면 방금 올린 정확한 새 key만 지우고 기존 공개본은 그대로 유지한다.

## 검증

```powershell
$env:DJANGO_SETTINGS_MODULE = 'apps.api.config.settings.test'
C:\academy\backend\.venv\Scripts\python.exe -m pytest tests/test_problem_review_report.py -q --reuse-db
C:\academy\backend\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
C:\academy\backend\.venv\Scripts\python.exe -m ruff check apps/domains/tools/problem_review academy/adapters/ai/problem/reviewer.py tests/test_problem_review_report.py
```

배포 후에는 실제 tenant의 안전한 시험 fixture로 API upload → AI worker → draft
저장 → tools worker PDF/PPTX → fresh download URL을 확인하고, 생성 PDF/PPTX를
각각 다시 열어 페이지/슬라이드와 한글 조판 및 실제 검수본 문항 수를 점검한다.
