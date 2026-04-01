# DB Connection Budget (V1.1.1)

## 현재 설정
| 항목 | 값 |
|---|---|
| RDS 인스턴스 | db.t4g.medium |
| max_connections | 400 |
| rds_superuser_reserved | 2 (기본값) |
| 유효 max | 398 |

## API 서버
| 항목 | 값 |
|---|---|
| GUNICORN_WORKERS | 4 |
| GUNICORN_WORKER_CLASS | gevent |
| GUNICORN_WORKER_CONNECTIONS | 1000 |
| DB_CONN_MAX_AGE | 60 |
| 평시 커넥션 (1대) | ~35 |
| ASG min/max | 1/2 |

## Workers
| 워커 | 인스턴스 | 평시 커넥션 |
|---|---|---|
| Messaging | 1 | ~4 |
| AI | 1 | ~4 |
| RDS admin | - | ~3 |
| Background | - | ~5 |

## 시나리오별 예산
| 시나리오 | 예상 커넥션 | 여유 |
|---|---|---|
| 평시 (API 1대) | ~43 | 89% |
| API 2대 | ~78 | 80% |
| Rolling refresh (API 3대 순간) | ~113 | 72% |
| 장애 시 재시도 폭주 | 400 포화 가능 | 0% |

## 규칙
- 평시 사용률 30% 이하 유지
- Rolling refresh 중 동시 인스턴스 수 주의 (MinHealthyPercentage 설정)
- 비밀번호 변경 시 구 인스턴스 빠른 종료로 좀비 커넥션 방지
