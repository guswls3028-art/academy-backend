# EventBridge 규칙 상태 및 향후 조치 (기록)

인프라 reconcile 후 **EventBridge 규칙을 당분간 비활성화**해 둔 상태를 기록하고, 나중에 재활성화·삭제·업로드 인프라 등 다방면 검토 시 참고할 수 있도록 한다.

---

## 현재 상태 (기록일 기준)

| 규칙 이름 | 역할 | 상태 |
|-----------|------|------|
| `academy-reconcile-video-jobs` | 5분마다 reconcile job 제출 (Ops 큐) | **DISABLED** |
| `academy-video-scan-stuck-rate` | 5분마다 scan_stuck_video_jobs 제출 (Ops 큐) | **DISABLED** |
| `academy-worker-autoscale-rate` | 워커 오토스케일 관련 | **DISABLED** |
| `academy-worker-queue-depth-rate` | 큐 깊이 기반 스케줄/람다 등 | **DISABLED** |

- **Ops 큐 백로그 정리:** reconcile 직후 Ops 큐에 RUNNABLE 7개가 쌓여 있었음. 아래 명령으로 일괄 취소 후 RUNNABLE=0으로 정리함.
  ```powershell
  $Region = "ap-northeast-2"
  $OpsQ = "academy-video-ops-queue"
  $ids = aws batch list-jobs --job-queue $OpsQ --job-status RUNNABLE --region $Region --query "jobSummaryList[].jobId" --output text
  foreach ($id in $ids -split "\s+") {
    if ($id) { aws batch cancel-job --job-id $id --reason "clear ops backlog after infra reconcile" --region $Region }
  }
  ```
- **규칙 비활성화 명령 (참고):**
  ```powershell
  aws events disable-rule --name academy-video-scan-stuck-rate --region $Region
  aws events disable-rule --name academy-worker-queue-depth-rate --region $Region
  ```

---

## 향후 검토 사항 (재활성화·삭제·업로드 인프라 등)

다음은 **나중에** 인프라/운영 방향을 정할 때 고려하면 좋은 항목이다.

| 구분 | 내용 |
|------|------|
| **재활성화** | reconcile/scan_stuck 코드·설정 반영이 끝나고 운영을 다시 켤 때: `aws events enable-rule --name <RuleName> --region ap-northeast-2`. 순서는 `VIDEO_INFRA_ONE_TAKE_ORDER.md`의 EventBridge 섹션 참고. |
| **삭제** | 서비스 종료·리소스 정리 시: 규칙 삭제 전에 **타깃 제거** 필요. `remove-targets` 후 `delete-rule`. 람다/권한 등 연관 리소스도 함께 정리. |
| **업로드 인프라** | 영상 업로드 플로우(S3, API, 큐 등)와 EventBridge/스케줄이 어떻게 연동되는지 문서·설정 한곳에 정리해 두면, 재활성화/변경 시 혼동을 줄일 수 있음. |
| **인프라 설정 SSOT** | 배포·원테이크 순서는 `docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md`. 실제 상태는 `docs/deploy/actual_state/` JSON들. 규칙 on/off 상태는 이 문서에 추가 기록하거나, 필요 시 `actual_state`에 rule 상태 스냅샷을 남기는 방식 검토. |
| **감사/검증** | `.\scripts\infra\verify_eventbridge_wiring.ps1`, `.\scripts\infra\infra_one_take_full_audit.ps1` 로 규칙·타깃 점검. 재활성화 후 한 번씩 실행 권장. |

---

## 관련 문서·스크립트

- **배포 순서·역할 구분:** `docs/deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md`
- **EventBridge 규칙 on/off:** 동문서 “EventBridge 규칙 (스케줄 on/off)” 섹션
- **EventBridge 배포 스크립트:** `scripts/infra/eventbridge_deploy_video_scheduler.ps1`
- **프로덕션 정합 스크립트:** `scripts/infra/reconcile_video_batch_production.ps1`
