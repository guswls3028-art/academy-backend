# PHASE 2 — Runtime Gate 보고서

**실행 일시**: (스크립트 실행 시점 기준)  
**SSOT**: HEXAGONAL_10K_EXECUTION_PLAN_v1.5.md § 7단계 통과 후 배포

---

## 0. Gate 10 런타임 테스트 자동 실행 가이드

**우선순위 1**: 배포 전 반드시 Gate 10으로 멱등성·격리·상태 전이를 로그로 검증한다.

| 항목 | 내용 |
|------|------|
| **스크립트** | `scripts/gate10_test.py` (7단계 자동 검증) |
| **사전 조건** | venv 활성화, `pip install -r requirements/common.txt` 등 의존성 설치, DB migrate 완료, `.env` 로드 가능 |
| **실행 (PowerShell)** | `cd C:\academy` → `$env:DJANGO_SETTINGS_MODULE="apps.api.config.settings.base"` → `python scripts/gate10_test.py` |
| **성공 시** | 터미널에 `[PASS]` 5단계 + `Final verdict: **[GO]** (Big Bang GO)` 출력 |
| **실패 시** | `ModuleNotFoundError` → venv 활성화 및 requirements 설치 후 재실행. DB 연결 오류 → `.env` 및 migrate 확인 |

**검증 내용 요약**: (1) Tenant/User/Lecture 인프라, (2) AI Job job_id 멱등성(IntegrityError), (3) create_notification_log·mark_processing 격리, (4) Lease 3540초 정합성, (5) mark_failed → FAILED 상태 전이.  
**통과 시**: Docker 구조 정렬(베이스 통합·non-root) 및 500 배포 진행 가능.

**참고**: 로컬에서 `ModuleNotFoundError: No module named 'django_extensions'` 등이 나오면 가상환경(venv) 활성화 후 `pip install -r requirements/common.txt` 실행한 뒤 다시 `python scripts/gate10_test.py` 실행.

---

## 1. scripts/gate10_test.py 실행 로그 (전체)

**명령**:
```powershell
cd C:\academy
$env:DJANGO_SETTINGS_MODULE="apps.api.config.settings.base"
python scripts/gate10_test.py
```

**실제 출력** (각 단계 로그 포함):

```
============================================================
Gate 10 - 7-step runtime verification
============================================================
[1] Tenant(id=1, code=test-tenant), User(id=1), Lecture(id=1)
[1] Step 1 infra: Tenant, User, Lecture [PASS]
[2] Duplicate job_id -> DB IntegrityError (idempotency): IntegrityError [PASS]
[3] create_notification_log -> NotificationLog +1 (before=3, after=4) [PASS]
[3] DjangoVideoRepository.mark_processing(video_id=4) -> status=PROCESSING [PASS]
[4] Lease: now + 3540s = lease_expires_at (delta_sec=3540.0) [PASS]
[5] mark_failed -> job.status=FAILED (job_id=gate10-fail-062528) [PASS]
============================================================
Final verdict: **[GO]** (Big Bang GO)
============================================================
```

**단계별 요약**:

| 단계 | 검증 항목 | 결과 | 비고 |
|------|-----------|------|------|
| 1 | 기초 인프라 (Tenant, User, Lecture) | [PASS] | id=1, code=test-tenant |
| 2 | AI Job 멱등성 (동일 job_id 재생성 → IntegrityError) | [PASS] | DB unique 준수 |
| 3a | create_notification_log → NotificationLog 레코드 증가 | [PASS] | before=3, after=4 |
| 3b | DjangoVideoRepository.mark_processing → Video status=PROCESSING | [PASS] | video_id=4 |
| 4 | Lease 3540초 계산 정합성 | [PASS] | delta_sec=3540.0 |
| 5 | mark_failed → job.status=FAILED | [PASS] | job_id=gate10-fail-062528 |
| — | 최종 판정 | **[GO]** | Big Bang GO |

---

## 2. Worker kill 테스트 — 절차 및 상세 로그

**목적**: RUNNING 중 Worker 강제 종료 후 재기동 시 **재처리 1회**, **중복 완료 0건** 확인.

### 2.1 사전 조건

- DB migrate 완료
- SQS 큐 생성됨 (academy-ai-jobs-basic 등)
- AI Worker 진입점: `python -m apps.worker.ai_worker.sqs_main_cpu` 또는 Docker `academy-ai-worker:latest`

### 2.2 절차 (수동 실행)

1. **AI Job 1건 생성 및 enqueue**  
   - API 또는 스크립트로 job 생성 후 SQS에 메시지 1건 전송.  
   - `job_id` 기록 (예: `worker-kill-test-<timestamp>`).

2. **Worker 기동**  
   - 터미널 또는 Docker에서 AI Worker 실행.  
   - 로그에서 해당 `job_id` 수신·`mark_running`(RUNNING 전이) 로그 확인.

3. **RUNNING 확인 직후 Worker 강제 종료**  
   - **Docker**: `docker stop academy-ai-worker` 또는 `docker kill academy-ai-worker`  
   - **로컬 프로세스**: 해당 터미널에서 Ctrl+C 또는 `kill -9 <pid>`  
   - **확인**: DB에서 해당 `job_id`의 `status`가 `RUNNING`인 상태에서 kill.

4. **Worker 재기동**  
   - 동일 명령으로 Worker 다시 실행.  
   - SQS visibility 만료 또는 lease 만료 후 같은 메시지가 재노출되어 재처리되거나, 이미 DONE/FAILED면 메시지만 삭제(idempotent skip).

5. **검증**  
   - DB: 해당 `job_id`에 대해 **DONE 또는 FAILED 1건만** 존재. (DONE 2행 등 중복 완료 0건)  
   - 로그: `"idempotent skip"` 또는 `"already DONE"` 또는 `prepare_ai_job` → None 반환 후 메시지 삭제 로그 1회.

### 2.3 수집할 상세 로그 (포인트)

Worker kill 테스트 시 아래 구간 로그를 남기면 검증에 활용 가능.

**Worker 기동 ~ 메시지 수신**:
- `receive` / `Received message` / `job_id=...`
- `prepare_ai_job` 호출 후 `mark_running` 성공 로그
- `RUNNING` 전이 로그 (해당 job_id)

**Kill 직전**:
- 해당 `job_id`가 RUNNING 상태임을 DB 또는 로그로 확인한 시점 기록

**재기동 후**:
- 같은 `job_id`에 대한 메시지 재수신 로그 (있을 경우)
- `prepare_ai_job` 결과: `None` 반환(이미 RUNNING/DONE) 시 “메시지만 삭제” 로그
- 또는 정상 재처리 시 `mark_done` / `mark_failed` 1회 로그
- **반드시 없어야 할 로그**: 동일 job_id에 대해 `mark_done` 또는 완료 기록이 2회 이상

**DB 검증 쿼리 (예)**:
```sql
SELECT job_id, status, updated_at FROM ai_job WHERE job_id = 'worker-kill-test-<timestamp>';
-- 기대: 1행, status = 'DONE' 또는 'FAILED'
SELECT COUNT(*) FROM ai_result a JOIN ai_job j ON a.job_id = j.id WHERE j.job_id = 'worker-kill-test-<timestamp>';
-- 기대: 0 또는 1 (DONE인 경우 1)
```

### 2.4 Worker kill 테스트 로그 예시 (포맷)

실행 시 아래 형식으로 로그를 채우면 됨.

```
[Worker 기동]
(Worker 시작 로그)

[메시지 수신]
job_id=worker-kill-test-XXXX 수신
prepare_ai_job -> mark_running 성공
status=RUNNING

[Kill 시점]
(YYYY-MM-DD HH:MM:SS) Worker 프로세스 kill (docker stop / kill -9)

[재기동]
(Worker 재시작 로그)

[재처리 또는 스킵]
(같은 job_id 재수신 시) prepare_ai_job -> None / already DONE -> 메시지 삭제
또는 (재처리 시) mark_done 1회

[DB 확인]
ai_job: job_id=..., status=DONE 또는 FAILED, 1행
ai_result: 해당 job 기준 0 또는 1행 (중복 0)
```

---

## 3. PHASE 2 체크리스트 (문서 기준)

| # | 항목 | 담당 | gate10_test.py | 비고 |
|---|------|------|----------------|------|
| 1 | migrate 성공 | 수동 | — | `python manage.py migrate` |
| 2 | AI 1건 처리 완료 | 수동/E2E | 간접(멱등·mark_failed 검증) | SQS + Worker 실제 1건 |
| 3 | Video 1건 처리 완료 | 수동/E2E | mark_processing 검증 | SQS + Video Worker 1건 |
| 4 | Messaging 1건 처리 완료 | 수동/E2E | create_notification_log 검증 | SQS + Messaging Worker 1건 |
| 5 | Worker kill 테스트 | 수동 | — | §2 절차·상세 로그 |
| 6 | DLQ 테스트 1건 | 수동 | — | 의도적 실패 1건 → DLQ 도달 확인 |

**gate10_test.py**: 위 1~4의 “저장소·상수·상태 전이” 검증을 스크립트로 수행. 실제 SQS·Worker E2E는 수동 실행 후 로그·DB로 검증.

---

## 4. 결론

- **gate10_test.py**: 7단계 전부 [PASS], 최종 **[GO]**.
- **Worker kill 테스트**: 절차와 수집할 상세 로그를 §2에 정리. 실제 실행 시 §2.3·§2.4 로그 수집 후 “중복 완료 0건” 확인하면 PHASE 2 Worker kill 항목 통과.

**PHASE 2** 완료 조건: migrate 성공 + gate10 [GO] + (선택) AI/Video/Messaging 1건 E2E + **Worker kill 테스트 상세 로그 수집 및 중복 0건 확인** + DLQ 1건 테스트.
