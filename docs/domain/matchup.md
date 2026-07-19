# AI 매치업 도메인 가이드

## 1. 제품 정의

매치업은 강사가 평소 수업에서 사용한 교재, 자체 제작 자료, 프린트, 참고 PDF와 실제 학교 시험지를 비교해 문제 유사도와 적중률을 리포트로 만드는 기능이다.

핵심 사용자는 강사다. 강사는 시험 전에는 수업자료를 참고자료로 축적하고, 시험 당일 학생이 제출한 학교 시험지를 업로드한다. 시스템은 수업자료의 문항과 실제 시험 문항을 분리하고, 서로의 유사도와 출처를 매칭해 보고서 형태로 제공한다.

이 리포트는 두 가지 목적을 동시에 가진다.

- 다음 수업과 자료 개선을 위한 분석/통계 자료
- 강사의 수업 시스템과 적중 근거를 보여주는 홍보 자료

따라서 매치업의 성공 기준은 단순히 “비슷한 문제를 몇 개 찾았는가”가 아니다. 강사가 실제 시험지를 받은 뒤 짧은 시간 안에 신뢰 가능한 적중 리포트를 만들 수 있어야 한다.

## 2. 기본 워크플로우

1. 강사가 평소 수업에 사용한 자료 PDF 또는 이미지를 자료실/매치업 영역에 업로드한다.
2. 시험 당일 학생이 가져온 실제 학교 시험지를 강사가 업로드한다.
3. 시스템이 각 문서를 문항 단위로 분리한다.
4. 시스템이 수업자료 문항과 학교 시험 문항의 유사도를 계산한다.
5. 강사는 자동 매칭 결과를 검수하고 필요하면 문항 영역을 직접 수정한다.
6. 시스템은 유사 문항, 적중률, 출처, 통계 정보를 리포트로 만든다.

## 3. 문항 분리의 현재 상태

문항 분리는 현재 매치업의 병목이다. 자동 분리는 일부 문서에서 잘 동작하지만, 모든 수업자료/시험지 형식을 안정적으로 처리하는 상태는 아니다. 특히 한 문서를 한 번 잘라야 할 때 3천~5천 문항 규모가 될 수 있으므로, 수동 자르기만으로 운영하는 것은 지속 가능하지 않다.

현재 시스템에는 다음 경로가 공존한다.

- 텍스트 기반 PDF 분리: PyMuPDF 텍스트 블록과 휴리스틱을 사용한다.
- OCR/OpenCV 분리: 이미지형 PDF, 스캔본, 사진에서 후보 영역을 만든다.
- YOLO 문항 감지: `/app/models/yolo_v11_combined.pt`를 기본 모델로 사용하는 AI worker 경로가 있다.
- VLM fallback: Gemini 기반 VLM 어댑터가 구현되어 있으나 환경변수/테넌트 게이트 설정에 따라 실제 사용 여부가 달라진다.
- 수동 보정: 강사가 페이지별 상태를 지정하거나 직접 박스를 자르는 UI가 있다.
- Proposal/검수 흐름: 자동 후보를 사람이 승인/거절하는 구조가 일부 구현되어 있으나 운영 게이트가 필요하다.

수동 자르기·붙여넣기로 만든 `MatchupProblem(meta.manual=true)`의 OCR/embedding도
원본에 자동 반영하지 않는다. `matchup_manual_index` callback은
`ProblemSegmentationProposal(proposal_kind=manual_index)`만 만들고, 검수 화면에서
학원장이 승인한 뒤에만 대상 수동 문항에 반영한다. callback·재색인 management
command가 수동 문항의 `text`, `embedding`, `image_embedding`, `meta`를 직접
UPDATE하는 경로는 금지한다.

페이지 전체를 하나의 문항으로 넣는 fallback은 품질을 숨기는 방식이므로 정답 경로가 아니다. 문항 경계가 틀리면 이후 유사도, 적중률, 리포트 신뢰도가 모두 무너진다.

[CURRENT 2026-06-20] Tenant 2 과거 실사용 자료 중 손촬영 사진을 제외한 PDF/스캔본/텍스트 PDF는 v55 full-display 감사에서 운영 baseline을 닫았다. 수동 GT가 있는 61개 문서의 물리 문항 기준 `physical_missed_count=0`, `physical_recall=1.0`이며, raw miss 11건은 중복 GT row로 설명된다. 재현 절차와 합격 기준은 `docs/operations/runbooks/matchup-segmentation-qa.md`를 정본으로 본다. 새 자료 유형이나 손촬영 사진은 이 baseline에 자동 포함하지 않고 별도 감사로 편입한다. 숨은 버그 후보와 다음 실행 단위는 `docs/refactor/matchup-segmentation-risk-backlog.md`에 [PROPOSED]로 둔다.

## 4. 잘 되는 유형과 취약한 유형

상대적으로 잘 되는 유형:

- 텍스트가 살아 있는 PDF
- 문항 번호와 본문 경계가 명확한 단일/2단 구성
- 반복 레이아웃이 안정적인 자료
- 여백 번호, 문항 anchor, 페이지별 번호 재시작 패턴이 뚜렷한 자료
- 깨끗한 스캔본 중 문제 영역과 해설/필기/채점 표시가 잘 분리된 경우

취약한 유형:

- 학생 답안 사진, 휴대폰 촬영본, 기울어진 이미지
- 손글씨, 채점 표시, 풀이 흔적이 문항 경계와 섞인 스캔본
- 내신 교재 내지처럼 개념, 예제, 본문, 해설, 단원 장식, 여백 번호가 섞인 자료
- 지문 하나에 여러 하위 문항이 붙는 구조
- 해설지, 정답지, 표지, 목차, 단원 소개 페이지
- 4분할/복합 레이아웃처럼 현재 데이터가 부족한 유형
- 문항 일부만 잡히거나 여러 문항이 한 박스에 합쳐져도 리포트 품질에 치명적인 문서

기존 문서나 리포트에서 `clean_pdf_dual`을 일반적으로 안정적인 유형처럼 표현한 내용은 과장될 수 있다. 실제 수동 보정 이력에서는 깨끗한 2단 PDF라도 자료 성격에 따라 대량 수동 보정이 발생했다.

## 5. 평가 기준

모델 mAP만으로 매치업 품질을 판단하면 안 된다. 강사가 느끼는 실제 성공 기준은 다음에 가깝다.

- 문서당 검수 시간이 크게 줄었는가
- 문항 누락, 병합, 조각 박스가 리포트 신뢰도를 해치지 않는가
- 자동 결과를 강사가 빠르게 승인/수정할 수 있는가
- 수동 수정 이력이 다음 자동 분리 개선에 재사용되는가
- 실제 학교 시험지 기준 적중률 리포트가 납득 가능한가

자동 분리 결과는 “최종 정답”이 아니라 “검수 가능한 후보”로 취급해야 한다. 운영 목표는 모든 문서를 100% 무인 자동화하는 것이 아니라, 강사의 반복 노동을 문서당 몇 분 수준으로 줄이는 것이다.

## 6. 개선 방향

우선순위는 다음 순서다.

1. 실제 사용 문서별로 자동 분리 결과, 수동 보정량, 실패 유형을 저장한다.
2. 페이지별 `auto/manual/skip` 상태와 문항 후보 승인/거절 흐름을 운영 경로로 연결한다.
3. 문항 박스 미세 조정 UI를 강사가 빠르게 사용할 수 있게 유지한다.
4. YOLO 단일 모델만 키우는 방식이 아니라, 휴리스틱, 레이아웃 분류, VLM 검증/보정, 수동 보정 재학습 데이터를 함께 사용한다.
5. VLM은 모든 페이지를 대체 처리하는 기본 엔진보다, 실패 위험이 큰 페이지를 검증하거나 후보를 보정하는 선택적 경로로 먼저 도입한다.
6. 모델 재학습은 수동 보정 이력과 원본 자동 후보의 연결 정보가 충분히 쌓인 뒤 진행한다.

OpenAI의 저가 VLM 모델을 도입하려면 현재 Gemini 기반 VLM adapter와 별개로 OpenAI adapter를 추가하고, API 키/프로젝트/결제 설정 및 테넌트별 사용량 제한을 별도로 연결해야 한다. Codex 로그인 인증 정보가 서비스 백엔드의 운영 API 키로 자동 재사용되지는 않는다.

Gemini VLM을 먼저 운영 실험할 때는 전체 문서를 VLM primary로 바꾸기보다 다음 게이트를 조합한다.

- `MATCHUP_VLM_FILL_EMPTY_PAGES=1`: 자동분리 결과가 없는 페이지만 Gemini bbox 보정
- `MATCHUP_VLM_PAGE_ROLE_FILTER=1`: text-PDF의 표지/목차/해설/정답 페이지를 Gemini Flash-Lite text page-role로 제외
- `MATCHUP_HYBRID_VLM_TENANTS=<tenant_id>`: YOLO/OCR 박스를 Gemini로 검증
- `MATCHUP_HYBRID_VLM_SOURCE_TYPES=student_exam_photo,school_exam_pdf,other`: 워크북 main 단위 cut의 거짓 reject 방지
- `MATCHUP_VLM_FORCE_PRIMARY_TYPES=`: 필요하면 기존 commercial/school 전체 VLM primary를 비우고 선택적 VLM만 검증
- `MATCHUP_VLM_PAGE_ROLE_USE_VISION_FALLBACK=0`: role-only 판별에서 비싼 vision bbox 호출을 기본 차단

실제 호출에는 `GEMINI_API_KEY`, `MATCHUP_VLM_TEXT_ADAPTER=gemini_flash_lite`, `MATCHUP_VLM_VISION_ADAPTER=gemini_flash` 설정이 필요하다.

## 7. 운영상 주의

- 문항 분리 실패를 “문항 없음” 또는 “페이지 전체 문항”으로 조용히 통과시키면 안 된다.
- 해설지/정답지/표지/목차는 매칭 대상에서 제외하거나 별도 상태로 분류해야 한다.
- 수업자료와 실제 학교 시험지는 역할이 다르므로, 문서의 `source_type`과 리포트에서의 의미를 구분해야 한다.
- 학생 QnA 유사문제 검색은 매치업 DB를 활용하는 부가 흐름일 수 있으나, 매치업 도메인의 중심 목적은 수업자료 대비 실제 시험 분석 리포트다.

## 8. 실사용 레벨 문항분리 설계

[COMPLETED 2026-05-16] 현재 dispatcher는 `classify_paper_type()`이 페이지를 `non_question`으로 판정하면 해당 페이지를 자르지 않고 skip한다. T1 doc 615에서 확인된 남은 실패 유형은 `CHAPTER / 개념 / 추가 설명 / 정의형 본문` 페이지가 섹션 번호 `1)`, `2)` 때문에 문제 1개로 잘리는 false-positive였다. 이를 막기 위해 `question_splitter.is_non_question_page()`에 개념/본문 페이지 전용 gate를 추가했다. 단, `①~⑤`, `옳은 것`, `답하시오`, `서술하시오` 같은 강한 문항 신호가 있으면 문제 페이지로 유지한다.

[CURRENT] 양식 다양성에 대한 기본 구조는 다음 순서다.

1. 페이지 역할 분류: 표지, 목차, 개념 본문, 해설, 정답, 실제 문제를 먼저 가른다.
2. 레이아웃 라우팅: `source_type`, `paper_type`, 텍스트/스캔 여부, 1단/2단/4분할/사진 여부로 splitter 전략을 나눈다.
3. 후보 생성: 텍스트 PDF, OCR/OpenCV, YOLO, VLM을 한 엔진으로 통일하지 않고 유형별 후보 생성기로 사용한다.
4. 품질 게이트: 빈 페이지, 과잉 박스, 조각 박스, 비문항 박스는 조용히 인덱싱하지 않고 skip 또는 검수 후보로 남긴다.
5. 운영 평가: 대표 PDF golden set에서 page-role, 문항 수, 박스 위치, false-positive/false-negative 페이지 목록을 비교한다.

[PROPOSED] 실사용 기준까지 올리려면 golden set 평가 runner가 필요하다. 최소 세트는 `academy_workbook`, `commercial_workbook`, `school_exam_pdf`, `student_exam_photo`, `scan_dual`, `clean_pdf_dual`, `concept/explanation`, `answer_key`, `cover/index`를 각각 포함해야 한다. 평가는 모델 mAP가 아니라 문서별 검수 부담을 줄이는 지표로 본다: 문제 페이지 누락, 비문항 페이지 오인식, 문항 수 차이, bbox IoU, low-quality 비율, 수동 수정 필요 페이지 목록.

[PROPOSED] 수동 보정 이력은 학습 데이터로 연결해야 한다. 자동 후보 bbox와 강사 최종 bbox를 같이 저장해야 같은 양식의 다음 업로드에서 fingerprint/profile 기반 재사용이나 모델 재학습이 가능하다.
