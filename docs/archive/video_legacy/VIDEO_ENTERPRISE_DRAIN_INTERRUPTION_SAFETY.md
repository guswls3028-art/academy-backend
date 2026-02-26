# ENTERPRISE FINAL HARDENING — VIDEO WORKER DRAIN & INTERRUPTION SAFETY

목표: Spot Interruption / ASG Scale-in / Instance Refresh 시 **작업 손실 0%** (No Lost Work).

---

## 변경 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `apps/worker/video_worker/sqs_main.py` | spot_termination_event, Spot metadata poller, SIGTERM 시 interrupt 플래그, drain 시 job_fail_retry+visibility=0, receive wait=0, heartbeat 90s drain timeout |
| `apps/support/video/redis_status_cache.py` | VIDEO_ASG_INTERRUPT_KEY, set_asg_interrupt(), is_asg_interrupt() |
| `apps/support/video/views/internal_views.py` | VideoAsgInterruptStatusView (GET asg-interrupt-status) |
| `apps/api/v1/urls.py` | path internal/video/asg-interrupt-status/ |
| `infra/worker_asg/queue_depth_lambda/lambda_function.py` | _is_asg_interrupt_from_api(), interrupt 시 BacklogCount publish skip |

---

## Worker Drain Sequence Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Spot metadata 200 / SIGTERM                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ spot_termination_event.set()        │
│ _shutdown = True (SIGTERM만)        │
│ set_asg_interrupt()  Redis TTL=180  │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ Main loop 조건:                      │
│ while not (_shutdown or spot)       │
│ → 다음 iteration에서 루프 탈출        │
└─────────────────────────────────────┘
         │
         ├── [유휴 중] receive_message(wait=0) → 곧바로 break → 종료
         │
         └── [Job 처리 중] process_video 실행 중
                    │
                    ▼
         ┌─────────────────────────────────────┐
         │ Heartbeat thread (60초마다):          │
         │ spot_termination_event.is_set()      │
         │ → get_current() process/job_id 일치   │
         │ → process.terminate()               │
         │ → process.wait(90s) → timeout 시 kill│
         │ → cancel_event.set()                │
         └─────────────────────────────────────┘
                    │
                    ▼
         ┌─────────────────────────────────────┐
         │ process_video → CancelledError       │
         └─────────────────────────────────────┘
                    │
                    ▼
         ┌─────────────────────────────────────┐
         │ except CancelledError:               │
         │   if spot_termination_event:         │
         │     job_fail_retry("DRAIN_INTERRUPT")│
         │     change_message_visibility(0)      │
         │     (delete_message 호출 안 함)      │
         │   else: job_cancel + delete_message │
         └─────────────────────────────────────┘
                    │
                    ▼
         루프 다음 iteration → spot 설정으로 while 탈출 → 종료
```

---

## 완료 기준 체크

| 기준 | 구현 |
|------|------|
| A. Spot interruption 시 메시지 유실 없이 재처리 | job_fail_retry + visibility=0, delete_message 미호출 → 다른 워커 reclaim |
| B. ASG Scale-in 시 SIGTERM graceful drain | _handle_signal → spot_termination_event + set_asg_interrupt, 동일 drain 경로 |
| C. Instance Refresh 시 job cancel 후 visibility 복귀 | 동일 (SIGTERM → drain → visibility=0) |
| D. ffmpeg hang 시 drain timeout 후 kill | Heartbeat에서 terminate 후 wait(90s), TimeoutExpired 시 kill() |
| E. Shutdown 중 SQS long polling 중단 | wait_sec = 0 if (_shutdown or spot) else 20 |
| F. Interruption 중 scale-out runaway 방지 | Redis video:asg:interrupt=1 TTL=180, Lambda asg-interrupt-status 확인 후 BacklogCount skip |

---

## Lifecycle Hook 생성 CLI (ASG Instance Terminating)

Worker 인스턴스가 terminating 될 때 SSM으로 SIGTERM 전달하려면:

1. **SNS 토픽** (lifecycle hook용)
```bash
aws sns create-topic --name video-worker-lifecycle-terminating
```

2. **Lambda** (SNS 구독 → SSM SendCommand로 워커 프로세스에 SIGTERM)
   - Payload: LifecycleActionToken, InstanceId, LifecycleHookName, AutoScalingGroupName 등
   - Lambda에서 해당 InstanceId에 대해 `aws ssm send-command` 로 사용자 정의 스크립트 실행 (예: `kill -SIGTERM $(pgrep -f "video_worker.sqs_main")`)
   - 완료 후 `autoscaling complete-lifecycle-action` 호출

3. **Lifecycle Hook 등록**
```bash
aws autoscaling put-lifecycle-hook \
  --lifecycle-hook-name video-worker-drain \
  --auto-scaling-group-name academy-video-worker-asg \
  --lifecycle-transition autoscaling:EC2_INSTANCE_TERMINATING \
  --default-result CONTINUE \
  --heartbeat-timeout 300 \
  --notification-target-arn arn:aws:sns:ap-northeast-2:ACCOUNT:video-worker-lifecycle-terminating \
  --role-arn arn:aws:iam::ACCOUNT:role/VideoWorkerLifecycleRole
```

4. **Lambda에서 complete-lifecycle-action 호출**  
   Drain 완료(또는 timeout) 후 반드시 호출해야 인스턴스가 종료됨.

---

## Rollback 방법

1. **Worker 코드 롤백**  
   - spot_termination_event / Spot poller / drain 분기 제거 배포 시:  
     기존처럼 SIGTERM 시 _shutdown만 설정되고, 정상 완료한 job만 delete_message.  
     Spot/중단 시 메시지는 visibility timeout 후 재노출되나, job_fail_retry는 호출되지 않으므로 **해당 인스턴스에서 claim한 Job은 scan_stuck 또는 수동 정리 필요**.

2. **Lambda 롤백**  
   - `_is_asg_interrupt_from_api()` 호출 및 `skipped` 반환 분기 제거:  
     interrupt 중에도 BacklogCount 퍼블리시 (기존 동작).

3. **Redis 키**  
   - `video:asg:interrupt` 는 TTL=180 이므로 별도 삭제 불필요.

4. **Lifecycle Hook 제거**
```bash
aws autoscaling delete-lifecycle-hook \
  --lifecycle-hook-name video-worker-drain \
  --auto-scaling-group-name academy-video-worker-asg
```

---

## 테스트 시나리오

### [Spot Test]

1. Job RUNNING 상태로 ffmpeg 실행 중인 워커 준비.
2. **방법 A**: 해당 워커 프로세스에 `kill -SIGTERM <pid>` 전달.  
   **방법 B**: 동일 네트워크에서 metadata mock (예: 169.254.169.254 대신 테스트 서버에서 200 응답).
3. **기대**:
   - `job_fail_retry(job_id, "DRAIN_INTERRUPT")` 호출됨.
   - `delete_message` 호출되지 않음.
   - `change_message_visibility(receipt_handle, 0)` 호출됨.
   - 다른 워커가 메시지 reclaim 후 `job_claim_for_running` 성공.
   - Job `attempt_count` 증가.

### [Scale-in Test]

1. ASG desired 감소 또는 instance refresh로 인스턴스 terminate 유도.
2. Lifecycle hook 설정 시: SNS → Lambda → SSM SendCommand로 해당 인스턴스의 video-worker 프로세스에 SIGTERM 전달.
3. **기대**:
   - 워커가 drain 수행 (진행 중 job 있으면 job_fail_retry + visibility=0).
   - Lifecycle heartbeat timeout 내에 프로세스 종료 후 Lambda가 complete-lifecycle-action 호출.
   - 인스턴스 정상 종료.

### [Metric Skip Test]

1. 워커에서 drain 진입 (SIGTERM 또는 Spot 감지) → Redis `video:asg:interrupt=1` 설정.
2. queue_depth_lambda 실행 (EventBridge 1분).
3. **기대**: Lambda 로그에 `METRIC_PUBLISH_SKIPPED_DURING_INTERRUPT`, BacklogCount 미퍼블리시.
