V2단계 마스터 프롬프트이다. 
V1 단계는 완료 되었다고 가정한다. 

문서V1의 프롬프트를 최신화하고 이 문서를 단일진실로 고정한다. 

역할:
너는 대기업 실무급 Django/DRF + Computer Vision 백엔드 엔지니어다.
교육/OMR/문서인식 시스템을 실제 서비스 수준으로 설계·구현한 경험이 있다고 가정한다.

목표:
이미 구현된 assets 도메인을 “실사용 가능한 최종 상태”로 완성하고,
즉시 AI worker(OCR/OMR/영상 처리)와 자연스럽게 연결될 수 있도록 보강하라.
예제/가이드가 아닌 실제 운영 가능한 코드와 파일 단위 패치가 필요하다.

절대 규칙:
- 아래 “단일진실 고정 문서”와 기존 코드 구조를 1바이트도 위반하지 말 것
- assets 도메인의 책임을 절대 넘지 말 것
- submissions / exams / results 도메인에 코드 추가 금지
- 외부 SaaS 호출 금지
- 비동기 큐 도입 금지 (요청 → 즉시 결과)
- 설계 변경은 v2가 아닌 한 허용되지 않음

========================
[현재 확정된 사실]
========================

1) assets 도메인은 이미 다음을 완성했다.
- OMR 객관식 답안지 PDF 생성 (10/20/30문항)
- A4 / Landscape / 3단 레이아웃
- reportlab 벡터 PDF
- constants.py = 좌표/수치 단일진실
- 로고 optional 업로드
- API: POST /api/v1/assets/omr/objective/pdf/

2) 실사용 조건
- 답안지는 99% 스캔 이미지
- 일부 상황에서는 휴대폰 촬영 이미지 또는 동영상 프레임 사용
- 향후 과제/다른 답안지에서도 재사용 예정

3) AI worker는 이미 존재하며,
- OMR grading 엔진은 ROI(bbox) 기반 입력을 받는다
- 촬영/영상 대응을 위해 opencv / yolo / warp 기반 처리가 가능하다

========================
[이번 작업의 핵심 요구]
========================

A. assets 도메인 보강 (필수)
assets는 “정적 산출물 생성” 책임만 가지면서,
OMR 답안지를 **기계가 읽을 수 있도록 하는 구조 정보(meta)** 를 제공해야 한다.

구현해야 할 것:

1) OMR 템플릿 메타데이터 생성 로직
- constants.py / layouts 기준으로 계산
- mm 단위 좌표 유지
- 아래 정보를 반드시 포함:
  - page_size (A4 landscape)
  - question_count (10/20/30)
  - identifier 영역:
      - 각 digit(1~8)별
      - 숫자 0~9 각각의 bubble 중심 좌표/반지름
  - objective questions:
      - question_number
      - 각 choice(A~E) bubble 중심 좌표/반지름
      - question-level ROI bbox (5개 버블을 감싸는 박스)

2) API 추가 (stateless)
- GET /api/v1/assets/omr/objective/meta/
- query param: question_count=10|20|30
- 응답: JSON (위 meta 전체)
- DB 저장 금지
- 이 meta는 “채점 결과가 아닌 산출물 구조 정보”임을 명확히 유지

3) 설계 원칙
- meta는 PDF와 1:1로 대응되어야 함
- PDF가 바뀌면 meta도 반드시 같이 바뀌는 구조
- constants.py가 유일한 좌표 단일진실

========================
B. worker 연계 관점 설계 (구현 또는 명세)
========================

assets 작업을 마친 뒤, AI worker가 다음 두 경로를 모두 지원할 수 있도록
“연결 설계 또는 코드”를 제공하라.

1) 스캔 이미지 경로 (Primary, 99%)
- API → meta 조회
- 스캔 이미지는 A4 정렬 가정
- meta ROI를 그대로 적용하여 OMR grading
- segmentation/yolo 사용하지 않음

2) 촬영/동영상 경로 (Secondary)
- 이미지 또는 프레임에서 문서 영역 검출
- 원근 보정(warp)으로 A4 평면화
- 동일 meta ROI 적용
- 실패 시 기존 yolo/opencv segmentation fallback

※ worker에서의 판단/추출만 담당
※ 정답 비교/점수 계산은 API(results) 영역으로 넘긴다는 철학 유지

========================
[산출물 요구 형식]
========================

1) assets 도메인
- 새로 추가/수정되는 파일:
  - 경로 + 전체 코드 제공
- urls.py / views.py / services.py 단위 명확히 분리
- 기존 objective/pdf API는 변경하지 말 것

2) worker 쪽
- 실제 코드 또는
- “이대로 구현하면 된다” 수준의 정확한 파일 단위 명세 제공
- assets meta와 어떻게 연결되는지 명확히 기술

3) 마지막에 반드시 포함:
- 전체 처리 플로우 다이어그램(텍스트)
- 스캔 vs 촬영 처리 차이 요약
- 수동 QA 체크리스트 (운영 기준)

========================
[최종 목표 선언]
========================

이 프롬프트에 따라 구현된 결과물은 다음을 만족해야 한다.

- 학원/학교에서 바로 배포 가능한 OMR 답안지
- 스캔/촬영/영상 모두 대응 가능한 인식 파이프라인
- assets ↔ worker 간 책임 분리가 명확
- 좌표/레이아웃 단일진실 유지
- v1 스펙을 절대 수정하지 않고 확장 가능
- 대기업 실무 리뷰를 통과할 수 있는 구조

이 조건을 만족하는 “완성 산출물”을 제시하라.
