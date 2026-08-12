# 제품 사용 분석 잔여 작업

**상태:** `hakwonplus` 28일 파일럿 진행 중, 2026-08-26 첫 판정 예정

**현재 계약:** [제품 사용 분석](../domain/product-usage-analytics.md)

**DB 판단 기준:** [DB 확장·테넌트 분리](../infrastructure/database-scaling-and-tenant-isolation.md)

## 지금 완료된 범위

- 익명 원본 이벤트와 일별 actor 집계 모델·migration
- tenant·membership·HMAC·idempotency·PII 방어가 있는 batch API
- platform-only overview와 single-tenant small-cell suppression
- 22개 기능·68개 인증 route registry
- 화면 방문·10초 참여·CTA 노출/클릭
- 선생님 출석·성적·시험/과제 대상·알림톡, 학생 시험/과제 제출,
  클리닉 대표 task funnel
- 메모리 queue, 제한된 1회 재시도와 본 업무 fail-open
- rollup, dry-run-first purge와 tenant DB capacity report 명령
- 전용 HMAC key의 값 비노출·exact-version 수렴 스크립트
- GitHub OIDC + SSM 기반 일별 rollup·30/400일 보존 자동화
- 내부 `hakwonplus` 단일 tenant 활성화와 외부 tenant OFF 확인
- sampled DB telemetry 제어, 24시간 비용 집계와 hard gate 자동 차단
- 일별 28일 파일럿 JSON 보고서 90일 artifact 보존

## 현재 파일럿 범위 밖

- 대표 tenant 추가 활성화
- 기존 HMAC secret 교체
- 메뉴·CTA 문구·위치·우선순위 변경
- Multi-AZ, read replica, `DATABASE_ROUTERS`
- tenant schema/database, data copy, dual-write, routing 전환

## 파일럿

1. `hakwonplus`는 2026-07-30 정식 플랫폼 API로 활성화되었다.
2. daily report로 활성 tenant 범위, 적격/합성/대리 이벤트, task 성공률,
   DB 시간·write 비중과 90일 저장 전망을 관측한다.
3. hard gate가 넘으면 daily maintenance가 exact pilot만 즉시 해제하고
   운영 감사 로그와 실패 artifact를 남긴다.
   hard gate에는 DB 시간·write·저장 전망뿐 아니라 활성 tenant 범위 불일치와
   최근 24시간 외부 tenant 이벤트 발생도 포함한다.
4. 2026-08-26에 첫 28일 적격 기준선을 판정한다.
5. 대표 외부 tenant 2~3곳 확대는 첫 판정과 별도 대상 검증 후 진행한다.

파일럿 중 제품 분석이 사용자 오류를 만들거나 writer DB 시간·write의
10%, 90일 전망 여유 공간의 20% 게이트를 넘으면 즉시 플래그를 끈다.

## 계측 완성

- 선생님 제출 검토·채점 후속 퍼널
- 학부모 성적·공지 퍼널
- 학생 영상 퍼널
- StrictMode·visibility·수집 실패 자동 회귀 테스트
- 이전 28일 대비 trend
- 운영 대시보드 권한·반응형·빈 상태 E2E
- PostgreSQL 실행계획·index·실데이터 증가량 검증
- maintenance workflow 실행 실패 알림과 장기 실행 이력 검토

각 퍼널은 기존 업무 성공을 바꾸지 않고 `useTrackedTask` 또는 동등한
명시 경계에서만 추가한다.

## DB 구조 리뷰

28일마다 제품 분석과 별도로 다음을 판정한다.

- Multi-AZ: RTO·다중 tenant 영향 + 비용·복원 리허설
- read replica: 읽기 70% 이상 + 최적화 후 writer 병목 + lag allowlist
- dedicated data plane: 계약 사유 또는 2개 구간 30% noisy-tenant 증거

게이트가 없으면 현재 단일 writer를 유지한다.
