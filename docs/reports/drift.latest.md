# Drift — SSOT vs actual
**Generated:** 2026-05-05T09:11:28.9107745+09:00

| ResourceType | Name | Expected | Actual | Action |
|--------------|------|----------|--------|--------|
| Batch CE | academy-v1-video-batch-ce-200gb | exists | exists | NoOp |
| Batch CE | academy-v1-video-batch-long-ce-200gb | exists | exists | NoOp |
| Batch CE | academy-v1-video-ops-ce | exists | exists | NoOp |
| Batch Queue | academy-v1-video-batch-queue | exists | exists | NoOp |
| Batch Queue | academy-v1-video-batch-long-queue | exists | exists | NoOp |
| Batch Queue | academy-v1-video-ops-queue | exists | exists | NoOp |
| EventBridge | academy-v1-reconcile-video-jobs | exists | exists | NoOp |
| EventBridge | academy-v1-video-scan-stuck-rate | exists | exists | NoOp |
| EventBridge | academy-v1-enqueue-uploaded-videos | exists | exists | NoOp |
| ASG | academy-v1-api-asg | Min=1 Max=2 Desired=1 | Min=1 Max=2 Desired=2 | Update |
| ASG | academy-v1-messaging-worker-asg | exists | exists | NoOp |
| ASG | academy-v1-ai-worker-asg | Min=1 Max=5 Desired=1 | Min=1 Max=5 Desired=5 | Update |
| API LT | academy-v1-api-lt | AMI/SG/Profile/UserData SSOT | drift | NewVersion |

