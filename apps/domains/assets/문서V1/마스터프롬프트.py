역할: 너는 대기업 실무급 Django/DRF 백엔드 엔지니어다. 아래 “단일진실 고정 문서”를 1바이트도 위반하지 말고, 현재 레포 구조를 존중하여 완성된 상품(바로 프론트에서 호출 가능한 PDF 생성 API)을 구현하라. 예제/연습/가이드가 아니라 실제 코드와 파일 단위 패치가 필요하다.

전제: 나는 이미 다음 폴더를 생성했다.
apps/domains/assets/
apps/domains/assets/omr/
apps/domains/assets/omr/layouts/
apps/domains/assets/omr/services/
apps/domains/assets/omr/views/

단일진실 고정 문서 (절대 변경 금지):
- 공통 철학: 라벨/번호/설명은 좌측 정렬, 입력/마킹은 우측 정렬
- A4, 흑백, 벡터 PDF, 커스텀 없음, 로고만 업로드로 교체 가능
- 시험지 1 (객관식 전용 OMR): 앞면만, Landscape, 3단 레이아웃
  | 영역1 | 영역2 | 영역3 |
  | 로고  | 객관식| 객관식|
  | 식별자|      |      |
  식별자: 휴대폰번호 뒤 8자리(010 제외), 자리별 0~9 버블
  객관식: 5지선다, 문항번호 좌측, 버블 우측 정렬
- 시험지 1 변형 버전: 객관식 문항 수만 10/20/30 세 가지. 레이아웃/철학 동일. 간격은 문항 수 적을수록 더 넉넉.
- 시험지2(참고): 이번 구현 범위에서 제외. (객관식 버전 10/20/30만 구현)

API 고정:
- POST /api/v1/assets/omr/objective/pdf/
- multipart/form-data
  - logo: optional image
  - question_count: enum {10,20,30} (필수)
- 응답: application/pdf, 즉시 다운로드
- 서버는 DB 저장을 하지 않는다(stateless). (캐시도 1차 제외)

기술/품질 요구(대기업 실무급):
- reportlab로 벡터 PDF 생성
- 좌표는 mm 단위로 관리(가독성/인쇄 안정성)
- constants.py가 수치 단일진실(버블 반지름, 간격, 마진 등)
- layouts는 좌표/배치만 담당, 비즈니스 로직 최소화
- pdf_generator는 reportlab wrapper로 렌더링만 담당
- view는 인증/입력검증/응답만 담당(얇게)
- 에러 응답은 DRF 표준으로 400/415 등 명확하게
- logo는 이미지 파일만 허용(png/jpg/webp 등), 비정상이면 400
- question_count가 잘못되면 400
- 코드 스타일은 프로젝트 기존 패턴을 존중(IsAuthenticated 등)

현재 레포에 맞춰 반드시 해줘야 하는 것:
1) apps/domains/assets/apps.py 작성 (AppConfig)
2) apps/domains/assets/urls.py 작성 (omr include)
3) apps/domains/assets/omr/urls.py 작성 (objective endpoint)
4) apps/domains/assets/omr/constants.py 작성
5) apps/domains/assets/omr/layouts/objective_v1_10.py, objective_v1_20.py, objective_v1_30.py 작성
6) apps/domains/assets/omr/services/pdf_generator.py 작성
7) apps/domains/assets/omr/views/omr_pdf_views.py 작성
8) 프로젝트 라우팅 연결 지점까지 “어디 파일을 어떻게 수정해야 하는지”를 구체적으로 제시
   - 예: apps/api/v1/urls.py 또는 apps/api/config/urls.py 등 실제 이 레포 구조에서 맞는 위치를 찾아 안내(경로는 내가 보여준 tree 기준으로 판단)
9) pip requirements(필요시) 안내

산출물 형식(필수):
- 파일별로 “경로” + “전체 코드”를 제공
- 어떤 기존 파일을 수정해야 하면: 수정 전/후 또는 patch 형태로 제공
- 마지막에 “프론트 호출 예시(fetch)”를 1개 제공
- 마지막에 “수동 QA 체크리스트” 제공 (PDF 확인 포인트)

주의:
- 시험지2는 이번에 구현하지 않는다.
- submissions/results/exams 도메인에 코드를 넣지 않는다. assets 도메인에만 구현한다.
- 외부 서비스 호출 금지.
- 비동기 작업/큐 사용 금지. 요청 즉시 PDF 반환.
- 이미지 변환 라이브러리 추가 금지(가능하면 reportlab 기본으로 처리).

2) AI가 반드시 내야 하는 “완성 산출물” 체크리스트

너(또는 내가)가 위 명령어를 실행했을 때 결과물은 반드시 아래를 포함해야 함.

 assets 앱(AppConfig) + urls + include 경로 정합성

 /api/v1/assets/omr/objective/pdf/ 엔드포인트 동작

 multipart 입력검증(logo 타입, question_count enum)

 pdf는 A4 landscape + 3단 레이아웃

 영역1에 로고/식별자

 영역2/3에 객관식 (10/20/30 분기)

 좌측 정렬/우측 정렬 철학 준수

 reportlab 벡터 PDF

 프론트 fetch 예시 제공

 QA 체크리스트 제공