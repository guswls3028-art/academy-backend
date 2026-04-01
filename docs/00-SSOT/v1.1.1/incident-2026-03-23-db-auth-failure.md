# Incident Report: DB Authentication Failure (2026-03-23)

## Summary
RDS 비밀번호의 `!` 특수문자가 EC2 Docker 환경에서 bash history expansion으로 해석되어 전체 서비스 장애 발생.

## Timeline (KST)
| 시각 | 이벤트 |
|---|---|
| ~16:00 | API ASG rolling 교체 다수 발생 |
| ~18:17-18:59 | video-ops-ce ASG crash loop (5회) |
| ~19:00 | 커넥션 풀 완전 소진, 모든 요청 500 |
| 19:09 | RDS 비밀번호 리셋 시도 (Koreaseoul!97) |
| 19:10 | RDS 리부팅 (좀비 커넥션 정리) |
| 19:21 | `!` 문자 문제 발견, Koreaseoul97로 재변경 |
| 19:22 | SSM 3곳 업데이트 (api/env v19, workers/env v95, rds/master_password v3) |
| 19:23 | 3개 ASG instance refresh 시작 |
| 19:25 | 전체 ASG 교체 완료 |
| 19:28 | /health → healthy, database: connected 확인 |

## Root Cause
- RDS 비밀번호 `Koreaseoul!97`의 `!`가 bash 환경에서 history expansion으로 해석
- EC2 Docker 컨테이너 내부에서 psycopg2가 변형된 비밀번호로 접속 시도
- 로컬(외부 IP)에서는 정상 연결, VPC 내부(EC2→RDS private IP)에서만 실패
- 인증 실패 재시도 누적 → max_connections(400) 포화 → 전체 서비스 장애

## Resolution
1. RDS master password 변경: `Koreaseoul!97` → `Koreaseoul97` (! 제거)
2. SSM 3곳 동시 업데이트
3. RDS 리부팅 (좀비 커넥션 정리)
4. 3개 ASG instance refresh (api, messaging-worker, ai-worker)

## Verification
- 비밀번호 정합성: SSM 3곳 + 런타임 env 3곳 = sha256 해시 6개 전부 일치
- /healthz: 200 OK
- /health: database=connected
- 로그: password auth failed 0건 (API, MSG worker, AI worker 전부)
- pg_stat_activity: 44/400 (정상 범위)
- 워커 연결: SQS 2개 + Batch 2개 실사 확인

## Prevention Rules
1. **DB 비밀번호에 bash 특수문자 금지** — `!`, `$`, `` ` ``, `\` 등 포함 금지
2. **비밀번호 변경 시 SSM 3곳 동시 업데이트 필수** — /academy/api/env, /academy/workers/env, /academy/rds/master_password
3. **변경 후 모든 ASG instance refresh 실행** — api, messaging-worker, ai-worker
4. **변경 후 런타임 env까지 검증** — SSM만 맞추고 끝내지 않음

## Connection Budget (참고)
| 항목 | 현재값 |
|---|---|
| max_connections | 400 |
| API 1대 평시 | ~35 |
| Workers 2대 | ~8 |
| 평시 합계 | ~43 |
| Rolling refresh 피크 | ~80 |
| API 2대 + rolling 피크 | ~105 |
| 여유율 | 74% (400 기준) |

## Structural Notes
- GUNICORN: workers=4, worker_class=gevent, worker_connections=1000, timeout=120
- DB_CONN_MAX_AGE=60
- rds.force_ssl=1, password_encryption=scram-sha-256
