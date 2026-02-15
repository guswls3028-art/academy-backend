# Hexagonal 전환 + 10K 목표 실행 계획서 (SSOT)

**기준**: 현재 코드·설정·[500명 스타트 가이드](cursor_docs/AWS_500_START_DEPLOY_GUIDE.md)만 반영. 추측 금지.  
**목표**: Big Bang Hexagonal 전환 실행 + 10K 1차 목표 + 첫 달 500명 런칭 운영 확정성.

**500 가이드와의 정합성**: 배포 절차·인스턴스 스펙(t4g.small/micro/medium)·RDS·Video 100GB·CloudWatch 7~14일·오픈 전 체크는 500 가이드와 동일. **차이**: DB 연결 수는 본 문서 §3.1에서 **steady state 7**로 계산하고, 500 가이드는 "**20~40개**까지 갈 수 있음"으로 상한 경고. 알람은 `max_connections × 0.8` 기준으로 두 문서 모두 모니터링 권장. AI 큐는 스크립트 기본 300초·Academy Worker 사용 시 3600초 연장(§3 병목 3).

---

### Big Bang 진입 조건 (사용자 0·배포 직전 기준)

**“지금 문서 상태 그대로 즉시” Big Bang 진입은 금지.** 문서가 스스로 **런타임 크래시 가능(Pre-Phase)** / **필수 로직 미구현(60분 상한)** / **핵심 정책 미확정(Lease)** 를 포함하고 있으면 설계와 불일치한다.

**아래 5개가 완료되면**, 사용자 0 기준으로 DB 포함 파괴적 Big Bang GO 가능.

| # | 조건 | 완료 기준 |
|---|------|-----------|
| 1 | **Tier Enforcer Pre-Phase** | TypeError 0 보장. payload 인자 제거 또는 enforcer에 optional 추가 + 테스트/부팅 1건. |
| 2 | **AI 60분 상한 구현** | 구현 위치 1곳 고정(권장: `academy/framework/workers/ai_sqs_worker.py`) 후 60분 초과 시 fail_ai_job + SQS delete + extender stop. |
| 3 | **Lease 전략 1개 확정** | 4개 중 1개 선택 후 **코드·문서에 고정**. (사용자 0·빠른 Big Bang에는 (1) Lease 고정 또는 (4) Lease 미사용이 단순.) |
| 4 | **Legacy 경로 방향 확정** | `USE_LEGACY_AI_WORKER` 최종 제거 시점 또는 “배포 전까지 유지” 중 하나로 결정. §7 완료 기준 참고. |
| 5 | **Video visibility (선택·권장)** | 10K 목표면 주기 extender가 최종적으로 필수. “배포 직전/초기 사용자 0”이면 이번 Big Bang에 같이 넣으면 10K 때 큰 수술 감소. |

**현재 반영 상태**  
- (1) Pre-Phase: 호출부에서 `payload` 인자 제거 반영. 부팅/테스트 1건은 운영 측 확인.  
- (2) 60분 상한: `ai_sqs_worker.py`에 INFERENCE_MAX_SECONDS(3600), 스레드 timeout 후 fail + delete + extender stop 반영.  
- (3) Lease: **채택 (1) 고정 3540초**. `ai_sqs_worker.py` LEASE_SECONDS 기본값 3540, 문서 §8.3에 채택 명시.

**마지막 체크 2개 (결정 시 설계도·인프라 가이드 완전 정렬)**  
아래 두 가지만 확정하면 500 가이드와 10K SSOT가 일치한다.  
1. **USE_LEGACY_AI_WORKER**: 이번 Big Bang에서 **완전 제거**할지, 아니면 **배포 전까지 유지**할지 결정. (§7 완료 기준: 제거 시 `grep -r "USE_LEGACY_AI_WORKER"` → 0건.)  
2. **Video visibility extender**: **지금 Big Bang에 같이 넣을지** 결정. 넣으면 10K 때 수술 감소; 안 넣으면 500 가이드대로 “단일 extend 1회” 유지 후 10K 단계에서 주기 extender 도입.

**최종 판정**  
- **사용자 0** · **배포 직전** · **SSOT 정렬 완료** · **선행 3조건(Tier Enforcer TypeError 제거, AI 60분 상한 반영, Lease 3540 고정 확정) 충족** 시 → **DB Drop 포함 Big Bang 진입 가능**.  
- 단, **코드가 문서대로 정렬되어 있는지**는 설계가 아닌 실행 검증 문제다 (domain/application django import 0건, src import 제거, legacy 제거, Worker 상태 전이 직접 호출 0건 등).

**Big Bang 진입 방식 (7단계 통과 후 배포)**  
무식하게 한 번에 올리지 말고, 아래 **7개 통과 후** 배포한다.

| # | 단계 | 통과 기준 |
|---|------|-----------|
| 1 | DB Drop | (필요 시) 스키마 초기화 후 진행 |
| 2 | migrate | `python manage.py migrate` 성공 |
| 3 | AI 1건 | AI Job 1건 생성 → SQS → Worker 처리 → DONE 확인 |
| 4 | Video 1건 | Video Job 1건 → Worker 처리 → 완료 확인 |
| 5 | Messaging 1건 | Messaging 1건 발송/처리 확인 |
| 6 | Worker kill 테스트 1회 | 처리 중 Worker 강제 종료 → 재기동 후 재처리 또는 멱등 스킵, 중복 완료 0건 |
| 7 | DLQ 테스트 1회 | (선택) 의도적 실패 1건 → DLQ 도달 확인 → §8.4 절차 1회 점검 |

**이 7개 통과하면 배포.**

**Big Bang 최종 게이트 (10개 전부 YES면 GO)**  
"일치"는 감각이 아니라 **기계적으로 검증된 일치**다. 아래 10개 전부 통과하면 바로 Big Bang 시작 가능. 검증 결과는 `docs/SSOT_0215/CODE_ALIGNMENT_REPORT.md`(정렬 보고서)에 기록.

| # | 항목 | 검증 방법 | 통과 기준 |
|---|------|-----------|-----------|
| 1 | **Tier Enforcer** | 호출부 확인, Worker 1건 처리 | `enforce_tier_limits` 호출에 payload 인자 없음. Worker 1건 처리 시 TypeError 0. |
| 2 | **AI 60분 상한** | 코드·로그 | `INFERENCE_MAX_SECONDS = 3600`. 초과 시 fail_ai_job + SQS delete + extender.stop(). 로그로 확인 가능. |
| 3 | **Lease 정책** | 코드·env | `LEASE_SECONDS = 3540`, visibility = 3600. lease ≤ visibility − 60 수식 유지. |
| 4 | **Legacy 완전 제거** | `grep -r "USE_LEGACY_AI_WORKER"` | → 0건 |
| 5 | **Worker 상태 전이 직접 호출 없음** | Worker 코드 grep | `mark_processing` / `complete_job` / `fail_job` 직접 호출 0건. use case 경유만 존재. |
| 6 | **domain/application에 django import 없음** | `grep -r "import django" academy/domain academy/application` | → 0건 |
| 7 | **adapters 외부 ORM 접근 없음** | `.objects.` 접근 위치 | adapters 외부에서 0건 |
| 8 | **DB unique 보장** | 모델 정의 | AIJobModel.job_id unique. AIResultModel OneToOne. |
| 9 | **Visibility 정책 통일** | 큐 속성·코드 | AI queue visibility 3600. 기존 300초 큐 남아 있지 않음. |
| 10 | **7단계 런타임 테스트 통과** | 실행 | AI 1건, Video 1건, Messaging 1건, Worker kill 1회, DLQ 1회 통과. |

---

## 1. 전체 실행 로드맵

| Phase | 목표 | 산출물 | PR 단위 |
|-------|------|--------|---------|
| **0** | academy/ 패키지 구조 생성 | `academy/domain`, `academy/application`, `academy/adapters`, `academy/framework` 디렉터리 및 `__init__.py` | PR-0 |
| **1** | AI 도메인·상태 전이 이전 | AIJob entity in domain, 상태 전이·멱등 정책 in domain/application | PR-1 |
| **2** | AI Use Case 작성 | SQS 메시지 → prepare → inference → complete/fail 흐름 in application | PR-2 |
| **3** | DB Adapter 격리 | Django ORM 접근을 `academy/adapters/db/django`로만 제한, repository 구현체 | PR-3 |
| **4** | SQS Adapter + Visibility | AI Worker에 change_message_visibility 연장 로직 구조적 도입, visibility extender | PR-4 |
| **5** | Framework 정리 | Worker 엔트리를 `academy/framework/workers`로 교체, Django API 얇게 유지 | PR-5 |
| **6** | Video/Messaging 동일 패턴 | Video·Messaging용 domain/application/adapters/framework 동일 구조 적용 | PR-6 |
| **7** | src 폴더 제거 | src → academy/* 흡수, 역방향 import 0건 | PR-7 |

**30K 로드맵 (한 줄)**  
현재 Lite/Basic 큐 분리. 30K 단계에서 **Premium 전용 큐·Batch 큐·High-priority 큐** 분리 검토. 장기 SaaS 확장 시 AI 큐 전략 1줄 이상 유지.

**의존성 방향 (강제)**  
`Domain ← Application ← Adapters ← Framework`. 역방향 import 금지.

**현재 코드 근거**

- `academy/` 패키지 이미 존재: `academy/domain/ai/entities.py`, `academy/application/use_cases/ai/process_ai_job_from_sqs.py`, `academy/adapters/db/django/repositories_ai.py`, `academy/adapters/queue/sqs/visibility_extender.py`, `academy/framework/workers/ai_sqs_worker.py`.
- `src/` 역방향 import: `src/infrastructure/video/processor.py` → `apps.worker.*`, `apps.core.r2_paths`; `src/infrastructure/video/sqs_adapter.py` → `apps.support.video.services.sqs_queue`; `src/infrastructure/ai/sqs_adapter.py` → `apps.support.ai.services.sqs_queue`; `src/infrastructure/db/ai_repository.py` → `apps.domains.ai.models`; `src/infrastructure/db/video_repository.py` → `apps.support.video.models`; `src/application/services/excel_parsing_service.py` → `apps.domains.enrollment.services` (255행).
- AI Worker 이중 경로: `apps/worker/ai_worker/sqs_main_cpu.py` 406–410행에서 `USE_LEGACY_AI_WORKER != "1"`이면 `academy.framework.workers.ai_sqs_worker.run_ai_sqs_worker()` 호출, 실패 시 legacy `main()` fallback.

---

## 2. SLO/SLI 정의

**수치·조건만 명시. 추상 표현 배제.**

| 구분 | 지표 | 목표값 | 측정 방법 | 근거 |
|------|------|--------|-----------|------|
| **가용성** | API 가용률 | 99.5% (월간) | ALB 5xx 비율 또는 `/health` 실패 비율 | AWS 500 가이드 §11: `GET /health` → 200 |
| **지연** | API P95 응답 시간 | **측정 범위 정의**: (1) **Read endpoints** P95 &lt; 2초 (2) **Write endpoints** P95 &lt; 3초 (3) **Health** `/health` &lt; 300ms. DB 포함, inference/비동기 job 제외. | CloudWatch/APM 또는 ALB target response time. 엔드포인트별 구분 측정 권장. | 미정의 시 SLO 추상 수준. |
| **지연** | AI Job 처리 완료 | 큐 대기 + 처리 &lt; 1시간 (Basic/Lite). **상한**: inference 최대 60분 초과 시 강제 fail (아래 §8.3). **SLO는 단일 실행 기준**: Worker kill 등으로 재처리 시 “첫 실행 + 재실행” 합산은 1시간 초과 가능. | 로그 `SQS_JOB_COMPLETED \| total_duration` | `apps/worker/ai_worker/sqs_main_cpu.py` 306–314행 |
| **지연** | Video 인코딩 완료 | 3시간 영상 기준 &lt; 4시간 | Worker 로그 + `VIDEO_SQS_VISIBILITY_EXTEND=10800` | `scripts/create_sqs_resources.py` 64행, `apps/worker/video_worker/sqs_main.py` 48행 |
| **정확성** | AI Job 멱등 | 동일 job_id 재처리 시 상태 덮어쓰기 없음 (DONE 유지). **DB 레벨**: job당 result 1행만 보장. | `prepare_ai_job` → None 반환 시 메시지만 삭제. `mark_done` 시 get_or_create로 result 1행. | 아래 §8.3.1 DB unique 제약. |
| **정확성** | DLQ 비율 | AI/Video/Messaging DLQ 메시지 &lt; 1% (대비 송신) | SQS 콘솔 `ApproximateNumberOfMessagesVisible` (DLQ) / 메인 큐 송신 수 | AWS 500 가이드 §11.5 |
| **용량** | DB 연결 수 | &lt; max_connections (PostgreSQL). **Worst case**: steady_state + burst_margin + admin_margin (health/migration/admin·Celery 등). 계산표(§3.1)는 steady state 기준; burst·예약 연결 고려 시 안전계수 적용. | `SHOW max_connections;` 및 활성 연결 모니터링 | AWS 500 가이드 §오픈 전 실전 체크 2번 |
| **용량** | Video Worker 디스크 | 사용량 &lt; 80% (100GB 볼륨) | EC2 `df -h` 또는 CloudWatch 디스크 메트릭 | AWS 500 가이드 §2, §8.1 |

**500명 런칭 시 트리거**

- SLO 위반 시: API 5xx율 1% 초과 5분 지속 → 알람.
- AI Job: DLQ 적체 10건 이상 → 알람.
- DB: 활성 연결 수 &gt; `max_connections * 0.8` → 알람.

---

## 3. 10K 병목 7개 대응 전략

| # | 병목 | 근거 (파일/함수/설정) | 대응 전략 | 검증 방법 |
|---|------|----------------------|-----------|-----------|
| **1** | **DB 연결 수** | `apps/api/config/settings/base.py` 186행 `CONN_MAX_AGE=60`, Worker 동일 `worker.py` 85행. Gunicorn worker 수 + 워커 프로세스 = 동시 연결 증가. | 10K 전: `SHOW max_connections;` 확인. 연결 수 모니터링. 필요 시 PgBouncer 도입 또는 `CONN_MAX_AGE=0`(요청 단위 연결). | RDS CloudWatch `DatabaseConnections`, 알람 임계값 = max_connections × 0.8. |
| **2** | **Row lock (AI Job)** | `academy/adapters/db/django/repositories_ai.py` 82행, 98행, 128행 `select_for_update()`. 장시간 lock 시 대기 증가. | lease 기간 내 완료 보장. `prepare_ai_job` 실패(이미 RUNNING/DONE) 시 메시지만 삭제하여 재시도 축소. | 부하 테스트: 동일 job_id 동시 수신 시 1건만 RUNNING, 나머지 prepare → None. |
| **3** | **SQS Visibility** | Legacy AI: `apps/worker/ai_worker/sqs_main_cpu.py` 46행 `SQS_VISIBILITY_TIMEOUT=300`. 큐 기본값 `scripts/create_ai_sqs_resources.py` 29·35행 300초. 장작업 시 메시지 재노출 → 중복 처리. | Academy 경로 사용 시 `academy/framework/workers/ai_sqs_worker.py` 35행 `AI_SQS_VISIBILITY_TIMEOUT=3600`, 36행 `AI_VISIBILITY_EXTEND_INTERVAL=60`, `SQSVisibilityExtender` 주기 연장. Legacy 제거 또는 Visibility 3600으로 상향. | 로그: `Visibility extended \| tier=...` 주기 출력. 재노출 경고 로그 없음. |
| **4** | **Idempotency** | AI: `prepare_ai_job` → `repo.mark_running` 실패(이미 DONE/FAILED) 시 None 반환, 메시지 삭제 (`ai_sqs_worker.py` 185–189행). Video: `src/application/video/handler.py` 60행 `mark_processing` 실패 시 재시도. | Domain/Application에서 “이미 최종 상태면 쓰기 스킵” 규칙 유지. DB adapter는 `select_for_update` + 상태 검사. | 동일 job_id/video_id 2회 연속 메시지 시 1회만 RUNNING/DONE 반영, 2회째는 스킵 또는 메시지 삭제. |
| **5** | **디스크 (Video)** | Video Worker: 루트 8GB만 사용 시 트랜스코딩 중 디스크 Full. 가이드: 100GB EBS `/mnt/transcode` 필수. | `apps/worker/video_worker/sqs_main.py` 269행 등 `-v /mnt/transcode:/tmp`. 배포 체크: `df -h`로 `/mnt/transcode` 약 100G 확인. | 배포 전 체크리스트 항목. CloudWatch 디스크 사용률 알람 80%. |
| **6** | **관측** | 현재: 로그 기반. APM/메트릭 상세 설정 미명시. | CloudWatch Log groups 보관 7~14일 (가이드 §11.1). `/health` 응답에 DB 연결 상태 포함. Worker 로그에 request_id, job_id, duration 고정 포맷. | 로그 검색으로 `SQS_JOB_COMPLETED`, `SQS_JOB_FAILED` 집계 가능. 알람: 5xx, DLQ, DB 연결. |
| **7** | **롤백** | 코드/설정 변경 시 롤백 절차 미문서화. | PR 단위 배포. 이전 이미지 태그 보관. DB 마이그레이션은 역마이그레이션 스크립트 준비(Phase 3에서 스키마 변경 시). | 배포 시 “이전 버전 컨테이너로 재기동” 절차 명시 및 1회 수행. |

### AI Queue Visibility 정책 확정 (운영 고정값)

- AI SQS VisibilityTimeout = **3600초**
- lease_expires_at = **3540초** (visibility − safety_margin 60초)
- **Academy Worker 경로만 사용**
- **USE_LEGACY_AI_WORKER** 환경 변수 완전 제거

위 정책은 Big Bang 시점부터 고정한다.  
기존 300초 Visibility 큐는 콘솔 또는 IaC로 **3600초로 상향 조정**한다.

---

### 3.1 DB 연결 수 — 실제 숫자 대입

**근거**: `docker/api/Dockerfile` 28행 `GUNICORN_WORKERS:-4`, [500명 스타트 가이드](cursor_docs/AWS_500_START_DEPLOY_GUIDE.md) API 상시 1대·워커 각 1대, RDS db.t4g.micro 기본 파라미터 그룹(실제값은 `SHOW max_connections;`로 확인).

| 항목 | 값 | 근거 |
|------|-----|------|
| API 인스턴스 수 | 1 | AWS 500 가이드 §6: API t4g.small 1대 상시 |
| Gunicorn worker 수 | 4 | `docker/api/Dockerfile` 28행 `--workers ${GUNICORN_WORKERS:-4}` |
| AI Worker 프로세스 수 | 1 | `docker/ai-worker-cpu/Dockerfile` CMD 단일 프로세스 |
| Video Worker 프로세스 수 | 1 | `docker/video-worker/Dockerfile` CMD 단일 프로세스 |
| Messaging Worker 프로세스 수 | 1 | `docker/messaging-worker/Dockerfile` CMD 단일 프로세스 |
| **예상 총 DB 연결 수** | **7** | API 4 (worker당 1) + AI 1 + Video 1 + Messaging 1. CONN_MAX_AGE=60이면 프로세스당 1연결 유지. |
| RDS max_connections | 확인 필요 | 실제 값은 운영 인스턴스에서 `SHOW max_connections;`로 확인 (엔진 버전·파라미터 그룹에 따라 다름). |
| 안전 여유 판단 | 계산식 사용 | (max_connections − 예상 steady_state 연결 수) / max_connections 로 계산. 운영에서 0.8 초과 시 알람. |

**10K 시 참고**: AI Worker 3대로 확장 시 예상 연결 수 = 4 + 3 + 1 + 1 = 9. 확인된 max_connections 대비 여유 있을 때까지 모니터링. API 워커 증설 시 연결 수 선형 증가 → PgBouncer 또는 max_connections 상향 검토.

**확장 시 공식 재계산**: 10K에서 API 인스턴스 2대, Gunicorn 6 workers, AI Worker 3대, Video/Messaging 각 1대면 **예상 총 연결 수 = 2×6 + 3 + 1 + 1 = 17**. **스케일 변경 시마다 위 표 공식으로 재계산 후, 운영에서 `SHOW max_connections;`로 확인된 값과 비교 필수**.

---

### 3.2 DB Query Plan 및 인덱스 점검 (10K 대비)

10K에서 쿼리 병목을 막기 위해 아래 항목을 병목 7개 대응 후 점검한다.

| 항목 | 내용 | 근거 |
|------|------|------|
| AIJobModel 인덱스 | `status`, `job_id`, `tier`, `next_run_at` 각각 db_index. 복합: `(status, next_run_at)`, `(lease_expires_at)`, `(source_domain, source_id)`, `(tier, status, next_run_at)`. | `apps/domains/ai/models.py` 84–88행 |
| 자주 조회되는 필드 | Job 상태 조회: `job_id` (PK/unique). 대기 Job 폴링: `status`, `next_run_at` 복합 인덱스 사용 여부 확인. | `academy/adapters/db/django/repositories_ai.py` filter(job_id=...), select_for_update |
| EXPLAIN ANALYZE | AI Job `get_for_update`, `mark_running`, `mark_done` 경로에서 `EXPLAIN (ANALYZE, BUFFERS)` 실행 후 Seq Scan/인덱스 사용 여부 확인. | Repository 쿼리 경로 |
| Slow query log | RDS 파라미터 그룹에서 `log_min_duration_statement` (예: 1000ms) 활성화 여부. 10K 전 활성화 권장. | 운영 관측 |

**완료 조건**: 10K 부하 전 1회 — AI Job 조회/갱신 쿼리에 대해 EXPLAIN ANALYZE 실행, 인덱스 사용 확인. Slow query 로그 설정 여부 문서화.

---

### 3.3 Worker 수평 확장 전략 (10K 대비)

500명: Worker 각 1대. 10K에서는 AI Worker 동시 실행 개수 증가를 전제로 아래를 반드시 검증한다.

| 항목 | 내용 | 완료 조건 |
|------|------|-----------|
| AI Worker 3개 확장 가정 | 10K 시 AI Worker 인스턴스(또는 프로세스) 3개로 확장. 동일 Lite/Basic 큐에서 여러 워커가 동시에 메시지 수신. | 배포/스케일 가이드에 “AI Worker 3대” 시나리오 명시 |
| DB row lock 충돌 시뮬레이션 | 동일 시점에 서로 다른 job_id로 3개 워커가 각각 `mark_running`(select_for_update) 수행. lock 대기 시간·데드락 0건 확인. | 부하 테스트: 동시 AI job 20~30건, 3 워커에서 처리. DB lock 대기 로그 없음(또는 허용 수준). |
| SQS max in-flight / 재노출 | 워커 3개 × 메시지 1건씩 = in-flight 3. Visibility 연장 스레드는 **메시지당 1개** (extender 인스턴스당 1 receipt_handle). 워커 수 증가해도 “visibility 만료 → 메시지 재노출” 0건 유지. | 시나리오: 3 워커 기동, 장작업(>300초) 3건 동시 처리. 로그에 “Visibility extended” 출력, “SQS_VISIBILITY_TIMEOUT_EXCEEDED” 또는 재노출 0건. |
| Visibility extender 스레드 | 워커 3개면 extender 스레드 3개(각 워커 1개). 스레드당 1 receipt_handle만 유지. 추가 부하는 없음. | 문서화: extender는 “현재 처리 중인 메시지 1건당 1 스레드”로 한정. |

**검증 방법 요약**: 10K 준비 부하 테스트에 “AI Worker 3개로 동시 실행, DB lock 충돌 시뮬레이션, SQS 메시지 재노출 0건 확인”을 통과 기준으로 추가.

---

## 4. Sprint 단위 분해

각 Sprint는 실행 가능 상태 유지. Done/리스크/검증을 작업 단위로 명시.

### Pre-Phase: Tier Enforcer 사전 런타임 검증 (Phase 0 이전 선행)

**목적**: `enforce_tier_limits` 호출부와 시그니처 불일치로 인한 **Worker 부팅 시 TypeError** 방지. 런타임에서 실제로 터질 수 있는 항목이므로 Phase 1 이전에 반드시 수행.

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| Pre-1 | 호출부 단위 테스트 1건 추가 | `enforce_tier_limits`를 호출하는 코드 경로(예: `academy/framework/workers/ai_sqs_worker.py` 또는 legacy `sqs_main_cpu.py`)에 대한 단위 테스트 1건. 인자 개수/이름 일치 확인. | 없음 | 테스트 실행 시 TypeError 0건 |
| Pre-2 | payload 인자 정리 | `enforce_tier_limits(tier=..., job_type=..., payload=...)` 호출 시 **payload 인자 제거** 또는 함수 시그니처에 `payload: Optional[dict] = None` 명시. 현재 `apps/worker/ai_worker/ai/pipelines/tier_enforcer.py` 19행은 `(tier, job_type)` 만 받음. | payload 제거 시 tier/job_type만으로 제한 판단 가능한지 확인 | 호출부와 정의부 시그니처 일치 |
| Pre-3 | Worker 부팅 시 TypeError 확인 | AI Worker 컨테이너(또는 로컬) 기동 후 메시지 1건 수신 → tier 검증 구간까지 실행. 부팅 직후 또는 첫 메시지 처리 시 `TypeError: ... unexpected keyword argument 'payload'` 발생 여부 확인. | 미수행 시 프로덕션에서 첫 요청 시 크래시 | Worker 로그에 TypeError 0건, 정상 처리 1건 |

**변경 파일 목록 (예상)**  
- `academy/framework/workers/ai_sqs_worker.py` (94행 근처: `enforce_tier_limits` 호출에서 `payload` 제거 또는 tier_enforcer에 optional 추가)  
- `apps/worker/ai_worker/ai/pipelines/tier_enforcer.py` (선택: `payload: Optional[dict] = None` 추가)  
- 테스트: `tests/` 또는 `academy/tests/`에 `enforce_tier_limits` 호출부 단위 테스트 1건

**다음 단계**: Phase 0.

---

### Phase 0: 구조 생성

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| 0-1 | `academy/domain`, `application`, `adapters`, `framework` 디렉터리 및 `__init__.py` 확인/보완 | 기존 academy 구조와 충돌 없음, import 가능 | 없음 (이미 존재) | `from academy.domain.ai import entities` 등 실행 |
| 0-2 | 의존성 규칙 문서화 | `HEXAGONAL_10K_EXECUTION_PLAN.md` 본 문서에 규칙 명시 | - | 코드리뷰 |

**변경 파일 목록**: 없음(이미 구조 있음) 또는 `academy/**/__init__.py` 보완.  
**다음 단계**: Phase 1.

---

### Phase 1: AI 도메인·상태 전이 이전

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| 1-1 | AIJob entity를 domain으로 통합 | `academy/domain/ai/entities.py`에 이미 존재. `apps/shared/contracts/ai_job.py`와 역할 분리 유지(Contract는 API↔Worker 직렬화용, Entity는 도메인 규칙용). | Contract와 Entity 이중 정의로 불일치 가능 | Contract from_dict ↔ Entity 변환 테스트 1건 |
| 1-2 | 상태 전이 로직을 domain 메서드로 | `academy/domain/ai/entities.py`의 `start`, `complete`, `fail` 유지. Repository는 entity 메서드 호출 후 DB 반영. | `apps.domains.ai.services.status_resolver.status_for_exception` 의존 (adapters에서만 호출) | `repositories_ai.py` 128행: status_resolver 호출을 adapter 내부로 한정 확인 |
| 1-3 | 멱등 정책을 application에 명시 | `prepare_ai_job`에서 이미 DONE/FAILED면 None 반환. `complete_ai_job`/`fail_ai_job` 멱등 유지. | - | 기존 use case 단위 테스트 또는 수동 2회 호출 |

**변경 파일 목록 (예상)**  
- `academy/domain/ai/entities.py` (이미 존재, 필요 시 보완)  
- `academy/application/use_cases/ai/process_ai_job_from_sqs.py` (이미 멱등 반영)  
- `academy/adapters/db/django/repositories_ai.py` (status_resolver import 위치 확인)

**다음 단계**: Phase 2.

---

### Phase 2: Use Case 작성

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| 2-1 | AI 처리 use case 고정 | SQS 메시지 → prepare_ai_job → (inference) → complete_ai_job/fail_ai_job. 이미 `process_ai_job_from_sqs.py`에 구현됨. | Framework가 여전히 `apps.shared.contracts`, `apps.worker.ai_worker.ai.pipelines.dispatcher` import (`ai_sqs_worker.py` 88–90행) | Framework에서 application use case + inference만 호출하도록 할지, inference를 application으로 올릴지 결정 필요 |
| 2-2 | “상태 전이 코드 0건” 목표 | Worker 루프에는 use case 호출만. mark_processing/complete_job/fail_job 직접 호출 제거. | Legacy `sqs_main_cpu.py`에 여전히 queue.mark_processing/complete_job/fail_job 직접 호출 (258, 283, 345행) | Academy 경로로 기동 시 `prepare_ai_job`/`complete_ai_job`/`fail_ai_job`만 호출되는지 로그/추적 |

**변경 파일 목록 (예상)**  
- `academy/application/use_cases/ai/process_ai_job_from_sqs.py`  
- `academy/framework/workers/ai_sqs_worker.py` (inference 호출부를 application 또는 port 뒤로 이동)

**다음 단계**: Phase 3.

---

### Phase 3: DB Adapter 구현

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| 3-1 | Django ORM 접근을 adapters로 격리 | `academy/adapters/db/django/repositories_ai.py`에서만 `apps.domains.ai.models` import (메서드 내부 lazy). | 다른 경로에서 AIJobModel 직접 참조 잔존 | `grep -r "AIJobModel\|AIResultModel" --include="*.py"` 결과가 adapters/django 또는 apps.domains.ai.models 정의부만 |
| 3-2 | select_for_update, atomic을 repository로 | 이미 `repositories_ai.py`의 `get_for_update`, `mark_running`, `mark_done`, `mark_failed`에 반영. UoW는 `academy/adapters/db/django/uow.py`. | UoW 경계 밖에서 트랜잭션 열면 lock 시간 증가 | 단위 테스트: 한 트랜잭션 내 get_for_update → mark_done |
| 3-3 | Video/Messaging DB 접근 | Video: `src/infrastructure/db/video_repository.py`가 `apps.support.video.models` 사용. Phase 6·7에서 `academy/adapters/db/django`로 이전. | - | Phase 6에서 검증 |

**변경 파일 목록 (예상)**  
- `academy/adapters/db/django/repositories_ai.py`, `uow.py`  
- 기존 `src/infrastructure/db/ai_repository.py` 참조 제거 시 호출부를 academy adapter로 교체

**다음 단계**: Phase 4.

---

### Phase 4: SQS Adapter + Visibility

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| 4-1 | AI Worker visibility 연장 구조화 | `academy/adapters/queue/sqs/visibility_extender.py` 이미 존재. `ai_sqs_worker.py` 196–201행에서 start/stop. | Legacy 경로 사용 시 연장 없음 (300초만) | 환경변수 `USE_LEGACY_AI_WORKER` 미설정 시 academy 경로 사용, 로그에 "Visibility extended" 확인 |
| 4-2 | AI 큐 기본 Visibility 상향 (선택) | `scripts/create_ai_sqs_resources.py` 29·35·41행 300/300/600. 신규 큐는 3600 권장. 기존 큐는 AWS 콘솔에서 수동 변경. | 기존 큐 300초 유지 시 장작업 시 재노출 | 큐 속성 VisibilityTimeout=3600 확인 |
| 4-3 | Video Worker visibility | 500명 단계: VisibilityTimeout 10800 + 단일 change_message_visibility 1회. 10K 단계: 주기적 extender 도입 필수. | 아래 Video Visibility 정책 고정. | 500 단계 10800 확인. 10K 진입 Sprint에서 extender 도입 |

**Video Visibility 정책 (단계 구분 고정)**

- **500명 런칭 단계**: VisibilityTimeout 10800 + **단일 change_message_visibility 1회** 유지
- **10K 확장 단계**: AI와 동일한 **주기적 visibility extender** 도입 (필수)

이번 Big Bang에서는 Video는 **500명 정책 유지**.  
10K 진입 Sprint에서 extender를 도입한다.

**변경 파일 목록 (예상)**  
- `academy/adapters/queue/sqs/visibility_extender.py`, `ai_queue.py`  
- `apps/worker/ai_worker/sqs_main_cpu.py` (legacy fallback 시 경고 로그 또는 제거)  
- `scripts/create_ai_sqs_resources.py` (기본 visibility 값 문서/상향)

**다음 단계**: Phase 5.

---

### Phase 5: Framework 정리

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| 5-1 | Worker 엔트리를 academy로 | AI: `sqs_main_cpu.py`에서 `run_ai_sqs_worker()` 우선 호출 (이미 구현). Video/Messaging은 Phase 6에서 `academy/framework/workers` 진입점 추가. | Django setup은 엔트리에서만 (`if DJANGO_SETTINGS_MODULE: django.setup()`). framework 내부에서는 use case만 호출. | 엔트리 스크립트가 `academy.framework.workers.*` 실행하는지 확인 |
| 5-2 | Django API 얇게 유지 | API 라우트는 HTTP 수신 → application use case 호출. 도메인/application에서 django import 0건. | views에서 직접 ORM 호출 다수 존재 시 점진 이전 필요 | `grep -r "from django\|import django" academy/domain academy/application` 결과 0건 |
| 5-3 | tier_enforcer 시그니처 정합성 | payload 인자 제거 완료. 현재 시그니처 `(tier, job_type)` 기준으로 호출 정합성 확보. | 없음 | Worker 기동 후 메시지 1건 처리 시 TypeError 0건 확인 |

**변경 파일 목록 (예상)**  
- `apps/worker/ai_worker/sqs_main_cpu.py`  
- `academy/framework/workers/ai_sqs_worker.py` (tier_enforcer 호출 시그니처 정리)  
- Django API views (use case 주입 경로 정리)

**다음 단계**: Phase 6.

---

### Phase 6: Video/Messaging 동일 패턴

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| 6-1 | Video domain/application/adapters | Video job entity, process_video use case, SQS/Storage adapter를 academy 구조로 이전. | `src/application/video/handler.py`, `src/infrastructure/video/processor.py`가 apps 직접 참조 | processor 내부 apps 의존을 adapter 호출로 교체 |
| 6-2 | Video Worker framework 진입점 | `academy/framework/workers/video_sqs_worker.py` 추가. SQS 수신 → use case → visibility 연장(선택) → 완료/실패. | 기존 `sqs_main.py`와 이중 유지 기간 | 새 진입점으로 1건 처리 E2E |
| 6-3 | Messaging 동일 | Messaging job, use case, adapter, framework worker. | SOLAPI 등 외부 API는 adapters에만 | 메시지 1건 발송 E2E |

**변경 파일 목록 (예상)**  
- `academy/domain/video/`, `academy/application/use_cases/video/`, `academy/adapters/` (video), `academy/framework/workers/video_sqs_worker.py`  
- `src/infrastructure/video/*`, `src/application/video/*` 참조 제거 또는 academy 위임

**다음 단계**: Phase 7.

---

### Phase 7: src 폴더 제거

| 작업 | 내용 | Done 조건 | 리스크 | 검증 방법 |
|------|------|-----------|--------|-----------|
| 7-1 | src/infrastructure 의존 제거 | `src/infrastructure/video/processor.py`의 `apps.worker.*`, `apps.core.r2_paths` → academy adapters 또는 framework에서 주입. `src/infrastructure/db/*`, `src/infrastructure/ai/sqs_adapter.py` → academy adapters로 대체. | import 경로 변경 시 런타임 오류 | 전체 테스트 스위트, Worker 1건씩 실행 |
| 7-2 | src/application 의존 제거 | `src/application/services/excel_parsing_service.py` 255행 `apps.domains.enrollment.services.lecture_enroll_from_excel_rows` → application use case 또는 port로 분리. | 엑셀 수강등록 플로우 회귀 | 엑셀 수강등록 E2E |
| 7-3 | src 삭제 또는 빈 재export | 모든 참조가 academy로 이전 후 `src/` 삭제 또는 `src`는 `academy` 재export만. | 남은 참조 있으면 ImportError | `grep -r "from src\.\|import src\."` 결과 0건 후 src 디렉터리 제거 |

**변경 파일 목록 (예상)**  
- `src/infrastructure/**` 호출부 → `academy/adapters/**`  
- `src/application/**` 호출부 → `academy/application/**`  
- `apps/worker/video_worker/sqs_main.py` 등 `src.infrastructure` import 제거  
- 최종: `src/` 디렉터리 삭제 또는 유지 시 문서화

**다음 단계**: 완료 기준 검증.

---

## 5. 부하 테스트 시나리오 및 통과 기준

| 시나리오 | 조건 | 통과 기준 | 근거 |
|----------|------|-----------|------|
| **API 동시 접속** | 500명 런칭: 동시 50 요청, 60초 | 5xx 0건, P95 &lt; 3초 | `/health` 및 주요 API 1종 |
| **DB 연결** | Gunicorn 4 worker + 워커 3종 각 1프로세스 | 활성 DB 연결 수 &lt; 20 (db.t4g.micro 기본 max_connections 내) | `apps/api/config/settings/base.py` CONN_MAX_AGE=60, 가이드 §오픈 전 2번 |
| **AI Job 멱등** | 동일 job_id로 SQS 메시지 2건 연속 전송 (가능하면 동시) | 1건만 RUNNING → DONE, 2건째 prepare → None, 메시지 삭제. DB에 DONE 1건만. | `prepare_ai_job` None 반환 시 메시지만 삭제 |
| **AI 장작업 Visibility** | 1건 AI job 처리 시간 &gt; 300초 가정 | Academy 경로 사용 시 "Visibility extended" 로그 주기 출력. 메시지 재노출 없음 (중복 처리 0건). | `academy/adapters/queue/sqs/visibility_extender.py` 63–71행 |
| **Video 인코딩 1건** | 짧은 영상 1건 SQS 전송 | 처리 완료, 메시지 삭제, DLQ 0건. 디스크 사용량 증가 후 처리 완료 시 정리. | `apps/worker/video_worker/sqs_main.py`, 가이드 §8 |
| **10K 준비 (선택)** | 동시 AI job 20건 (Lite/Basic 혼합) | DLQ 없이 완료. DB lock 대기 로그 없음 (또는 허용 가능 수준). | Repository select_for_update, lease 기간 |
| **재시작 안정성 (필수)** | AI job RUNNING 상태에서 Worker kill → 재시작 후 재처리 | (1) 메시지가 visibility 만료로 재노출되거나, (2) lease_expires_at 초과로 다른 워커가 재시도. **정상 재처리 1회**, **중복 완료 0건** (DONE 2회 기록 없음). | 실전 SaaS에서 자주 발생: Worker 재시작/크래시 시 RUNNING job 처리. |
| **재시작 안정성 — lease_expires_at** | RUNNING인데 lease_expires_at 지남 → 다른 워커가 같은 메시지 수신 시 | prepare_ai_job에서 “이미 RUNNING이면” 기존 구현에 따라 None 또는 재시도. **중복 DONE 기록 0건**. | `repositories_ai.py` mark_running: PENDING/RETRYING만 RUNNING 전이. lease 만료 시 재시도 정책에 따라 RETRYING 등 처리. |
| **재시작 안정성 — visibility 만료 직후** | 처리 중 visibility 연장 실패 → 메시지 재노출 → 다른 워커가 수신 | 두 번째 워커가 prepare_ai_job 시도 시 이미 RUNNING/DONE이면 None 반환, 메시지만 삭제. **중복 완료 0건**. | Idempotency + prepare_ai_job None 반환. |

**재시작 안정성 테스트 절차 (필수 1회)**  
1. AI job 1건 enqueue, Worker가 메시지 수신 후 RUNNING 전이 직후 **Worker 프로세스 kill** (SIGKILL 또는 컨테이너 stop).  
2. Worker 재기동 (또는 다른 Worker가 메시지 수신).  
3. **통과 기준**: 해당 job_id에 대해 DONE 1건만 DB/결과에 존재. 로그에 “idempotent skip” 또는 “already DONE” 1회. 중복 완료 0건.

**중간 결과 보존 (현재 미정의)**  
inference가 45분 진행 후 Worker가 죽으면 **재시작 시 0부터 재처리**. **Checkpoint/재개 전략 없음** → CPU 45분 낭비, SLO 지연 증가. 문서 상 정상 재처리 1회·중복 완료 0건만 보장. 10K에서 장기 inference job이 많아지면 **중간 결과 저장·재개** 검토.

**실행 방법**  
- 수동: API는 `curl` 또는 부하 도구로 `/health`, 로그인/목록 1종.  
- AI: 테스트 스크립트 또는 API로 job 생성 후 SQS 직접 send 2건 (동일 job_id).  
- 검증: DB에서 해당 job_id 상태 1건 DONE, Worker 로그에 idempotent skip 1건.

---

## 6. 런칭(500명) 운영 체크리스트 및 알람 최소 세트

### 6.1 배포 전 체크리스트 (500명)

| # | 항목 | 확인 방법 | 근거 |
|---|------|-----------|------|
| 1 | RDS 퍼블릭 액세스 아니오 | RDS 콘솔 → 해당 인스턴스 → 퍼블릭 액세스 **아니오** | AWS 500 가이드 §배포 전 5가지 1 |
| 2 | Video Worker 100GB 마운트 | EC2 `df -h` → `/mnt/transcode` 약 100G | AWS 500 가이드 §2, §8.1 |
| 3 | CloudWatch 로그 보관 7~14일 | Log groups → Retention 7일 또는 14일 | AWS 500 가이드 §11.1 |
| 4 | EC2 Idle Stop 동작 | Video 1건 처리 후 큐 비움 → 5회 empty poll 후 인스턴스 Stop | AWS 500 가이드 §오픈 전 실전 3 |
| 5 | 8000 포트는 테스트용만 / 오픈 전 HTTPS | ALB + Target Group `/health` + ACM 443 + 80→443 리다이렉트 | AWS 500 가이드 §오픈 전 필수 |
| 6 | RDS max_connections 확인 | `SHOW max_connections;`로 확인(필수). API+Worker 합산 연결 모니터링. | AWS 500 가이드 §오픈 전 실전 2 |
| 7 | AI Worker 경로 | `USE_LEGACY_AI_WORKER` 미설정 또는 0. Academy 경로 사용 시 visibility 연장 동작 | 본 문서 §3 병목 3 |
| 8 | DB 마이그레이션 적용 | `python manage.py migrate` | OPERATIONS.md §1.2 |
| 9 | DLQ 처리 절차 | DLQ 발생 시 §8.4 **DLQ 처리 절차 (Step-by-step)** 6단계 준수. 메시지 저장 → 원인 확인 → 수정 배포 → Redrive → 24h 모니터링. | 본 문서 §8.4 |

### 6.2 알람 최소 세트

| 알람 | 조건 | 트리거 | 조치 |
|------|------|--------|------|
| API 5xx | ALB 5xx 또는 `/health` 실패율 1% 초과, 5분 | CloudWatch Alarm (ALB 5XXCount 또는 custom metric) | 로그 확인, 롤백 또는 핫픽스 |
| RDS 연결 수 | 활성 연결 &gt; max_connections × 0.8 | RDS CloudWatch `DatabaseConnections` | 스케일/설정 검토, PgBouncer 검토 |
| DLQ 메시지 | AI 또는 Video DLQ `ApproximateNumberOfMessagesReceived` &gt; 10 | SQS CloudWatch | DLQ 내용 확인, 재처리 또는 버그 수정 |
| Video Worker 디스크 | 디스크 사용률 &gt; 80% (100GB 볼륨) | CloudWatch 사용자 메트릭 또는 에이전트 | 정리 또는 볼륨 확장 |
| EC2 상태 | Video/AI Worker 인스턴스 Running → Stopped (Self-stop 제외) | EC2 상태 알람 (선택) | 의도된 Self-stop과 구분 필요 |

**근거**  
- AWS 500 가이드 §11 검증, §12 안정성 평가, §13 요약 체크리스트.  
- 본 문서 §2 SLO/SLI, §3 병목 1·3·5·6.

---

## 8. 장애·배포·관측·캐시 전략 (10K~30K)

500명: Single-AZ·로그 기반으로 충분. 10K 이상에서는 아래를 반드시 정의·점검한다. **현재 코드에 없으면 “정의 예정”으로 명시.**

### 8.1 RDS 단일 실패 지점 (SPOF)

| 항목 | 500명 (현재) | 10K 목표 | 근거 |
|------|--------------|----------|------|
| **Multi-AZ** | 아니오 (Single-AZ, 비용 최소) | **예** 권장. **전환 트리거**: (1) 월 매출 [예: 500만 원] 이상 **또는** (2) 일 평균 AI job 500건 이상 **또는** (3) 사용자 5,000명 초과 시 검토. (매출 기준은 사업 정책에 따라 수치 확정.) 트리거 없으면 Single-AZ 유지. | AWS 500 가이드 §2. 10K에서 DB 5분 장애 = 전체 SaaS 정지. |
| **Failover 예상 시간** | 해당 없음 | 1~2분 (RDS Multi-AZ 전환 시 AWS 측 정책에 따름). 애플리케이션 재연결 지연 추가 가능. | AWS RDS 문서 |
| **RTO 목표** | 미정의 | **10분**. DB 복구 완료 시점부터 서비스 재개까지 목표. | 10K 이상 SLA 전제 |
| **RPO 목표** | 미정의 | **0~1분**. 허용 데이터 손실 구간. RDS 자동 백업 + PITR로 1분 단위 복구 가능 검토. | AWS 500 가이드 §2.1 백업 7일. |
| **백업 복구 테스트** | 미정의 | **분기 1회**. PITR 또는 스냅샷에서 복원 → DB 접근·마이그레이션 검증. | 실전 복구 가능성 검증 |

**완료 조건 (10K 전)**: RTO/RPO 수치 확정, Multi-AZ 전환 여부 결정, 백업 복구 테스트 1회 수행 및 문서화.

---

### 8.2 API 레이어 병목 (Gunicorn 튜닝)

**설계 원칙**: **API 서버는 AI inference를 절대 동기 호출하지 않는다.** 장시간·CPU bound inference는 Worker 전용. API는 job enqueue만 하고 상태 조회·결과는 DB/캐시에서 읽는다. (동기 inference 호출 시 gevent 이점 상실·타임아웃·연결 소진.)

10K에서 동시 요청 200~300 구간 대비. **현재 설정**: `docker/api/Dockerfile` 26–32행.

| 항목 | 현재 값 | 10K 권장 | 비고 |
|------|---------|----------|------|
| **worker_class** | gevent | gevent 유지 (비동기 I/O로 동시 처리). sync면 요청 대기 누적. | `docker/api/Dockerfile` 29행 |
| **workers** | 4 | 4~8 (인스턴스당). CPU 코어 수·실측 부하에 따라 조정. | `GUNICORN_WORKERS` |
| **timeout** | 120초 | 120초 유지. 장시간 API는 별도 큐·비동기 패턴 권장. | `docker/api/Dockerfile` 31행 |
| **worker-connections** | 1000 | 1000 유지. gevent 기준 동시 코루틴 수. | `GUNICORN_WORKER_CONNECTIONS` |
| **keepalive** | 명시 안 됨 | 2~5초 (프록시·ALB 대기 방지). Gunicorn `--keep-alive` 옵션 검토. | 확인 불가 |
| **max_requests / max_requests_jitter** | 명시 안 됨 | **10K 전 설정 권장**: `--max-requests 2000` `--max-requests-jitter 200`. gevent/라이브러리 메모리 누수 시 1~2년 운영 후 OOM 방지. | `docker/api/Dockerfile` CMD에 추가 |

**완료 조건 (10K 전)**: timeout·workers는 코드에 있음. keepalive·max_requests 반영 여부 결정 및 Dockerfile/CMD 문서화.

---

### 8.3 AI Inference 시간 상한

SLO “AI Job &lt; 1시간”은 목표이지만, **무한 루프·비정상 장시간 inference** 방지가 없으면 visibility만 연장되어 운영이 꼬인다.

| 항목 | 내용 | 완료 조건 |
|------|------|-----------|
| **Inference 최대 처리 시간** | **60분** (Basic/Lite). 초과 시 해당 job **강제 fail** (상태 FAILED 또는 REVIEW_REQUIRED), 메시지 삭제 또는 DLQ. | Use case 또는 Worker 루프에서 “처리 시작 시각 + 60분 &lt; now”이면 중단·fail_ai_job 호출. |
| **무한 루프 방지** | 하위 라이브러리(OCR/OMR 등)에 timeout 주입 가능 시 적용. 불가 시 프로세스 단위 최대 실행 시간(예: 60분) 후 강제 fail. | 문서화: 어떤 job_type에 timeout 적용 여부. |

**구현 위치 (고정)**  
반드시 다음 **한 곳**에만 구현한다: **`academy/framework/workers/ai_sqs_worker.py`** (권장) **또는** **`academy/application/use_cases/ai/process_ai_job_from_sqs.py`**.

**강제 로직 (필수)**  
- `start_time` 기록 (메시지 처리 시작 시점).  
- inference 완료 대기 중 또는 주기 검사: `if (now - start_time) > 3600:` → `fail_ai_job(uow, job_id, error_message="inference_timeout_60min", tier)` 호출 → 해당 SQS 메시지 delete → visibility extender stop.  
- 60분 초과 시 위 순서로 반드시 처리. (inference 라이브러리 hang·무한 루프 시 visibility만 연장되면 워커 throughput 0 되므로 코드 강제 필수.)

**현재 코드**: `academy/framework/workers/ai_sqs_worker.py`에 구현 완료. `INFERENCE_MAX_SECONDS`(기본 3600), 스레드 join(timeout=3600) 후 초과 시 fail_ai_job + delete + extender stop.

**Lease와 Visibility 관계 (필수)**  
`lease_expires_at`(DB)·`visibility_timeout`(SQS)·inference 상한(3600)이 따로 정의되면 **visibility 만료 전에 lease 만료** 또는 **lease 만료 전에 visibility 만료**가 되어 중복 처리 조건이 발생할 수 있다.  
**수식 (반드시 유지)**  
- **lease_expires_at ≤ visibility_timeout − safety_margin**  
  예: visibility 3600초, safety_margin 60초 → lease 만료 시점 ≤ 3540초.  
- 또는 **lease_expires_at &lt; visibility_timeout** (엄격).  
구현 시 **lease 갱신 주기(heartbeat)** 또는 **단일 lease 길이**를 SQS visibility 연장 주기·연장 길이보다 짧게 두어, “visibility는 유효한데 lease만 만료”로 다른 워커가 같은 메시지를 가져가는 상황을 방지한다.

**Lease 갱신 전략 (실행 전략 — 아래 중 하나 필수)**  
수식만 있으면 "언제·누가·어떻게 갱신하는가"가 없어 31분 시점에 lease 만료·visibility는 유효 → 다른 워커가 동일 job 수신 가능. **다음 네 가지 중 하나를 반드시 채택.**  
1. **Lease 고정**: lease_expires_at = 처리 시작 + 3540초. 갱신 없음. (단일 실행 59분 이내 가정.)  
2. **Lease를 visibility 연장 시 함께 갱신**: extender가 extend_visibility 호출할 때 같은 주기로 DB last_heartbeat_at·lease_expires_at 갱신. lease heartbeat 주기 = visibility 연장 주기 (예: 60초).  
3. **Lease heartbeat 주기 = visibility 연장 주기**: Worker에서 visibility 연장과 동일 주기로 DB lease_expires_at 갱신. (`apps/domains/ai/queueing/db_queue.py` heartbeat() 참고.)  
4. **Lease 미사용**: DB lease 미사용, **visibility만 단일 진실**. RUNNING 전이만 하고 중복 수신 여부는 visibility에만 의존.

**현재 코드**: `db_queue.py`에 heartbeat(job_id, worker_id) 존재. SQS Worker 경로에서 heartbeat 호출 여부·주기는 구현체에 따름.

**채택 확정 (Big Bang 준비)**: **옵션 (1) Lease 고정**. lease_expires_at = 처리 시작 + 3540초, 갱신 없음. 구현: `academy/framework/workers/ai_sqs_worker.py` 상수 `LEASE_SECONDS` 기본값 **3540** (env `AI_JOB_LEASE_SECONDS`). visibility 3600 − safety_margin 60 = 3540과 일치.

**§8.3.1 DB unique 제약 (멱등 보장)**  
Application 레벨: `prepare_ai_job`에서 이미 DONE/FAILED면 None 반환. **DB 레벨**: job당 result 1행만 허용해야 중복 insert 불가.  
- **AIJobModel**: `job_id` unique (동일 job_id 2행 불가).  
- **AIResultModel**: `job` 필드가 `OneToOneField(AIJobModel, ...)` → job당 result 1행만 존재. DB unique 제약으로 중복 result row 방지.  
`mark_done` 시 get_or_create 또는 “job에 대한 result 존재 시 update만” 사용 시 DB 제약과 함께 **중복 insert 0건** 보장.  
근거: `apps/domains/ai/models.py` (AIJobModel.job_id unique, AIResultModel OneToOne).

**§8.3.2 AI Job lifecycle — Failure-state FSM**  
상태: **PENDING** → **RUNNING** → **DONE** | **FAILED** | (재시도 시 **RETRYING** → RUNNING).  
- **정상**: PENDING → (prepare_ai_job, mark_running) → RUNNING → (inference 완료, mark_done/fail_ai_job) → DONE/FAILED.  
- **Timeout(60분 초과)**: RUNNING → fail_ai_job 호출 → FAILED. SQS 메시지 delete → **DLQ로 이동하지 않음** (아래 Timeout vs DLQ 정책).  
- **Visibility 만료·재수신**: 두 번째 워커가 prepare_ai_job 시도 → 이미 RUNNING/DONE이면 None 반환, 메시지 삭제.  
- **Worker kill**: RUNNING 유지. lease/visibility 만료 후 다른 워커가 메시지 수신 시 기존 RUNNING 행 기준으로 재시도 또는 None. **중간 결과(checkpoint) 없음** → 재처리 시 처음부터 (§5·SLO 충돌 가능 시 “SLO는 단일 실행 기준” 명시).  
이 FSM을 Runbook 또는 본 문서에 고정하여 “어떤 상태에서 어떤 전이만 허용하는가”를 한 곳에서 관리.

---

### 8.4 DLQ 재처리 전략

DLQ 알람만 있고 **재처리 정책**이 없으면 운영 지옥. SaaS에서 DLQ는 반드시 발생한다. 아래 절차를 **본 문서 운영 체크리스트**로 포함한다.

**DLQ 처리 절차 (Step-by-step)**  
1. **SQS 콘솔** → 해당 DLQ (예: `academy-ai-jobs-lite-dlq`) → **메시지 확인**.  
2. **Message body 저장**: 메시지 1건 이상 본문을 로컬/문서에 저장 (재현·분석용).  
3. **원인 확인**: 로그·에러 메시지로 실패 원인 파악 (코드 버그·타임아웃·페이로드 오류 등).  
4. **수정·배포**: 코드/설정 수정 후 배포. 동일 입력 재전송 시 성공하는지 로컬/스테이징에서 확인.  
5. **Redrive to source queue**: SQS 콘솔에서 DLQ 메시지 선택 → **Start and finish moving messages to source queue** (또는 CLI로 메인 큐에 재전송). 원본 그대로 redrive 또는 수정 후 전송.  
6. **24시간 모니터링**: redrive 후 메인 큐·DLQ·Worker 로그 확인. 동일 메시지 재실패 시 2~4 반복.

| 항목 | 내용 | 완료 조건 |
|------|------|-----------|
| **담당** | DLQ 발생 시 온콜 또는 주기 점검 담당. DLQ 확인 주기: **일 1회** 권장. | 담당 역할·주기 명시. |
| **자동 redrive** | **없음**. 수동 redrive만 사용. 자동 이동 시 무한 루프 위험. | 자동 redrive 미사용. |
| **DLQ retention** | 14일. `MessageRetentionPeriod` 1209600. 14일 내 분석·redrive 또는 내보내기. | 14일 초과 시 삭제됨 인지. |

**완료 조건**: 위 6단계 절차를 §6 운영 체크리스트에 포함. 담당·주기 확정.

**Timeout vs DLQ vs Retry 정책 통합**  
- **60분 inference 상한**: 초과 시 `fail_ai_job` 호출 후 SQS 메시지 **delete** → 해당 메시지는 **DLQ로 이동하지 않음**.  
- **정책 정의**: timeout은 “실패”로 간주. 재시도는 **SQS maxReceiveCount**에만 의존(동일 메시지가 N회 수신 후 DLQ로). 60분 초과로 delete된 job은 DLQ에 없으므로 **운영자는 실패율·로그·메트릭으로만 timeout 감지**.  
- **일관성**: tier별 timeout 상이 여부(현재 60분 통일)와 “timeout 시 DLQ로 보낼지”는 설계 선택. **현재 채택**: timeout = 실패 처리 + 메시지 delete (DLQ 미사용). 변경 시 DLQ 정책·알람·Runbook과 함께 통합 문서화.

**Video Worker visibility (10K 이상 필수)**  
현재 Video는 **change_message_visibility 1회 호출만** (처리 시작 시). 인코딩이 3시간 20분 등 큐 visibility(10800)를 초과하면 메시지 재노출 가능. **10K 이상·장영상에서는 AI와 동일하게 주기적 visibility extender 도입 필수** (옵션이 아님). 중복 인코딩은 비용·지연 폭증. Phase 4-3 참고.

---

_(아래 표는 참고용. 실제 절차는 위 6단계.)_  
| _참고_ | DLQ 메시지 발생 시 **누가** 확인·재처리: 온콜 또는 주기 점검 담당. | Runbook에 “DLQ 확인 주기(예: 일 1회)” 및 담당 역할 명시. |
| **재처리 방법** | (1) SQS 콘솔/CLI로 DLQ → 메인 큐 redrive (원본 메시지 그대로 또는 수정). (2) 버그 수정 후 재배포 후 redrive. (3) 비복구 가능 메시지는 로그 보관 후 삭제. | Runbook에 “AI/Video/Messaging DLQ redrive 절차” 1회 문서화. |
| **자동 redrive** | 현재 **없음**. 람다 등으로 DLQ → 메인 자동 이동 시 무한 루프 주의. 수동 redrive 권장. | “자동 redrive 없음” 또는 정책 명시. |
| **DLQ retention** | SQS 기본 14일. `scripts/create_sqs_resources.py`, `create_ai_sqs_resources.py`에서 `MessageRetentionPeriod` 1209600(14일). | 14일 초과 메시지는 삭제됨. 중요 메시지는 14일 내 분석·redrive 또는 내보내기. |

**완료 조건**: Runbook에 DLQ 확인 주기·redrive 절차·담당 명시. 자동/수동 정책 확정.

---

### 8.5 캐시 계층 (10K+ read-heavy)

현재 전부 DB 직조회. 10K 이상에서 **학생 목록·강의 목록·통계 API** 등 read-heavy 구간이 DB 부하로 직결된다.

| 항목 | 내용 | 완료 조건 |
|------|------|-----------|
| **Redis 사용 여부** | 현재: 선택(미설정 시 DB fallback). `docs/ARCHITECTURE_AND_INFRASTRUCTURE.md` §7. | 10K 이상에서 read-heavy API는 **Redis cache layer 도입 고려** 명시. |
| **Cache TTL 기준** | 미정의 | **구체화**: 학생 목록 **120초**, 강의 목록 **300초**, 통계 **60초**. 개인화·실시간 API: 캐시 미사용 또는 짧은 TTL. | 10K 전 TTL 정책 문서화. |
| **Cache invalidation** | 미정의 | **쓰기 직후 무효화**: (1) 학생 수정/삭제 시 해당 tenant·학생 목록 캐시 키 무효화. (2) 강의 변경 시 해당 강의·강의 목록 캐시 무효화. (3) 통계 원본 변경 시 해당 통계 캐시 무효화. TTL만 있으면 갱신 지연. | Redis 도입 시 invalidation 경로 3줄 문서화. |
| **Consistency 모델** | 미정의 | TTL 캐시는 **eventual consistency**. Strong 필요 구간은 캐시 미사용 또는 쓰기 직후 무효화. **Stampede 방지**: 동일 키 동시 miss 시 단일 fill만 허용(lock·probabilistic early expiry 등). | Redis 도입 시 "eventual + stampede 대비" 1줄 명시. |
| **계획 없음 상태 제거** | “캐시 계획 없음”은 위험. 최소한 “10K 이상에서 read-heavy API는 Redis 도입 검토, TTL 기준 정의”를 문서에 넣음. | 본 문서 또는 OPERATIONS.md에 위 문구 반영. |

**현재 코드**: Redis 설정·캐시 레이어 코드 경로 미확인. “정의 예정”으로 두고 10K 전에 구체화.

---

### 8.6 배포 전략 (Single-shot → 구조화)

PR 단위·이전 이미지 태그는 있으나, **Blue/Green·Rolling·마이그레이션 호환**이 없으면 Big Bang 배포 시 다운타임·롤백 리스크.

| 항목 | 내용 | 완료 조건 |
|------|------|-----------|
| **Blue/Green** | 현재 없음. 10K 이상에서 무중단 전환 시 ALB Target Group 2개·교체 방식 검토. | “500명: 단일 배포. 10K: Blue/Green 검토” 문서화. |
| **Rolling update** | ECS/EC2 배포 시 한 대씩 교체로 무중단. 현재 EC2 수동 배포 시 절차 명시. | 배포 Runbook에 “이전 이미지 pull → 새 컨테이너 기동 → 이전 종료” 순서 고정. |
| **DB migration backward compatibility** | Hexagonal 전환·스키마 변경 시 **이전 앱 버전이 새 스키마에서 동작** (또는 역마이그레이션 가능)해야 롤백 가능. | Phase 3에서 스키마 변경 시 “이전 코드 호환 구간” 정의. 2단계 배포(스키마 먼저 → 코드 배포) 검토. |

**스키마 변경 시 배포 순서 (DB 먼저, 코드 나중)**  
1. Backward compatible migration 먼저 배포 (새 컬럼 추가 등 구버전이 무시 가능한 변경).  
2. 코드 배포 (새 스키마 사용).  
3. 구버전 컬럼/테이블 제거용 migration은 그 다음 배포.  
→ 롤백 시 이전 코드가 현재 DB에서 동작 가능.

**완료 조건**: 배포 Runbook에 Rolling 절차 + 위 3단계 순서 명시.

---

### 8.7 관측: 로그 → 메트릭 기반 (10K+)

500명: 로그 검색·CloudWatch retention으로 충분. 10K 이상은 **메트릭 기반 알람** 없이 수동 로그 집계만으로는 한계.

| 항목 | 내용 | 완료 조건 |
|------|------|-----------|
| **현재** | 로그 기반. `SQS_JOB_COMPLETED` 등 로그로 집계. CloudWatch Logs retention 7~14일. | §3 병목 6, §6.2 알람. |
| **10K 목표** | **메트릭 기반 alert**: 성공률, 처리량(건/분), latency histogram. CloudWatch Custom Metrics 또는 APM(예: X-Ray, Datadog) 도입 검토. | “로그만”이 아닌 메트릭 차원 정의: 예) AI job success rate %, AI job count/min, API P95 latency. |
| **알람 트리거 (수치 고정)** | 5xx율·DLQ·DB 연결 외, **메트릭 기반**: (1) **AI success rate &lt; 95%** for 5 minutes → 알람. (2) **AI throughput = 0** during peak hour (정의: 1시간 구간 내 완료 0건) → 알람. 수치 없으면 운영 불가. | CloudWatch 알람 또는 APM에 위 2건 설정. |
| **수동 집계 제거** | “SQS_JOB_COMPLETED 로그로 집계”는 수동. 10K에서는 자동 집계(메트릭) 권장. | 문서에 “10K 전 메트릭 파이프라인 검토” 명시. |

**완료 조건**: 10K 전에 “관측 메트릭 목록(성공률·처리량·레이턴시)” 및 알람 트리거 1건 이상 문서화.

---

### 8.8 AI Worker CPU 병목 (10K)

10K에서 **DB보다 먼저 터질 수 있는 것**: CPU inference. **"왜 처리량이 안 올라가?"** 상황을 막으려면 아래 처리 모델을 반드시 명시한다.

**AI Worker 처리 모델 (고정)**  
- **1 프로세스**당 **1 메시지**만 동시 처리.  
- 처리 **완료 후** 다음 메시지 수신 (순차 처리).  
- 동시 처리 확장은 **인스턴스 수 증가**로만 달성 (프로세스 다중화·동시도 상향은 별도 설계 필요).

| 항목 | 내용 | 완료 조건 |
|------|------|-----------|
| **인스턴스 스펙** | AWS 500 가이드 §9: AI Worker CPU 0.5 vCPU / 1GB (부하 시 2GB). EC2 t4g.micro~t4g.small. | 현재 문서·가이드에 1대 기준 스펙 있음. |
| **CPU 코어 수** | t4g.micro 2 vCPU, t4g.small 2 vCPU. **inference가 단일 스레드**면 코어 2개여도 동시 1건 처리. | 코드에서 inference 동시도(동일 프로세스 내 여러 job 동시 처리 여부) 확인. 현재 Worker 1프로세스 1메시지 순차. |
| **inference concurrency** | 현재 **1**. 한 메시지 처리 끝난 뒤 다음 수신. 10K에서 지연 줄이려면 Worker 인스턴스 수 확장(3~5대) 또는 프로세스 다중화 검토. | “AI Worker 동시 처리 = 1건/프로세스” 명시. 확장 시 “인스턴스 수 증가”로 처리량 확보. |
| **메모리 사용량** | 1GB~2GB. OMR/OCR 등 모델 로딩 시 메모리 스파이크. 10K에서 장작업 동시 다건 시 OOM 모니터링. | CloudWatch 메모리 메트릭 또는 컨테이너 상한(`--memory`) 문서화. |

**SQS polling 설정 (필수 명시)**  
Worker 수평 확장 시 long polling·max_number_of_messages·prefetch 미정의면 worker starvation·unfair distribution 가능. **receive_message(wait_time_seconds=20, max_number_of_messages=1)** 로 1건만 수신 (prefetch 없음). 코드/설정에 위 값 명시. Lite/Basic 큐 공통.

**완료 조건**: 10K 확장 시 “AI Worker: 1프로세스 1동시, 확장은 인스턴스 수로” 및 메모리 상한 1줄 문서화.

---

## 7. 완료 기준 검증 (최종)

| 기준 | 검증 방법 |
|------|-----------|
| domain/application에서 django import 0건 | `grep -r "from django\|import django" academy/domain academy/application` → 0건 |
| adapters 외부에서 ORM 접근 0건 | `grep -r "\.objects\.\|select_for_update\|get_or_create" --include="*.py"` 결과가 `academy/adapters/db/django` 또는 `apps/*/models` 정의부만 |
| Worker에서 상태 전이 코드 0건 | Worker 진입점 코드에 `mark_processing`/`complete_job`/`fail_job` 직접 호출 없음, use case만 호출 |
| AI visibility 중복 처리 해결 | **Legacy 완전 제거**: (1) `apps/worker/ai_worker/sqs_main_cpu.py`에서 legacy `main()` 코드 제거 **또는** `USE_LEGACY_AI_WORKER` 분기 제거. (2) Academy 경로만 사용, visibility 3600 + 주기 연장. 재노출 로그 없음. |
| USE_LEGACY_AI_WORKER 제거 | 코드베이스에 Legacy 분기 잔존 0건. **검증**: `grep -r "USE_LEGACY_AI_WORKER"` → 0건. |
| src 폴더 제거 | `src/` 디렉터리 삭제 또는 참조 0건 문서화 |
| 기존 기능 동작 유지 | API 로그인/목록, AI job 1건, Video 1건, Messaging 1건 수동 또는 E2E 테스트 통과 |

### Legacy AI Worker 완전 제거 기준

- `apps/worker/ai_worker/sqs_main_cpu.py`에서 legacy main() 제거
- `USE_LEGACY_AI_WORKER` 분기 삭제
- `grep -r "USE_LEGACY_AI_WORKER"` 결과 0건
- AI Worker 진입점은 **academy/framework/workers/ai_sqs_worker.py** 단일 경로

Big Bang 이후 legacy 경로는 유지하지 않는다.

---

**문서 버전**: 1.5  
**기준 코드**: 위 “현재 코드 근거” 및 `docs/cursor_docs/AWS_500_START_DEPLOY_GUIDE.md`, `docs/ARCHITECTURE_AND_INFRASTRUCTURE.md`, `docs/OPERATIONS.md` 반영.  
**v1.1 보완 (6항목)**: (1) DB 연결 수 실제 숫자 §3.1 (2) Legacy AI 완전 제거 + grep USE_LEGACY_AI_WORKER §7 (3) Tier Enforcer Pre-Phase §4 (4) 10K Worker 수평 확장 §3.3 (5) DB 인덱스·EXPLAIN §3.2 (6) 재시작 안정성 §5.  
**v1.2 보완 (§8)**: 10K~30K 운영·장애·배포·관측: RDS SPOF·RTO/RPO §8.1, API Gunicorn §8.2, AI inference 60분 상한 §8.3, DLQ 재처리 §8.4, 캐시 Redis §8.5, 배포 전략 §8.6, 관측 메트릭 §8.7, AI Worker CPU §8.8.  
**v1.3 보완**: (1) AI 60분 상한 구현 위치 고정·강제 로직 §8.3 (2) DLQ 6단계 절차 본문 포함·§6 체크리스트 항목 (3) AI Worker 처리 모델 4줄 명시 §8.8 (4) Multi-AZ 전환 트리거 §8.1 (5) Gunicorn max_requests 2000/200 §8.2 (6) 메트릭 알람 수치 95%·0건 §8.7 (7) Redis TTL 학생 120·강의 300·통계 60 §8.5 (8) 스키마 변경 DB 먼저 3단계 §8.6.  
**v1.4 보완 (논리 보강)**: (1) lease_expires_at·visibility 수식 §8.3 (2) 중간 결과 보존 미정의 §5 (3) API P95 측정 범위 정의 §2 (4) Video visibility AI 수준 승격 권장 §8.4·Phase 4-3 (5) SQS polling wait_time_seconds·max_number_of_messages §8.8 (6) Cache invalidation 3줄 §8.5 (7) 30K AI 큐 로드맵 §1.  
**v1.5 보완 (AI 관점 완전 무결 4가지)**: (1) Lease 갱신 전략 §8.3 — 4가지 옵션 필수 채택 (2) DB unique §8.3.1 — job_id unique, OneToOne result 1행 (3) Timeout vs DLQ vs Retry §8.4 통합 (4) AI Job lifecycle FSM §8.3.2. 추가: Video 10K 필수, SLO 단일 실행 기준, DB worst_case, API inference 미호출 §8.2, Cache consistency·stampede §8.5.  
**유지**: Phase/Sprint 진행 시 이 문서를 SSOT로 갱신하고, “확인 불가” 항목은 코드 근거 확보 후 수치·경로로 대체할 것.
