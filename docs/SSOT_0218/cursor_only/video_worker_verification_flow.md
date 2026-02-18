# Video Worker 배포 후 확인 순서

Instance Refresh 후, 새 이미지로 워커가 동작하는지 확인하는 순서입니다.

---

## (0) 로컬 PowerShell — Instance Refresh 완료 여부 확인

Refresh가 끝나야 새 인스턴스가 새 이미지를 쓰고 있습니다.

```powershell
aws autoscaling describe-instance-refreshes --auto-scaling-group-name academy-video-worker-asg --region ap-northeast-2 --query "InstanceRefreshes[0].{Status:Status,StartTime:StartTime,EndTime:EndTime}" --output table
```

`Status`가 **Successful** 이면 다음 단계로 진행하면 됩니다. `InProgress` 이면 완료될 때까지 기다립니다.

---

## (1) 로컬 PowerShell — Video 워커 인스턴스 IP 확인

ASG 인스턴스는 IP가 바뀔 수 있으므로, 확인 후 SSH에 사용합니다.

```powershell
aws ec2 describe-instances --region ap-northeast-2 --filters "Name=tag:Name,Values=academy-video-worker" "Name=instance-state-name,Values=running" --query "Reservations[].Instances[].[PublicIpAddress,PrivateIpAddress]" --output table
```

PublicIpAddress가 있으면 그 IP로 SSH, 없으면 PrivateIpAddress는 Bastion 등 내부에서만 접근 가능합니다. 아래 `VIDEO_WORKER_IP` 에 넣어 사용합니다.

---

## (2) API 서버에서 실행

API 서버에 SSH 접속한 뒤, 아래만 실행하면 됩니다.

```bash
# API 서버 SSH (로컬 PowerShell에서, KeyDir=C:\key 기준)
# ssh -i C:\key\backend-api-key.pem ec2-user@15.165.147.157

# 접속 후: 인코딩 트리거는 프론트/API에서 영상 업로드 후 자동이거나, Django shell로 큐에 넣을 수 있음
# Django shell 예시 (프로젝트 루트에서)
cd /home/ec2-user  # 또는 API 앱 경로
source .venv/bin/activate  # 가상환경 있으면
python manage.py shell -c "
from academy.models import Video
from apps.support.video.services.sqs_queue import send_video_encode_message
v = Video.objects.filter(tenant_id=YOUR_TENANT_ID).order_by('-id').first()
if v: send_video_encode_message(v.tenant_id, v.id, v.file_key); print('Queued video_id=', v.id)
"
```

- `YOUR_TENANT_ID` 는 실제 tenant_id 로 바꿉니다.
- 이미 프론트에서 업로드/인코딩 요청했다면 이 단계는 생략해도 됩니다.

---

## (3) Video 워커에서 실행

Video 워커 인스턴스에 SSH 접속한 뒤, 로그로 동작을 확인합니다.

```bash
# Video 워커 SSH (로컬 PowerShell에서)
# ssh -i C:\key\video-worker-key.pem ec2-user@VIDEO_WORKER_IP

# 접속 후: 워커 로그 스트리밍 (timeout 21600, ChangeMessageVisibility, 360p/720p 등 확인)
docker logs -f academy-video-worker
```

- `VIDEO_WORKER_IP` 는 (1)에서 확인한 IP로 바꿉니다.
- 로그에서 다음을 보면 새 설정으로 동작하는 것입니다.
  - `[TRANSCODER] Starting ffmpeg` 등 인코딩 시작
  - 진행률 업데이트 (50%에서 멈추지 않음)
  - SQS visibility 연장(21600) 관련 동작
  - 360p/720p 관련 로그

---

## 요약

| 단계 | 어디서 | 할 일 |
|------|--------|--------|
| 0 | 로컬 PowerShell | Instance Refresh `Status` == Successful 확인 |
| 1 | 로컬 PowerShell | Video 워커 인스턴스 IP 확인 |
| 2 | API 서버 (SSH 후) | (선택) Django shell로 인코딩 큐에 넣기 |
| 3 | Video 워커 (SSH 후) | `docker logs -f academy-video-worker` 로 동작/로그 검증 |
