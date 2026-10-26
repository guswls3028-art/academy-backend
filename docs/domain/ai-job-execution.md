# AI·Tools 작업 실행 경계

## 목적과 흐름

매치업, OMR, 교사 AI 기능은 AI queue를, PPT·결정적 문서 도구는 별도 Tools
queue를 사용한다. HTTP 진입점이 현재 tenant와 역할을 검증하고 작업을 DB에
저장한 뒤 commit 이후 메시지를 발행한다. 두 워커는 공통
`academy/framework/workers/ai_sqs_worker.py`에서 실행 claim, 결과 저장,
도메인 callback, 수신 메시지 ACK를 처리한다. Video·Messaging 경계는 별도다.

## 실행 정보와 권한

`prepare_ai_job`은 잠근 DB 작업의 유형·payload·tenant·source·tier로 실행한다.
큐 메시지는 job ID와 수신 영수증을 전달하며 저장된 문서나 실행 엔진을 바꾸지
않는다. 메시지 tenant와 payload tenant의 기존 일치 검사를 유지하고, 저장된
payload도 dict이며 같은 tenant인지 확인한다. 잘못된 입력은 실행 전 실패한다.
현재 살아 있는 claim과 이미 끝난 작업은 기존 preflight/claim fence로 보호한다.
Tools의 지원 유형 검사도 DB에서 읽은 유형을 사용한다.

실행 tier와 메시지를 받은 queue는 다를 수 있다. 예를 들어 Tools 작업의 DB
tier는 basic일 수 있다. 실패 정책은 저장된 tier, visibility 연장과 ACK는
실제로 조회한 queue를 사용한다. 원본 파일·교사 수동 보정·승인·기존 성적을
수정하거나 일괄 재처리하는 변경은 없다.

## 대기·성공·실패·재시도

CPU 워커는 기존 basic/lite 선택 가중치를 유지하면서 선택한 queue와 다른
CPU queue를 먼저 즉시 조회한다. 둘 다 비었을 때만 선택한 queue에서 최대
20초 long poll한다. 빈 queue 대기로 이미 준비된 다른 queue의 작업이 지연되는
경로를 줄이며, 유휴 상태의 반복 호출 루프를 만들지 않는다. Premium 전용과
Tools 전용 워커는 자기 queue만 조회한다. 이는 모델 추론 시간 보장이 아니다.

정상 흐름은 PENDING/RETRYING → RUNNING claim → 저장된 DONE 결과 → 도메인
callback → ACK다. callback이나 ACK가 실패하면 저장된 결과를 유지하고 재배달에서
callback/ACK만 다시 처리한다. 살아 있는 RUNNING claim은 재배달로 중복 실행하지
않으며, 만료 후 새 claim이 생기면 이전 실행은 새 결과를 완료/실패로 덮지 못한다.
상태·진행 조회와 완료 파일 재조회는 각 기능의 소유권 검사를 유지한다.

## PDF·이미지 자원 경계

공통 `PdfDocument.render_page`는 네이티브 픽셀 할당 전에 페이지당 2천만 픽셀,
한 변 8192픽셀 안으로 배율을 계산한다. 일반 A4 300dpi는 유지하고 큰 페이지는
전체를 비례 축소한다. 잘못된 DPI/페이지 크기는 렌더 전에 거부한다. Matchup
좌표는 렌더된 실제 크기를 사용하며 일반 200dpi의 기존 반올림 호환을 유지한다.
텍스트·원본 PDF·저장된 수동 영역을 다시 쓰지 않는다. 이 한계는 PDF 전체의
처리 시간이나 결과 품질을 보장하지 않으며 문항 검수 경로는 유지한다.

PPT 입력 scope, 압축 이미지 한계와 순차 처리는
[PPT 생성기](ppt-question-generator.md#비동기-작업과-저장-경계)에 있다.
인식·분리 정책은 [OMR](omr.md)과 [매치업](matchup.md)이 소유한다.

## 검증

- `tests/test_ai_worker_hardening.py`: 저장된 실행 정보, tenant 불일치, queue 대기.
- `tests/test_pdf_render_budget.py`, `tests/test_segment_dispatcher_pdf_render_budget.py`:
  네이티브 할당 전 상한, A4 해상도, 대형·긴 페이지 좌표/문항/임시 파일.
- `tests/test_ai_job_claim_fence.py`, `apps/domains/ai/tests/test_ai_worker_tenant_guard.py`,
  `apps/domains/ai/tests/test_ai_sqs_worker_callback.py`: DB claim, callback 재시도와 ACK.
- `tests/test_ai_job_terminal_transitions_pg.py`: PostgreSQL 동시 완료·실패 경계.

운영 반영은 정상 backend CI와 immutable candidate의 개발·격리 preprod smoke,
런타임 readback을 따른다. 합성 회귀 통과만으로 모든 실제 스캔/문서의 품질이나
운영 지연 개선을 완료했다고 판정하지 않는다.
