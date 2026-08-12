# DB 확장·테넌트 분리 판단 기준

**상태:** 현재 단일 PostgreSQL writer와 tenant-column 격리 유지

**적용 범위:** Multi-AZ, read replica, 선택적 tenant data plane

**관련 제품 신호:** [제품 사용 분석](../domain/product-usage-analytics.md)

## 1. 현재 구조와 기본 결정

API와 일반 워커는 하나의 Django `default` PostgreSQL 연결을 사용한다.
현재 애플리케이션에는 `DATABASE_ROUTERS`, read replica alias,
테넌트별 schema/database와 `TenantDataPlane` 메타데이터가 없다.
테넌트 격리는 요청에서 테넌트를 확정하고 tenant foreign key로 닫는다.

현재 결론은 **멀티 DB를 도입하지 않는다**다. 사용 화면 수나 이벤트
수만으로 데이터베이스를 나누지 않는다. 비용과 안정성은 다음 세 문제를
분리해 판단한다.

| 선택지 | 해결하는 문제 | 해결하지 않는 문제 |
|---|---|---|
| Multi-AZ | DB 인스턴스·AZ 장애 시 가용성 | 읽기 용량, noisy tenant 격리 |
| read replica | lag 허용 조회의 읽기 부하 | 쓰기, 강한 일관성, tenant 격리 |
| dedicated tenant data plane | 계약·복구·성능상 선택 tenant 격리 | 공용 control plane 의존 |

## 2. 증거 수집 경계

DB 구조 판단은 최소 28일의 동일 기간 증거를 사용한다.

- RDS CPU, DB load/wait, connections, memory, burst credit, storage와
  90일 증가 전망
- API·worker SLO, timeout, connection exhaustion과 사용자 영향
- Academy 태그가 확인된 현재 비용과 변경안 견적
- snapshot/PITR 복원, migration, health, 핵심 사용자 흐름 리허설
- 테넌트별 추정 DB 시간·query·write 비중
- 제품 분석이 추가한 writer 시간·write·storage

`TenantDatabaseUsageMiddleware`는 명시적으로 활성화된 경우에만 요청
단위 `default` DB query 수, write 수, DB 시간, 전체 요청 시간, 상태
등급과 route family를 구조화 로그로 남긴다. SQL, parameter와 사용자
입력은 기록하지 않는다. 기본 표본율은 10%이며 느린 요청과 5xx는
전수 기록한다. 테넌트가 확정되지 않으면 기록하지 않는다.

`report_tenant_db_capacity --input <jsonl>`은 외부로 export한 구조화
로그를 읽어 테넌트별 추정 비중을 JSON으로 계산하며 DB에 쓰지 않는다.
환경변수 기본값은 OFF다. 내부 파일럿은
`.github/workflows/product-usage-pilot-controls.yml`의 production 승인과
exact confirmation으로만 5% 또는 10% 표본을 설정하고, guarded backend
release로 runtime을 교체한다. daily maintenance는 CloudWatch Logs
Insights에서 `sample_weight`를 적용해 제품 분석 route의 DB 시간·write
비중을 계산한다. telemetry가 없으면 수치를 0으로 가정하지 않고
`db_usage_share_unavailable` 경고를 남긴다. worker context는 아직
연결되지 않았다.

## 3. Multi-AZ 게이트

다음 중요도 조건 중 하나가 성립하면 Multi-AZ 검토를 연다.

- 승인된 DB RTO가 10분 이하
- 독립 운영 테넌트 2곳 이상이 한 DB 장애로 동시에 중단
- 출결·시험·결제 같은 시간 민감 흐름의 예상 장애 비용이 월 증분 비용
  이상
- 유사한 DB 또는 AZ 장애가 실제 발생

실행 승인은 아래를 모두 요구한다.

1. Academy 태그 기준 30일 비용과 변경 시점 견적이 있다.
2. 월 증분액에 20% 여유를 더한 forecast가 승인 예산의 85% 이하다.
3. 최신 snapshot/PITR을 격리 복원하고 migration, `/healthz`,
   `/health`, 로그인과 적용 핵심 흐름을 통과한다.
4. failover 시 API·worker 연결 재수립과 재시도 계약을 검증한다.
5. maintenance window, rollback 기준, 담당자와 다음 리뷰일을 기록한다.

조건이 부족하면 Single-AZ를 유지하고 28일 뒤 다시 본다. Multi-AZ
문서 준비는 실제 운영 변경 승인이 아니다.

## 4. read replica 게이트

다음을 모두 만족할 때만 별도 epic을 연다.

- 28일 DB 시간의 읽기 비중이 70% 이상
- 느린 query, N+1, 누락 index와 불필요한 polling을 먼저 개선
- 개선 후에도 writer 압박이 사용자 SLO를 위협
- lag를 허용할 endpoint allowlist가 존재
- replica 비용이 writer 상향보다 유리

인증, 권한, 결제, 출석, 제출과 상태 머신은 writer denylist다. 전역
round-robin router를 두지 않고 allowlist 조회만 명시적으로 replica를
선택한다. 오류 시 writer fallback도 해당 endpoint의 일관성 계약이
허용할 때만 사용한다.

## 5. 선택적 tenant data plane 게이트

다음 사유는 즉시 분리 검토를 연다.

- 계약·법률·데이터 레지던시상 물리 분리
- 테넌트별 backup, RPO/RTO 또는 독립 중지·복구 요구

성능·비용 사유는 다음 중 하나를 요구한다.

- 한 테넌트가 DB 시간 또는 write의 30% 이상을 2개 연속 28일 구간에서
  차지하고 다른 테넌트의 SLO·연결·writer 안정성에 반복 영향
- 한 테넌트가 storage의 25% 이상이며 90일 안에 공용 여유를 소진
- 전용 운영 비용을 계약이나 수익으로 감당

모든 테넌트가 비슷하게 증가하면 tenant split 대신 공용 DB 상향,
query·index, partitioning과 retention을 먼저 검토한다. 제품 분석
자체가 90일 전망상 현재 DB 여유 공간의 20%를 넘거나 writer DB 시간
또는 write를 10% 이상 추가하면 분석 rollout을 중지하거나 분석
저장소를 분리한다. 그 신호만으로 제품 도메인을 shard하지 않는다.

## 6. 장기 구조와 fail-closed 규칙

게이트가 통과한 경우의 목표는 다음과 같다.

```text
default control plane
  Tenant / TenantDomain / auth / membership / feature flags
  TenantDataPlane(alias, kind, schema_version, routing_version, status)

shared data plane
  대부분 테넌트의 이동 가능 도메인 데이터

dedicated data plane
  승인된 테넌트의 동일 schema
```

- credentials는 SSM·secret store에만 두고 메타데이터에는 alias와 상태만
  둔다.
- 요청은 control plane에서 tenant를 확정한 뒤 data plane을 결정한다.
- alias가 없거나 비활성·schema/routing version이 맞지 않으면
  `default`로 fallback하지 않고 실패한다.
- cross-DB foreign key, join과 원자 transaction을 금지한다.
- 비동기 작업은 tenant ID를 전달하고 시작 시 최신 routing version을
  다시 해석한다.
- 모든 data plane의 migration/schema inventory가 맞아야 배포한다.

## 7. 테넌트 이동 안전 게이트

1. 대상 tenant, 도메인, 행 수, 첨부, queue와 영향 사용자를 열거한다.
2. dedicated DB의 암호, backup, 삭제 보호, 관측과 복구를 준비한다.
3. preprod와 dedicated DB에 expand migration을 적용한다.
4. 초기 copy 뒤 count, checksum과 도메인 invariant를 비교한다.
5. 승인된 delta 수렴 방식과 짧은 tenant write pause를 준비한다.
6. queue drain과 최종 delta 검증 뒤 routing version을 원자 전환한다.
7. 로그인, 권한과 적용 도메인 핵심 사용자 흐름을 확인한다.
8. 구 위치는 최소 7일 read-only로 보존한다.

일반 dual-write는 사용하지 않는다. 새 data plane에 write가 발생한 뒤
단순 route flip으로 롤백하지 않는다. reverse delta와 검증 없는 데이터
삭제·정리는 별도 승인 전에는 금지한다.

## 8. 현재 남은 증거

- 운영 sampled tenant DB telemetry의 28일 overhead 실측
- 일반 worker context 계측
- Academy 태그 비용선과 28일 tenant 비중 보고서
- snapshot/PITR 격리 복원 리허설
- Multi-AZ 비용·RTO 승인 기록

어느 게이트도 아직 충족한 것으로 승인되지 않았으므로 현재 단일
writer와 tenant-column 격리를 유지한다.
