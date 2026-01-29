역할:
너는 대기업 실무급 Backend/Computer Vision 엔지니어다.
교육용 OMR 시스템을 실제 학원/학교에 상용 배포한 경험이 있다.

현재 상태:
- assets 도메인: v1 완성, PDF + meta 고정
- worker 도메인: OMR v1 인식 완료 (objective + identifier)
- 단일진실 문서 및 책임 분리 모두 확정
- 기능 추가는 금지, 구조 변경 금지

목표:
현재 시스템을 “실제 상품으로 즉시 출시 가능한 상태”로 봉인하라.
기능 개발이 아니라 **운영 기준·정책·실패 통제**를 완성하는 단계다.

절대 규칙:
- V1 단일진실 문서 1바이트도 위반 금지
- assets / worker 책임 변경 금지
- 신규 기능 추가 금지
- 구조 리팩토링 금지
- 튜닝/정책/운영 기준만 허용

해야 할 작업:

1) OMR v1 운영 기준 최종 고정
- blank / ambiguous / low_confidence 기준값 확정
- identifier 기준값 확정
- scan/photo/auto 모드별 추천 운영값
- 코드 기준(default) + 이유 명시

2) 실패 시나리오 정의
- 어떤 경우에 manual_review로 보내는가
- identifier 실패 시 정책
- meta fetch 실패 / warp 실패 / 인식 실패 분기

3) results 연계 운영 계약 검증
- payload 재확인
- retry / idempotency 실제 운영 관점 검토
- 장애 발생 시 worker 동작 정책

4) 실운영 QA 체크리스트 (최종본)
- 인쇄
- 스캔
- 촬영
- 영상 프레임
- 운영자 관점 점검 항목

5) 최종 선언
- 왜 이 상태가 “상품 출시 가능”인지
- 어디까지가 v1 책임인지
- v2에서만 가능한 확장 항목 명시

산출물 형식:
- 코드 변경이 필요한 경우: patch 단위
- 정책/기준은 문서 형태로 명확히 정리
- 마지막에 “운영자용 요약” 1페이지 제공

이 작업이 끝나면:
- 실제 학원/학교에서 바로 계약·배포 가능해야 한다.

5) 실운영 QA 체크리스트 (최종본)
5-1. 인쇄(학원/학교)

프린터 2종 이상(레이저/잉크젯) 출력 테스트

300dpi 스캔 기준으로 버블 원이 깨지지 않는지(벡터 유지)

로고 포함/미포함 모두 확인

용지 가장자리 크롭 발생 시에도 ROI가 크게 벗어나지 않는지

5-2. 스캔(Primary, 99%)

제조사 다른 스캐너 2종

밝기 자동보정 ON/OFF

기울어짐 1~3도 수준

결과 확인:

blank 폭증 없음(특히 연필 농도 낮게 마킹한 샘플)

ambiguous가 일부 발생해도 “상식적 수준”

identifier 8자리 정상 추출률 확인

5-3. 촬영(Secondary)

형광등 반사/그림자 케이스

15~30도 원근

손떨림/미세 블러

결과 확인:

mode=photo에서 warp 실패 시 즉시 error 반환되는지

mode=auto에서 warp 실패해도 aligned=false로 결과가 나오는지

aligned=false 결과는 manual_review로 보내는지(results 정책)

5-4. 영상 프레임

프레임 추출 stride가 큰 케이스(블러 많음)

warp 성공률 확인

warp 실패 프레임은 auto에서 fallback 결과가 나오는지

identifier 실패 시 manual_review 라우팅되는지

5-5. 운영자 관점 점검

meta API 장애 시 debug에 원인 남는지(meta_fetch_error)

results ingest 중복 호출 시에도 안전한지(idempotency)

“오류/리뷰/정상” 케이스가 운영 화면에서 구분 가능한지

6) 최종 선언: 왜 이 상태가 “상품 출시 가능”인가
6-1. 상품 완성의 기술적 근거(봉인 조건 충족)

인쇄/배포 가능한 답안지(assets): 벡터 PDF + 레이아웃 단일진실 + meta 제공 완료

실인식 파이프라인(worker): scan/photo/auto 정책 고정 + warp/fallback + ROI 기반 추출 완료

운영 실패 통제: 실패는 error로, 품질 이슈는 status로 → results가 manual_review 라우팅

책임 분리(대기업 리뷰 포인트):

assets: 생성 + meta(mm)

worker: mm→px 변환 + 판단/추출

results: 정답 비교/점수/저장/리뷰 라우팅

확장 가능(v2): v1 스펙 수정 없이 템플릿/레이아웃만 추가하면 동일 파이프 재사용 가능

6-2. v1 책임 경계(고정)

v1에서 worker는 정답 비교/점수 계산/학생 매칭 절대 안 함

identifier는 “추출 결과”일 뿐, 학생 매칭은 results

6-3. v2에서만 가능한 확장(명확히 분리)

다른 용지/다른 템플릿(세로/다페이지)

필기/서술 인식

adaptive threshold(학생/스캐너별 동적 보정)

문항 유형 확장(복수정답 허용 등)

템플릿 자동 분류(페이지 타입 auto-detect)

7) 운영자용 요약 (1페이지)

기본 모드: auto

스캔이 대부분이면: auto로 두고 aligned=true 비율만 모니터링

촬영 전용 플로우(학생 앱 등)는: photo 강제 (warp 실패를 즉시 알림)

manual_review 추천 라우팅 조건:

identifier.status != ok

blank ≥ 5 또는 ambiguous ≥ 3 또는 low_confidence ≥ 5

aligned=false

장애 대응:

meta 장애 → legacy questions 있으면 최소 서비스 유지

results 장애 → worker 재시도 (idempotency 필수)