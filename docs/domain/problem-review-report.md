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
   원인·수업 처방과 결론을 수정한다. 각 문항은 원문과 정답을 대조한 뒤에만
   `review_status=verified`가 된다. 문항 내용을 다시 고치면 해당 문항은 즉시
   `unverified`로 돌아간다. 저장은 정수 `version`을
   사용한 낙관적 잠금이며, 오래된 화면은 `409`로 실패하고 최신 리포트를 함께
   받는다. 문항 추가·삭제·번호 수정 뒤에는 실제 검수본 문항 수와 난도별 문항
   번호를 서버가 다시 계산한다.
7. 서버는 시험 기본 정보·총평·2개 이상의 출제 축·모든 문항의 필수 분석과
   원문 대조·핵심 변별·실패 패턴·72시간/2주/다음 시험 처방·결론을 다시
   검사한다. 모두 충족된 정확한 version에만 검수 완료 시각, 검수자와 SHA-256
   fingerprint를 기록한다. 이후 어떤 저장이든 이 완료 증표를 지우므로 다시
   최종 검수해야 한다.
8. PDF 또는 PPTX 다운로드는 최종 검수가 확정된 저장 버전만 정규화하고 SHA-256
   fingerprint를 고정해 `problem_review_export` 작업이 조판한다. 각 요청은
   `ProblemReviewArtifact`에 pending/ready/failed 상태, 실제 파일명·MIME·크기·
   SHA-256과 R2 key를 남긴다. 같은 version+format+fingerprint는 재사용하고
   실패 건만 같은 artifact로 재시도한다. 완료 상태를 다시 읽을 때마다 15분짜리
   새 presigned URL을 발급한다. 산출물에도 해당 검수 완료 시각을 복사하며,
   완료 증표 없는 기존 산출물은 다운로드 URL을 발급하지 않는다.
9. 선생님이 `홈페이지 공개`를 확인하면 최종 검수가 확정된 정확한 버전만 공개 스냅샷으로
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
- AI 정규화와 source fallback은 모든 문항을 `unverified`로 만든다. AI가 생성한
  JSON이나 이전 draft의 문구만으로 검수 완료 상태를 승격할 수 없다.
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
  다시 조판하므로 내부 메모가 빈 핵심 포인트를 대신해 노출되지 않는다. 난도
  분포의 보조 `points`·`note` 값에 AI 미확정 표식(`검수 필요`, `확인 필요`,
  `미확인`, `-`)이 남아 있어도 공개 스냅샷에서는 빈 값으로 정제한다. 검수된
  문항의 난도·배점·번호 자체는 그대로 보존한다.
- 공개 게시 권한도 정확한 `tenant + requested_by + report UUID + version`으로
  검사한다. 익명 목록·상세·PDF는 요청에서 해석된 단일 tenant의 `published`
  자료 중 `snapshot.verification.status=verified`인 것만 원칙적으로 반환한다.
  검수 계약 도입 전에 이미 staff가 공개한 자료는 배포 연속성을 위해 데이터
  migration이 `legacy_published + pre-verification-publication` 호환 표식을 남긴
  경우에만 익명 조회를 허용한다. 이 표식은 과거 공개 사실만 나타내며 검수 완료나
  fingerprint를 위조하지 않는다. 무표식 자료·hidden 자료·다른 tenant 자료는
  계속 숨긴다. 신규 게시와 재게시는 항상 전 문항 검수·finalization·fingerprint가
  일치하는 `verified` 스냅샷만 만들 수 있다. 과거 자료를 다시 게시하면 verified
  스냅샷으로 교체되어 호환 표식의 수명이 끝난다.
- 호환 표식 공개본의 조판 결함은 `repair_legacy_problem_review_pdfs` 관리 명령으로
  고친다. 명령은 `apps.domains.tools.contracts` 공개 경계로 fingerprint와
  renderer를 호출하며 `tools.problem_review` 내부 모듈을 직접 참조하지 않는다.
  exact tenant와 showcase ID를 요구하고 기존 immutable snapshot을
  현재 renderer로만 다시 그린다. 분석 내용·verification·snapshot 시각은 바꾸지
  않으며, fingerprint나 최종 검수 시각도 만들지 않아 표지에 `최종 검수 증표 없음`이
  남는다. footer는 placeholder 대신 정규화된 immutable snapshot의 SHA-256 앞
  12자를 `LEGACY PUBLICATION` identity로 표시한다. 새 R2 객체 업로드 뒤 해당 key를
  다시 내려받아 bytes·page count·SHA-256이
  로컬 렌더와 모두 같은지 확인하고, 트랜잭션 안에서 기존 key와 exact compatibility
  marker를 다시 확인한 경우에만 key를 교체한다. 업로드·readback·재확인 실패 시
  기존 key는 그대로 유지하고 새 객체만 정리한다. 성공 출력에는 교체 전후
  key·bytes·page count·SHA-256을 남긴다. 기존 key는 새 공개 다운로드 시각검수까지
  보존하고, 검수 성공 후 기록한 exact key만 별도 삭제한다.

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
  나눈다. 표지에는 최종 검수 일자와 fingerprint가 함께 남는다. 섹션마다 강제
  새 페이지를 만들지 않고 남은 지면을 기준으로 흐르게 하며, 25문항·핵심
  3문항 회귀 fixture는 불필요한 빈 장 없이 5페이지 안에 조판한다. 진한 표
  헤더는 `TableStyle`에만 의존하지 않고 셀 내부 문단에도 흰색 전용 스타일을
  적용해 raster 출력에서 실제 대비를 보장한다.
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
  0, 문항 누락 0, 배점 합계 교차검산을 하드 게이트로 삼는다. 핵심 문항 X-ray의
  세로 구분선은 하단 `LEARNING SIGNAL` 영역 위에서 끝나야 하며 문구를 가로지르지
  않는다. 출력에서 생명과학 용어 `DNA 양`은 공백을 포함한 표기로 통일한다.
- 홈페이지 공개용 PDF에도 최종 검수된 report version, review fingerprint,
  review completed time을 함께 전달한다. 공개 snapshot JSON에는 내부 검수 필드를
  넣지 않되 동일 검수본에서 생성한 PDF, PPTX, 공개 PDF의 identity를 교차 확인할
  수 있어야 한다.

교사 제공 원본과 보안·품질 경계는
[교사 제공 자료 인벤토리](teacher-provided-source-materials.md)를 따른다.

## 소유 경계

| 책임 | 구현 |
|------|------|
| DB 상태와 버전 | `apps/domains/tools/problem_studio/models.py`의 `ProblemReviewReport` |
| API와 소유권 검사 | `apps/domains/tools/problem_review/views.py` |
| 최종 검수 readiness와 fingerprint | `apps/domains/tools/problem_review/readiness.py` |
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
- `POST /api/v1/tools/problem-review/reports/<report_id>/verification/`
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
- finalization readiness가 부족하거나 현재 draft fingerprint와 저장된 검수
  fingerprint가 다르면 export와 publication 모두 `409`로 막고 최신 readiness를
  돌려준다.
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
