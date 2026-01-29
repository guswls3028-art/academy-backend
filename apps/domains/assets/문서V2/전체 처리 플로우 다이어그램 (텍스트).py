[assets]
  POST /api/v1/assets/omr/objective/pdf/  -> reportlab로 OMR PDF 생성 (벡터)
  GET  /api/v1/assets/omr/objective/meta/ -> constants/layout 기반 meta(JSON, mm 단위) 생성

[worker - 스캔(Primary)]
  입력: 스캔 이미지(페이지가 거의 정렬되어 있음)
  1) meta 확보 (API fetch 또는 payload로 주입)
  2) image_size_px 기준 (page_mm -> px) 스케일 계산
  3) meta ROI(mm) -> ROI(px) 변환
  4) detect_omr_answers_v1(roi 기반) -> 답안 추출

[worker - 촬영/영상(Secondary)]
  입력: 사진/프레임(기울어짐/원근)
  1) warp_to_a4_landscape로 문서 외곽 검출 + 원근보정(페이지 전체 정렬)
  2) meta ROI(mm) -> ROI(px) 변환
  3) detect_omr_answers_v1
  4) warp 실패 시 (auto 모드라면) 기존 yolo/opencv segmentation fallback 가능


6) 스캔 vs 촬영 처리 차이 요약

스캔: “이미 페이지가 정렬/크롭” 가정 → meta ROI를 바로 적용

촬영/영상: 문서 영역 검출 + 원근보정(warp)으로 “페이지 전체 정렬” 만든 뒤 → meta ROI 적용
(warp 실패 시 auto로 fallback 가능)

7) 수동 QA 체크리스트 (운영 기준)
assets

 POST /api/v1/assets/omr/objective/pdf/ 기존과 동일하게 동작 (10/20/30)

 PDF 확대해도 원/선이 깨지지 않음(벡터)

 3단 / 영역1 로고+식별자 / 영역2-3 객관식 배치 유지

 GET /api/v1/assets/omr/objective/meta/?question_count=10 응답 JSON 확인

 units = "mm"

 page.size.width/height 값 존재

 identifier.bubbles: 8*10=80개

 questions: 10/20/30 개 정확히 존재

 각 question에 roi(x,y,w,h) 존재

worker (스캔)

 정렬된 스캔 JPG/PNG를 넣었을 때 omr_grading이 answers를 반환

 blank/multi/ok 상태가 정상적으로 분기됨

worker (촬영/영상 프레임)

 기울어진 사진에서 warp 성공 → aligned=true로 반환

 warp 실패 사진은 mode=photo에서 실패, mode=auto에서는 fallback 설계 여지 확인
