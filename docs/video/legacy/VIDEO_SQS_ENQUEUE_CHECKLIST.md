# Video SQS Enqueue 검증 체크리스트

영상 업로드 후 SQS에 메시지가 들어가지 않아 워커가 뜨지 않을 때 확인용.

## 흐름

```
upload_complete API → VideoSQSQueue().enqueue() → send_message() → SQS
                                                      ↓
Lambda(1분) → ApproximateNumberOfMessages → DesiredCapacity → ASG scale-out
```

**증상**: SQS Visible=0 → Lambda가 scale 안 함 → 워커 0대 유지

---

## 1. IAM 정책 적용 (API EC2 Role)

API 서버가 SQS에 SendMessage 하려면 `academy-ec2-role`에 권한 필요.

```powershell
cd C:\academy
.\scripts\apply_api_sqs_send_policy.ps1
```

또는 수동:

```powershell
$src = "C:\academy\infra\worker_asg\iam_policy_api_sqs_send.json"
$dst = "C:\academy\infra\worker_asg\iam_policy_api_sqs_send.min.json"
(Get-Content $src -Raw | ConvertFrom-Json | ConvertTo-Json -Depth 10 -Compress) | Out-File $dst -Encoding ascii
aws iam put-role-policy --role-name academy-ec2-role --policy-name SQSSendMessageVideoJobs --policy-document file://C:/academy/infra/worker_asg/iam_policy_api_sqs_send.min.json
```

확인:

```powershell
aws iam get-role-policy --role-name academy-ec2-role --policy-name SQSSendMessageVideoJobs
```

---

## 2. API 컨테이너 재시작 (필수)

IAM 변경 후 EC2 인스턴스 메타데이터 credential 캐시 갱신을 위해:

```bash
# API 서버 SSH 접속 후
sudo docker restart academy-api
```

---

## 3. 직접 SendMessage 테스트 (API 서버 내부)

```bash
docker exec -it academy-api python manage.py shell
```

```python
from libs.queue import get_queue_client
c = get_queue_client()
ok = c.send_message(queue_name="academy-video-jobs", message={"test": "video-scale-check"})
print("SEND_RESULT =", ok)
```

- `True` → 정상
- `False` → 로그에 `Failed to send message to academy-video-jobs:` 확인

---

## 4. SQS 메시지 수 확인 (로컬)

```powershell
aws sqs get-queue-attributes --queue-url https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-video-jobs --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region ap-northeast-2
```

업로드 직후 `ApproximateNumberOfMessages` ≥ 1 이어야 함.

---

## 5. API 로그 확인 (enqueue 실패 시)

```bash
docker logs academy-api 2>&1 | grep VIDEO_UPLOAD_TRACE
docker logs academy-api 2>&1 | grep "Failed to send message"
docker logs academy-api 2>&1 | grep SQS_QUEUE_URL_TRACE
```

- `execution=5_SEND_MESSAGE_DONE success=False` → send_message 실패
- `Failed to send message to academy-video-jobs: AccessDenied` → IAM 권한 부족

---

## 6. upload_complete 호출 여부

- 영상 업로드는 chunk 업로드 후 **upload/complete** API 호출이 있어야 enqueue 됨
- 503 "비디오 작업 큐 등록 실패(SQS)" → enqueue 실패 (권한/설정)
- 200 OK → enqueue 성공 (이때 SQS에 메시지 있어야 함)

---

## 7. 원테이크 검증 명령어

```powershell
# 1) IAM 적용
cd C:\academy
.\scripts\apply_api_sqs_send_policy.ps1

# 2) API 재시작 (SSH로 EC2 접속 후)
# sudo docker restart academy-api

# 3) 영상 1개 업로드 (관리자/학생앱)

# 4) SQS 확인 (업로드 직후)
aws sqs get-queue-attributes --queue-url https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-video-jobs --attribute-names ApproximateNumberOfMessages --region ap-northeast-2

# 5) Lambda 수동 실행 (선택)
aws lambda invoke --function-name academy-worker-queue-depth-metric --region ap-northeast-2 out.json

# 6) DesiredCapacity 확인 (5~10초 후)
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-video-worker-asg --region ap-northeast-2 --query "AutoScalingGroups[0].DesiredCapacity"
```

---

## 결론

| 항목 | 상태 |
|------|------|
| Lambda / ASG / ScalingPolicy | 정상 (Lambda 단독 제어) |
| QueueUrl | academy-video-jobs 일치 |
| send_message 시그니처 | message= (정상) |
| **API EC2 → SQS SendMessage** | **권한 필요** |

IAM 적용 + API 재시작 후 업로드 테스트.
