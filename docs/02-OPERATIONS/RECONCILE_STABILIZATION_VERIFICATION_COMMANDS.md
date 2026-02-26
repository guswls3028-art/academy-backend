# Reconcile 안정화 — 운영 검증 체크

Video Batch Reconcile 폭주/오판 안정화 배포 후, 아래 커맨드로 정상 동작을 검증한다.

---

## 1. RUNNING reconcile이 동시에 1개 이하인지 확인

**의도:** EventBridge rate(5분) + Redis 단일 락으로 reconcile이 한 번에 하나만 실행되어야 한다. 동시에 2개 이상 RUNNING이면 폭주 가능성이 있음.

**PowerShell (저장소 루트):**

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$Region = "ap-northeast-2"   # 필요 시 변경
$OpsQueue = "academy-video-ops-queue"

$arn = (aws batch describe-job-queues --job-queues $OpsQueue --region $Region --query "jobQueues[0].jobQueueArn" --output text)
$running = (aws batch list-jobs --job-queue $arn --job-status RUNNING --region $Region --output json) | ConvertFrom-Json
$reconcileCount = ($running.jobSummaryList | Where-Object { $_.jobName -match "reconcile" }).Count
if ($reconcileCount -le 1) { Write-Host "PASS: RUNNING reconcile count = $reconcileCount" -ForegroundColor Green } else { Write-Host "FAIL: RUNNING reconcile count = $reconcileCount (expected <=1)" -ForegroundColor Red }
```

**원테이크 감사로 확인:**

```powershell
.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2
# "Ops Queue RUNNING" 행에서 reconcile=0 또는 reconcile=1 이면 PASS. reconcile=2 이상이면 WARN.
```

---

## 2. Reconcile 로그에서 AccessDenied 제거 확인

**의도:** reconcile job이 `batch:DescribeJobs` / `batch:ListJobs` 권한으로 실행되므로, CloudWatch 로그에 AccessDenied가 나오면 안 된다. IAM 정책 `AcademyAllowBatchDescribeJobs` 부착 후에는 해당 오류가 사라져야 한다.

**확인 방법:**

- **CloudWatch Logs:** `/aws/batch/academy-video-worker` (또는 reconcile job 정의의 logConfiguration 로그 그룹)에서 로그 스트림 이름에 `reconcile` 포함된 스트림을 연다.
- **검색:** 최근 1시간 이내 로그에서 `AccessDenied` 또는 `DescribeJobs` 관련 오류가 없는지 확인.

**CLI로 최근 reconcile 로그 스트림 검색 (예시):**

```powershell
$Region = "ap-northeast-2"
$LogGroup = "/aws/batch/academy-video-worker"
aws logs filter-log-events --log-group-name $LogGroup --filter-pattern "AccessDenied" --start-time ([long]((Get-Date).AddHours(-1).ToUniversalTime() - (Get-Date "1970-01-01")).TotalMilliseconds) --region $Region --query "events[*].message" --output text
# 결과가 비어 있으면 PASS.
```

---

## 3. Video job이 SUCCEEDED/READY로 정상 전이 확인

**의도:** 인코딩 완료된 비디오는 worker의 `job_complete()`에 의해 SUCCEEDED → READY로 전이된다. Reconcile은 READY를 만들지 않으며, SUCCEEDED 상태를 덮어쓰지 않는다.

**Django 관리 명령 (API 서버 또는 worker 설정으로 로컬):**

```bash
# DB에서 READY 비디오 수 / 최근 완료 job 수 확인
python manage.py shell -c "
from apps.support.video.models import VideoTranscodeJob, Video
from django.utils import timezone
from datetime import timedelta
ready = Video.objects.filter(transcode_status=Video.TranscodeStatus.READY).count()
recent = VideoTranscodeJob.objects.filter(state=VideoTranscodeJob.State.SUCCEEDED, updated_at__gte=timezone.now()-timedelta(hours=24)).count()
print('READY videos:', ready, '| SUCCEEDED jobs (24h):', recent)
"
```

**정성적 확인:** 대기 중이던 인코딩이 완료된 뒤 해당 비디오의 `transcode_status`가 READY로 바뀌었는지 API/Admin에서 확인.

---

## 4. Ops CE가 idle 시 scale down 되는지 확인

**의도:** Ops CE는 `minvCpus=0`, `maxvCpus=2`로 설정되어 있어, reconcile/scan_stuck job이 없으면 인스턴스가 0으로 줄어들어야 한다.

**PowerShell:**

```powershell
$Region = "ap-northeast-2"
$ceName = "academy-video-ops-ce"
$ce = (aws batch describe-compute-environments --compute-environments $ceName --region $Region --output json) | ConvertFrom-Json
$cr = $ce.computeEnvironments[0].computeResources
Write-Host "desiredvCpus=$($cr.desiredvCpus) minvCpus=$($cr.minvCpus) maxvCpus=$($cr.maxvCpus)"
# Ops 큐에 RUNNING/RUNNABLE job이 없을 때 desiredvCpus가 0이면 PASS. (일시적 지연 가능)
```

**원테이크 감사:**  
`.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2` 에서 "Ops CE min/max vCpus" 항목이 min=0 max=2로 나오면 스펙은 맞음. 실제 scale down은 job이 없을 때 시간이 지나면 반영된다.

---

## 요약 표

| 검증 항목 | 방법 | 기대 |
|-----------|------|------|
| RUNNING reconcile ≤1 | `infra_one_take_full_audit.ps1` 또는 list-jobs | reconcile=0 또는 1 |
| AccessDenied 없음 | CloudWatch 로그 검색 / filter-log-events | 최근 reconcile 로그에 AccessDenied 없음 |
| SUCCEEDED→READY 전이 | DB/API에서 READY 비디오 및 최근 SUCCEEDED job 수 확인 | 완료된 영상이 READY로 전이 |
| Ops CE scale down | describe-compute-environments desiredvCpus | idle 시 desiredvCpus=0 (min=0 유지) |
