# Video Batch — 프로덕션 투입 최소 필수 수정 및 로드맵

이 문서는 **Spot 유지 vs On-Demand 우선 안정화** 결론, **최소 필수 수정 세트**, **안정성 80점 체크리스트**, **테스트 시나리오**, **실행 순서**를 한곳에 정리한 공식 체크리스트다.

---

## 1) Spot 유지 vs On-Demand 우선 — 결론 (현 상태 기준)

- **결론:** 현재 코드만으로는 **Spot 사용은 비합리적**이다. (의견이 아니라 “자동복구 경로 0”이 근거.)
- **근거:** Batch 워커에 SIGTERM 핸들러/heartbeat/Batch→DB sync가 없으면, Spot 중단·scale-in·인프라 종료가 곧 **DB orphan**으로 이어진다.
- **권장 로드맵:**
  1. **On-Demand로 bring-up** + 코드 보강 + 강제종료 테스트를 먼저 통과.
  2. 그 다음 **Spot을 “부분 도입 + fallback”**으로 전환.
- 이건 비용 문제가 아니라, **“실패했을 때 자동 복구/정리하는 경로가 코드에 존재하지 않는다”**가 확정이기 때문이다.

---

## 2) Production 투입 “최소 필수 수정 세트” (구현 상태)

아래 4개가 최소 세트다. **이 4개 없이는 안정성 80점이 구조적으로 불가**하다.

| # | 항목 | 구현 상태 | 위치 |
|---|------|-----------|------|
| (1) | RUNNING 전환 반드시 수행 | **구현됨** | `apps/worker/video_worker/batch_main.py`: `job_set_running(job_id)` 호출. `job_set_running`이 False면 즉시 종료. |
| (2) | heartbeat를 DB에 주기 갱신 | **구현됨** | `batch_main.py`: `_heartbeat_loop` 스레드에서 `VIDEO_JOB_HEARTBEAT_SECONDS`(기본 60초)마다 `job_heartbeat(job_id)` 호출. `finally`에서 `_heartbeat_stop.set()`. |
| (3) | SIGTERM/SIGINT 핸들러 + 종료 시 DB 반영 | **구현됨** | `batch_main.py`: `_handle_term`에서 `job_fail_retry(job_id, "TERMINATED")` 후 `sys.exit(1)`. `_cancel_check`에 `_shutdown_event.is_set()` 포함. |
| (4) | Batch→DB reconcile 루프/커맨드 | **구현됨** | `apps/support/video/management/commands/reconcile_batch_video_jobs.py`: describe_jobs 후 SUCCEEDED/FAILED/RUNNING/not_found에 따라 DB 갱신. `--resubmit` 시 재제출. |

### aws_batch_job_id 저장

- **저장 위치:** `apps/support/video/services/video_encoding.py` L49-50 — submit 성공 시 `job.aws_batch_job_id = aws_job_id` 및 `save(update_fields=["aws_batch_job_id", "updated_at"])`.
- scan_stuck에서 RETRY_WAIT 후 재제출 시에도 `scan_stuck_video_jobs.py` L81-82에서 `aws_batch_job_id` 갱신.

---

## 3) 안정성 80점 달성 체크리스트

### 3.1 DB Lifecycle 정렬

| 항목 | 상태 |
|------|------|
| batch_main에서 job_set_running(job_id) 호출 | ✅ |
| heartbeat 루프에서 job_heartbeat(job_id) 주기 호출 | ✅ |
| finally 블록 도입 (heartbeat 정지) | ✅ |
| 정상 완료 → job_complete | ✅ (기존) |
| 예외 → job_fail_retry | ✅ (기존) |
| SIGTERM/SIGINT → job_fail_retry(job_id, "TERMINATED") | ✅ |
| _cancel_check에 shutdown Event 포함 (process_video 내 _check_abort 연동) | ✅ |

### 3.2 Batch 제출 시 aws_batch_job_id 저장

| 항목 | 상태 |
|------|------|
| submit 결과 aws_job_id를 VideoTranscodeJob.aws_batch_job_id에 저장 | ✅ (video_encoding.py L49-50) |

### 3.3 Reconcile 커맨드

| 항목 | 상태 |
|------|------|
| Django management command | ✅ `reconcile_batch_video_jobs` |
| describe_jobs (boto3) 후 DB 반영 | ✅ |
| Batch SUCCEEDED + DB 미완료 → job_complete 또는 job_fail_retry | ✅ |
| Batch FAILED → job_fail_retry(job_id, statusReason) | ✅ |
| Batch RUNNING + DB QUEUED → job_set_running | ✅ |
| Batch에서 job 미조회 → job_fail_retry + (선택) resubmit | ✅ `--resubmit` |
| cron/스케줄 1~2분마다 실행 권장 | 문서화됨 |

### 3.4 scan_stuck 정렬

- **정렬안 1 채택:** Batch 워커가 RUNNING + heartbeat를 기록하게 해 기존 `scan_stuck_video_jobs`를 그대로 사용. (위 (1)(2) 반영으로 충족.)

---

## 4) 멀티테넌트 격리

- **현재:** internal API는 `job_get_by_id(job_id)`만 사용, tenant_id 필터 없음. body의 job_id만으로 DEAD 가능.
- **필요 조치:** internal endpoint는 **네트워크 레벨에서 완전 차단**(VPC 내부/allowlist) + 강한 인증. 코드 레벨에서는 (선택) 요청에 tenant_id 포함 후 job의 tenant 검증 또는 `job_get_by_id(job_id, tenant_id=...)` 형태로 조회 강화.

---

## 5) 인프라 bring-up 후 “반드시” 수행할 테스트 시나리오

아래는 **코드 수정(2~3번) 반영 후** 수행해야 의미가 있다.

### 5.1 정상 플로우

| 단계 | 기대 |
|------|------|
| submit | DB state: QUEUED |
| 컨테이너 시작 직후 job_set_running | DB state: RUNNING, last_heartbeat_at 채워짐 |
| 트랜스코딩 중 | heartbeat 주기 갱신 |
| 완료 시 job_complete | DB state: SUCCEEDED |

### 5.2 강제 종료/인프라 실패 (합격 기준)

| 시나리오 | 기대 결과 | 합격 기준 |
|----------|-----------|-----------|
| (A) AWS Batch terminate-job | SIGTERM 핸들러 동작 시 job_fail_retry(reason="TERMINATED"); 미동작 시 reconcile이 FAILED 감지 후 job_fail_retry(statusReason) | DB가 QUEUED로 영구 고정되지 않음. attempt_count 증가, RETRY_WAIT 전환. |
| (B) 컨테이너 OOM kill | 앱이 DB 갱신 못 하고 죽을 수 있음 | reconcile이 Batch FAILED 감지 → RETRY_WAIT + (선택) 재제출. |
| (C) EC2 인스턴스 terminate/scale-in | 앱 레벨 기록 실패 가능 | 일정 시간 내(예: 2~5분) DB가 RETRY_WAIT로 전환되고 재시도. |
| (D) 네트워크 장애(DB unreachable) | heartbeat/complete/fail 실패 가능 | reconcile이 “Batch는 끝났는데 DB는 RUNNING/QUEUED” 부정합 정리. |

### 5.3 테스트 실행 목록 (문서 기록)

- 정상 완료 5회  
- terminate-job 5회  
- OOM 3회 (메모리 제한 조정 등)  
- 인스턴스 terminate 3회  
- 네트워크 장애 3회 (가능하면)

---

## 6) 비용 대비 안정성 전략

| 단계 | 내용 |
|------|------|
| 1 | **On-Demand 단독** — bring-up + 최소 세트 반영 + 강제종료 테스트 통과. |
| 2 | **Spot 부분 도입 + On-Demand fallback** — CE 2개(On-Demand, Spot), Queue 우선순위로 Spot 우선, 부족/중단 시 On-Demand. 전제: SIGTERM + heartbeat + reconcile 존재. |

---

## 7) 실행 순서 (To-do)

1. **코드 최소 세트 반영** — ✅ 완료  
   - `batch_main.py`: job_set_running, job_heartbeat 주기 호출, signal 핸들러, finally  
   - submit 시 aws_batch_job_id 저장: 기존 구현 확인됨 (video_encoding.py L49-50)  
   - reconcile 커맨드: `reconcile_batch_video_jobs` 신규 작성됨  

2. **배포**  
   - 워커 이미지 빌드/푸시 후 Job Definition revision 업데이트 또는 Queue가 사용할 revision 지정  

3. **인프라**  
   - Compute Environment: On-Demand, minvCpus=0, desiredvCpus=0, maxvCpus=16(또는 32)  
   - cron/스케줄: `scan_stuck_video_jobs` (기존), `reconcile_batch_video_jobs` (1~2분마다 권장)  

4. **테스트**  
   - 정상 완료 5회  
   - terminate-job 5회  
   - OOM 3회, 인스턴스 terminate 3회, 네트워크 장애 3회(가능하면)  

5. **문서**  
   - 이 체크리스트 및 테스트 결과 기록 유지  

---

## 8) 참고 파일

| 용도 | 경로 |
|------|------|
| 워커 엔트리 (RUNNING/heartbeat/SIGTERM) | `apps/worker/video_worker/batch_main.py` |
| Reconcile 커맨드 | `apps/support/video/management/commands/reconcile_batch_video_jobs.py` |
| submit + aws_batch_job_id 저장 | `apps/support/video/services/video_encoding.py` |
| Stuck 스캐너 | `apps/support/video/management/commands/scan_stuck_video_jobs.py` |
| job_set_running / job_heartbeat 정의 | `academy/adapters/db/django/repositories_video.py` |
