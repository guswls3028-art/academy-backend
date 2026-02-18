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

**로컬 PowerShell — API 서버 SSH**

```powershell
ssh -i C:\key\backend-api-key.pem ec2-user@15.165.147.157
```

**API 서버 접속 후 — (선택) 인코딩 큐에 넣기**

프론트에서 이미 업로드했다면 생략. Django shell로 큐에 넣을 때만 아래 실행. `YOUR_TENANT_ID` 를 실제 숫자로 바꿉니다.

```bash
cd /home/ec2-user
source .venv/bin/activate
python manage.py shell -c "from academy.models import Video; from apps.support.video.services.sqs_queue import send_video_encode_message; v = Video.objects.filter(tenant_id=YOUR_TENANT_ID).order_by('-id').first(); send_video_encode_message(v.tenant_id, v.id, v.file_key) if v else None; print('Queued video_id=', v.id if v else 'none')"
```

---

## (3) Video 워커에서 실행

**로컬 PowerShell — Video 워커 SSH**

(1)에서 나온 PublicIpAddress 사용. 예: 43.202.4.141

```powershell
ssh -i C:\key\video-worker-key.pem ec2-user@43.202.4.141
```

**Video 워커 접속 후 — 로그로 동작 확인**

```bash
sudo docker logs -f academy-video-worker
```

로그에서 `[TRANSCODER] Starting ffmpeg`, 진행률 업데이트, 360p/720p 등 나오면 새 설정으로 동작 중입니다.

---

## 요약

| 단계 | 어디서 | 할 일 |
|------|--------|--------|
| 0 | 로컬 PowerShell | Instance Refresh `Status` == Successful 확인 |
| 1 | 로컬 PowerShell | Video 워커 인스턴스 IP 확인 |
| 2 | API 서버 (SSH 후) | (선택) Django shell로 인코딩 큐에 넣기 |
| 3 | Video 워커 (SSH 후) | `docker logs -f academy-video-worker` 로 동작/로그 검증 |
