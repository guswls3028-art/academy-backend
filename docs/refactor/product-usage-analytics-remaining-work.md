# 제품 사용 분석 잔여 작업

**상태:** 구현·배포 이후의 조건부 rollout 및 계측 확장 백로그

**현재 계약:** [제품 사용 분석](../domain/product-usage-analytics.md)

**DB 판단 기준:** [DB 확장·테넌트 분리](../infrastructure/database-scaling-and-tenant-isolation.md)

## 지금 완료된 범위

- 익명 원본 이벤트와 일별 actor 집계 모델·migration
- tenant·membership·HMAC·idempotency·PII 방어가 있는 batch API
- platform-only overview와 single-tenant small-cell suppression
- 22개 기능·69개 인증 route registry
- 화면 방문·10초 참여·CTA 노출/클릭
- 선생님 출석, 학생 시험 제출, 클리닉 대표 task funnel
- 메모리 queue, 제한된 1회 재시도와 본 업무 fail-open
- rollup, dry-run-first purge와 tenant DB capacity report 명령
- 운영 배포와 전체 tenant 수집 OFF 확인

## 별도 승인 전 실행하지 않는 항목

- 테넌트 기능 플래그 활성화와 HMAC secret 변경
- 자동 rollup·purge 스케줄
- 메뉴·CTA 문구·위치·우선순위 변경
- Multi-AZ, read replica, `DATABASE_ROUTERS`
- tenant schema/database, data copy, dual-write, routing 전환

## 파일럿

1. 전용 secret 경계와 명시된 내부 파일럿 tenant를 승인한다.
2. 해당 tenant만 플래그를 켜고 7일간 품질·DB 증가량을 본다.
3. 수집 거부율, 중복, unknown feature, 역할 혼합, cross-tenant,
   제품 업무 오류와 DB overhead를 확인한다.
4. 대표 tenant 2~3곳을 별도 승인해 7일 더 관측한다.
5. 28일 적격 기준선 전에는 제품 위치를 바꾸지 않는다.

파일럿 중 제품 분석이 사용자 오류를 만들거나 writer DB 시간·write의
10%, 90일 전망 여유 공간의 20% 게이트를 넘으면 즉시 플래그를 끈다.

## 계측 완성

- 선생님 성적·시험·과제·제출·메시지 퍼널
- 학부모 성적·공지 퍼널
- 학생 영상·과제 퍼널
- StrictMode·visibility·수집 실패 자동 회귀 테스트
- 이전 28일 대비 trend
- 운영 대시보드 권한·반응형·빈 상태 E2E
- PostgreSQL 실행계획·index·실데이터 증가량 검증
- 자동 보존 스케줄의 정확한 운영 소유 경로

각 퍼널은 기존 업무 성공을 바꾸지 않고 `useTrackedTask` 또는 동등한
명시 경계에서만 추가한다.

## DB 구조 리뷰

28일마다 제품 분석과 별도로 다음을 판정한다.

- Multi-AZ: RTO·다중 tenant 영향 + 비용·복원 리허설
- read replica: 읽기 70% 이상 + 최적화 후 writer 병목 + lag allowlist
- dedicated data plane: 계약 사유 또는 2개 구간 30% noisy-tenant 증거

게이트가 없으면 현재 단일 writer를 유지한다.
