6) worker ↔ assets 경계(책임) “고정” 확인

이미 코드로 고정됨:

assets: meta를 mm로 제공 (단일진실)

worker: meta(mm) → px 변환 (roi_builder.py, meta_px.py) + 판단/추출 (engine.py, identifier.py)

worker는 점수/정답 비교 없음 → 결과 도메인(results)로 넘길 계약만 정의

7) results API 계약 정의 (구현 금지, 명세만)

results 도메인에 코드 추가 금지 조건 준수 → 정확한 계약 명세만 제시

7.1 Worker → API (results) 제안 엔드포인트

POST /api/v1/internal/results/omr/ingest/

인증: 기존 internal worker token 방식과 동일 (X-Worker-Token)

Content-Type: application/json

Request payload (고정)
{
  "submission_id": 123,
  "template": {
    "version": "objective_v1",
    "question_count": 30
  },
  "input": {
    "mode": "scan|photo|auto",
    "aligned": true
  },
  "extracted": {
    "identifier": {
      "identifier": "12345678",
      "raw_identifier": "12345678",
      "confidence": 0.91,
      "status": "ok|ambiguous|blank|error",
      "digits": [
        {
          "digit_index": 1,
          "value": 1,
          "status": "ok|ambiguous|blank",
          "confidence": 0.92
        }
      ]
    },
    "answers": [
      {
        "version": "v1",
        "question_id": 1,
        "detected": ["B"],
        "marking": "single",
        "confidence": 0.83,
        "status": "ok|blank|ambiguous|low_confidence|error"
      }
    ]
  },
  "debug": {
    "meta_used": true,
    "worker_version": "ai_worker_v1"
  }
}

Response payload (고정)
{
  "status": "ok",
  "accepted": true,
  "submission_id": 123,
  "next_action": "grade_async_in_results|grade_now|manual_review"
}

7.2 Retry / error 정책

worker는 네트워크 실패 시:

job 자체를 failed로 두지 말고 internal retry(현재 루프 구조상 다음 폴링에 재시도 가능)

단, 같은 submission_id 중복 ingest 가능성 → results는 idempotency 키 필요

results 측 idempotency:

key: submission_id + template.version + template.question_count

동일 key 재요청은 “이미 처리됨”으로 200 응답

7.3 identifier + answers 결합 방식

results는 identifier를 “학생 매칭 정보”로 사용 가능하지만

assets/worker는 매칭을 수행하지 않는다

results가 최종적으로:

submission.student_identifier 매칭

answer scoring

score persist

manual review routing
을 수행

8) 전체 처리 플로우 다이어그램 (텍스트)
[assets]
  POST /api/v1/assets/omr/objective/pdf/       -> 벡터 OMR PDF 생성
  GET  /api/v1/assets/omr/objective/meta/      -> 템플릿 meta(mm) 제공

[worker: omr_grading]
  input: 스캔 이미지 or 촬영 이미지 or 영상 프레임 (download_url)

  1) meta 확보
     - payload.template_meta 있으면 사용
     - 없으면 payload.template_fetch.base_url로 assets meta 호출
     - 실패 시 graceful fallback: payload.questions(legacy)로만 진행

  2) mode 결정
     - scan  : warp 금지, 바로 진행
     - photo : warp 필수, 실패하면 error 반환
     - auto  : warp 시도, 실패하면 scan처럼 진행 (fallback 허용)

  3) aligned 이미지 기준 meta(mm)->px 변환
     - questions ROI(px) 생성
     - identifier 버블 ROI(px) 생성

  4) 추출
     - identifier: digit별 0~9 fill 점수 -> 8자리 + 상태
     - answers   : question ROI -> A~E fill 점수 -> 상태

  5) 결과 반환
     - worker 결과는 “판단/추출”까지만 포함
     - scoring/정답 비교 없음

[results (API)]
  POST /api/v1/internal/results/omr/ingest/
    - identifier 매칭/검증
    - answer scoring
    - persist
    - manual review 라우팅

9) 스캔 vs 촬영 처리 차이 요약 (운영 고정)

scan (99%)

“이미 페이지 정렬/크롭” 가정

warp 안 함

meta ROI를 그대로 적용

빠르고 안정적

photo / video frame (예외 케이스)

warp로 “페이지 전체 정렬”을 먼저 만든다

그 다음 meta ROI 적용

photo는 warp 실패 시 즉시 실패(운영에서 확실한 신호)

auto는 warp 실패 시 scan처럼 진행(현장 유연성)

10) 실운영 수동 QA 체크리스트
assets

POST /api/v1/assets/omr/objective/pdf/ 기존 동작 동일

GET /api/v1/assets/omr/objective/meta/?question_count=10|20|30

units="mm"

identifier.bubbles = 80개(8*10)

questions = 10/20/30 정확

각 question에 roi 존재

worker — 스캔

mode=scan

aligned=false

identifier.status가 대부분 ok/ambiguous로 나오고 blank 폭증 없음

answers status 분기(ok/blank/ambiguous/low_confidence)가 합리적

worker — 촬영

mode=photo

warp 성공 사진: aligned=true

warp 실패 사진: failed("warp_failed_for_photo_mode") 반환

mode=auto

warp 성공: aligned=true

warp 실패: aligned=false 상태로도 answers/identifier가 산출(단, 품질은 낮을 수 있음)

운영 튜닝 점검

blank가 과다하면:

OMR blank_threshold / Identifier blank_threshold를 소폭 하향

ambiguous가 과다하면:

conf_gap_threshold를 소폭 하향

multi가 과소검출이면:

multi_threshold를 소폭 하향

low_confidence가 과다하면:

low_confidence_threshold를 소폭 하향