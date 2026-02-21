# Video Worker 엔터프라이즈급 리팩터 총정리

## 적용된 변경 사항

### 1. Lambda 반환값 패치 (완료)

**파일**: `infra/worker_asg/queue_depth_lambda/lambda_function.py`

- `set_video_worker_desired`가 디버깅용 dict 반환
- `lambda_handler` 반환값에 병합:
  - `video_visible`, `video_inflight`, `video_backlog_add`, `video_desired_raw`, `video_new_desired`, `video_decision`, `stable_zero_since_epoch`, `video_scale_visible_only`

### 2. Worker 빠른 ACK + DB lease (완료)

**환경변수**: `VIDEO_FAST_ACK=1` 시 활성화 (기본 0)

| 구성요소 | 변경 |
|----------|------|
| `repositories_video.py` | `try_claim_video(video_id, worker_id, lease_seconds)` 추가. UPLOADED→PROCESSING 원자 변경 + leased_by, leased_until |
| `IVideoRepository` | `try_claim_video` 메서드 추가 (기본 구현: mark_processing 호출) |
| `handler.py` | `_worker_id` 있을 때 try_claim 사용, `"skip:claim"` 반환 추가 |
| `sqs_main.py` | VIDEO_FAST_ACK 시 receive 직후 delete, visibility extender 미실행, 결과별 delete/visibility 조건부 스킵 |

### 3. Lambda visible-only 스케일 (선택)

**환경변수**: `VIDEO_SCALE_VISIBLE_ONLY=1` 시 `desired_candidate = backlog_add` (inflight 제외)

---

## 배포 순서

1. **Lambda** 배포 → 반환값 확인
2. **Worker** 배포 (VIDEO_FAST_ACK=0 유지) → 기존 동작 유지
3. Worker에 `VIDEO_FAST_ACK=1` 설정 후 rolling update → inflight 감소 모니터링
4. (선택) Lambda에 `VIDEO_SCALE_VISIBLE_ONLY=1` 설정 → 스케일 수식 전환

---

## Handler 반환값 정리

| 반환값 | 조건 | fast_ack=0 | fast_ack=1 |
|--------|------|------------|------------|
| ok | 처리 성공 | delete | (이미 delete) |
| skip:cancel | 취소 요청 | delete | (이미 delete) |
| skip:claim | try_claim 실패 | - | (이미 delete) |
| skip:mark_processing | mark_processing 실패 | NACK | - |
| lock_fail | Redis 락 실패 | NACK | - |
| failed | 처리 실패 | NACK(backoff) | (이미 delete, DB FAILED) |

---

## 관련 문서

- [VIDEO_ENTERPRISE_REFACTOR_PLAN.md](./VIDEO_ENTERPRISE_REFACTOR_PLAN.md) - 상세 설계 및 패치 위치
