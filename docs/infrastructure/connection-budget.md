# DB Connection Budget (RDS Downsize Baseline)

## 현재 설정
| 항목 | 값 |
|---|---|
| RDS 인스턴스 | db.t4g.medium |
| max_connections | 400 (2026-07-30 production readback) |
| superuser_reserved_connections | 3 |
| 유효 application max | 397 |

## API 서버
| 항목 | 값 |
|---|---|
| GUNICORN_WORKERS | 4 |
| GUNICORN_WORKER_CLASS | gevent |
| GUNICORN_WORKER_CONNECTIONS | 1000 |
| DB_CONN_MAX_AGE | 0 |
| 평시 커넥션 (1대) | ~2-6 |
| 비용 baseline | API 1대 |
| 배포 교체 용량 | Instance Refresh가 기존 1 + 후보 1을 일시 운영; `desired` 불변 |
| ASG min/max | 1/3 |

## Workers
| 워커 | 인스턴스 | 평시 커넥션 |
|---|---|---|
| Messaging | 1 warm baseline | ~1-3 |
| AI | 1 warm baseline | ~1-3 |
| Tools | 1 warm baseline | ~1-3 |
| RDS admin | - | ~3 |
| Background | - | ~5 |

## 시나리오별 예산
| 시나리오 | 예상 커넥션 | 여유 |
|---|---|---|
| 평시 (API, Messaging, AI, Tools 각 1대) | ~12-31 | 92%+ |
| 평시 배포 (API/worker 후보가 일시 중첩) | ~20-45 | 88%+ |
| 부하 중 배포 (`desired + 1`과 worker burst 중첩) | ~30-70 | 82%+ |
| 장애 시 재시도 폭주 | 400 포화 가능 | 0% |

## 규칙
- 평시 사용률 30% 이하 유지
- Rolling refresh는 `MinHealthyPercentage=100`, `MaxHealthyPercentage=200`을
  유지하고 `min`/`desired`를 직접 늘리지 않는다. `desired == max`이면 max
  ceiling만 한 슬롯 잠시 확장하고 원래 값으로 복구한다.
- 비밀번호 변경 시 구 인스턴스 빠른 종료로 좀비 커넥션 방지
- Production API connections close at request completion
  (`DB_CONN_MAX_AGE=0`). The direct-RDS, gevent runtime must not enable
  persistent Django connections without a fresh concurrency soak, RDS
  connection-budget review, and an isolated pre-production proof.
- AI domain callbacks release stale worker connections only outside an active
  database transaction. A callback reused by an API reconciliation path must
  never close the request transaction's connection mid-operation.
- `academy-rds-DatabaseConnectionsHigh` remains calibrated at 320 connections
  (80% of the measured db.t4g.medium connection budget).

## 2026-07-30 saturation evidence

- Repeated production E2E traffic increased `DatabaseConnections` from 143 to
  391 over 55 minutes.
- The single `academy-api` container owned 390 established PostgreSQL sockets
  while RDS rejected new application connections with
  `remaining connection slots are reserved`.
- Applying `DB_CONN_MAX_AGE=0` through `/academy/api/env` version 74 and the
  rollback-protected API env refresh reduced the API container's established
  PostgreSQL sockets from 390 to 0. Both `/healthz` and database-backed
  `/health` returned HTTP 200 after replacement.
- See
  [incident-2026-07-30-db-connection-exhaustion.md](../reports/incidents/incident-2026-07-30-db-connection-exhaustion.md)
  for the impact, recovery, and recurrence-prevention evidence.
