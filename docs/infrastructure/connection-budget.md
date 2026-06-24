# DB Connection Budget (RDS Downsize Baseline)

## 현재 설정
| 항목 | 값 |
|---|---|
| RDS 인스턴스 | db.t4g.medium |
| max_connections | ~405 expected after downsize (current db.t4g.large runtime observed 838 before maintenance apply) |
| rds_superuser_reserved | 2 (기본값) |
| 유효 max | ~403 expected |

## API 서버
| 항목 | 값 |
|---|---|
| GUNICORN_WORKERS | 4 |
| GUNICORN_WORKER_CLASS | gevent |
| GUNICORN_WORKER_CONNECTIONS | 1000 |
| DB_CONN_MAX_AGE | 5 |
| 평시 커넥션 (1대) | ~2-6 |
| 비용 baseline | API 1대 |
| 배포 headroom | API 2대 이상(일시), refresh 후 1대로 복귀 |
| ASG min/max | 1/3 |

## Workers
| 워커 | 인스턴스 | 평시 커넥션 |
|---|---|---|
| Messaging | 0 baseline, 작업 시 scale-out | 0 |
| AI | 0 | 0 |
| RDS admin | - | ~3 |
| Background | - | ~5 |

## 시나리오별 예산
| 시나리오 | 예상 커넥션 | 여유 |
|---|---|---|
| 평시 (API 1대, workers idle) | ~10-20 | 95%+ |
| 배포 headroom (API 2대 일시) | ~15-30 | 92%+ |
| Rolling refresh (API 3대 순간) | ~25-45 | 88%+ |
| 장애 시 재시도 폭주 | 400 포화 가능 | 0% |

## 규칙
- 평시 사용률 30% 이하 유지
- Rolling refresh 중 동시 인스턴스 수 주의 (MinHealthyPercentage 설정)
- 비밀번호 변경 시 구 인스턴스 빠른 종료로 좀비 커넥션 방지
- Django persistent DB connection is intentionally short in production
  (`DB_CONN_MAX_AGE=5`). Do not raise it without a fresh RDS connection budget
  review.
- `academy-rds-DatabaseConnectionsHigh` remains calibrated at 320 connections
  (~80% of the expected db.t4g.medium connection budget).
