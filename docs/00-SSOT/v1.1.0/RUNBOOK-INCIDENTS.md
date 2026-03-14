# 장애 대응 런북

**Version:** V1.1.0 | **최종 수정:** 2026-03-15

> 모든 AWS 명령은 `scripts/v1/run-with-env.ps1 --` 접두사를 사용한다.
> 아래에서 `RUN_ENV`는 이 접두사의 줄임말이다:
> ```
> powershell -File scripts/v1/run-with-env.ps1 --
> ```

---

## 1. API 500 급증

### 증상
- 사용자가 "서버 오류" 화면을 보고 보고
- `/health` 또는 `/healthz` 실패
- CloudWatch에서 5xx 급증

### 즉시 확인 (30초)
```bash
# 헬스체크
curl -s https://api.1academy.co.kr/healthz
curl -s https://api.1academy.co.kr/health

# 최근 배포 확인
gh run list -w "v1-build-and-push-latest.yml" -L 3
```

### 즉시 조치 (5분)

**경우 A: /healthz 실패 (앱 자체 다운)**
```bash
# ASG 인스턴스 상태 확인
RUN_ENV aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-api-asg \
  --query "AutoScalingGroups[0].Instances[*].{Id:InstanceId,Health:HealthStatus,State:LifecycleState}" \
  --output table

# 인스턴스가 Unhealthy → ASG가 자동 교체함. 기다린다.
# 인스턴스가 없음 → ASG min 확인
RUN_ENV aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-api-asg \
  --query "AutoScalingGroups[0].{Min:MinSize,Desired:DesiredCapacity}" \
  --output table
```

**경우 B: /healthz 200, /health 실패 (DB 연결 문제)**
→ **§ 5. DB 장애** 참조

**경우 C: 최근 배포 직후 발생**
→ **§ 6. 배포 실패/롤백** 참조 (이미지 롤백)

### 복구 확인
```bash
curl -s https://api.1academy.co.kr/healthz   # 200
curl -s https://api.1academy.co.kr/health     # 200
```

### 에스컬레이션 기준
- 5분 내 자동 복구 안 되면 롤백 실행
- 롤백 후에도 복구 안 되면 AWS Support 케이스 오픈

---

## 2. 메시징 중복/미발송

### 증상
- 학부모가 같은 문자를 여러 번 수신
- 발송 예정 메시지가 미발송

### 즉시 확인 (30초)
```bash
# SQS 큐 깊이 확인
RUN_ENV aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/{ACCOUNT}/academy-messaging-queue \
  --attribute-names ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible \
  --output table

# DLQ 확인
RUN_ENV aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/{ACCOUNT}/academy-dlq \
  --attribute-names ApproximateNumberOfMessages \
  --output text

# 워커 인스턴스 확인
RUN_ENV aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-messaging-worker-asg \
  --query "AutoScalingGroups[0].Instances[*].{Id:InstanceId,Health:HealthStatus,State:LifecycleState}" \
  --output table
```

### 즉시 조치 (5분)

**미발송 (큐에 메시지 쌓임):**
```bash
# 워커 살아있는지 확인 — 인스턴스 0개면:
RUN_ENV aws autoscaling set-desired-capacity \
  --auto-scaling-group-name academy-v1-messaging-worker-asg \
  --desired-capacity 1
```

**중복 발송:**
- SQS visibility timeout이 처리 시간보다 짧으면 중복 발생 가능
- 워커 로그에서 동일 message_id 처리 여부 확인
- 근본 원인 파악 전까지 **워커를 중지하지 말 것** (미발송이 중복보다 위험)

**DLQ에 메시지 있음:**
- DLQ 메시지 내용 확인 후 원인 파악
- 단순 일시 오류면 DLQ에서 원래 큐로 재전송 (AWS 콘솔 > SQS > DLQ redrive)

### 복구 확인
```bash
# 큐 깊이 0으로 수렴 확인
RUN_ENV aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/{ACCOUNT}/academy-messaging-queue \
  --attribute-names ApproximateNumberOfMessages --output text
```

### 에스컬레이션 기준
- DLQ 메시지 10개 이상 누적
- 30분 이상 큐 깊이 감소하지 않음

---

## 3. 영상 인코딩 실패

### 증상
- 관리자가 업로드한 영상이 "처리 중" 상태에서 멈춤
- 영상 상태가 FAILED

### 즉시 확인 (30초)
```bash
# Django 관리 명령으로 stuck 영상 확인
RUN_ENV aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --parameters 'commands=["docker exec academy-api python manage.py scan_stuck_video_jobs"]' \
  --output text --query "Command.CommandId"

# 위 명령 결과 확인 (CommandId 사용)
RUN_ENV aws ssm get-command-invocation \
  --command-id {COMMAND_ID} \
  --instance-id {INSTANCE_ID} \
  --query "StandardOutputContent" --output text
```

### 즉시 조치 (5분)

**PENDING 상태에서 멈춘 영상 (stuck):**
```bash
# stuck 영상 복구 (PENDING → NEW로 리셋)
RUN_ENV aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --parameters 'commands=["docker exec academy-api python manage.py recover_stuck_videos"]' \
  --output text
```

**FAILED 상태 영상 재시도:**
```bash
# 실패 영상 재인큐
RUN_ENV aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --parameters 'commands=["docker exec academy-api python manage.py enqueue_uploaded_videos --include-failed"]' \
  --output text
```

**비디오 데몬 워커 자체가 죽은 경우:**
- 비디오 워커는 전용 인스턴스에서 동작 (ASG 아님, 별도 관리)
- SSH 접속하여 docker 컨테이너 상태 확인

### 복구 확인
- 관리자 페이지에서 영상 상태가 COMPLETED로 변경 확인
- 학생 앱에서 영상 재생 가능 확인

### 에스컬레이션 기준
- 같은 영상이 3회 이상 실패
- 30분 이상 경과 후에도 PENDING 상태

---

## 4. 비용 급증

### 증상
- AWS Billing 알림 수신
- 예상 비용 대비 비정상적 증가

### 즉시 확인 (30초)
```bash
# EC2 실행 중인 인스턴스 수 확인
RUN_ENV aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" "Name=tag-key,Values=Name" \
  --query "Reservations[*].Instances[*].{Name:Tags[?Key=='Name']|[0].Value,Type:InstanceType,State:State.Name}" \
  --output table

# ASG desired capacity 확인
RUN_ENV aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-api-asg academy-v1-messaging-worker-asg academy-v1-ai-worker-asg \
  --query "AutoScalingGroups[*].{Name:AutoScalingGroupName,Min:MinSize,Desired:DesiredCapacity,Max:MaxSize,Running:length(Instances)}" \
  --output table
```

### 즉시 조치 (5분)

**ASG가 max까지 스케일아웃된 경우:**
```bash
# 원인 파악: 최근 스케일링 활동 확인
RUN_ENV aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name academy-v1-api-asg \
  --max-items 5 --output table
```
- CPU/메모리 부하 원인 파악 후 해결
- 의도하지 않은 스케일아웃이면 desired를 min으로 재설정

**Batch 작업이 과다 실행:**
```bash
# 활성 Batch 작업 확인
RUN_ENV aws batch list-jobs --job-queue academy-video-batch-queue --job-status RUNNING --output table
```

**ECR 이미지 과다 누적:**
```bash
# 이미지 수 확인
RUN_ENV aws ecr describe-repositories \
  --query "repositories[*].{Name:repositoryName}" --output table

# 특정 repo 이미지 수
RUN_ENV aws ecr list-images --repository-name academy-api \
  --query "length(imageIds)" --output text
```
- lifecycle policy 적용 여부 확인 → 미적용이면 즉시 적용

### 복구 확인
- 인스턴스 수가 정상 수준으로 복귀
- 다음 날 billing 추이 확인

### 에스컬레이션 기준
- 일일 비용이 평소 2배 이상
- 원인 불명의 인스턴스 증가

---

## 5. DB 장애

### 증상
- `/healthz` 200이지만 `/health` 실패 (DB 연결 불가)
- API 응답에서 "database" 관련 에러
- 모든 데이터 조회 실패

### 즉시 확인 (30초)
```bash
# 헬스 엔드포인트로 DB 상태 확인
curl -s https://api.1academy.co.kr/health

# RDS 상태 확인
RUN_ENV aws rds describe-db-instances \
  --query "DBInstances[*].{Id:DBInstanceIdentifier,Status:DBInstanceStatus,Class:DBInstanceClass}" \
  --output table
```

### 즉시 조치 (5분)

**RDS 상태가 "available"이 아닌 경우:**
- `modifying` → 진행 중인 변경 완료 대기
- `backing-up` → 자동 백업 중. 일시적. 대기
- `storage-full` → RDS 스토리지 즉시 증설:
  ```bash
  RUN_ENV aws rds modify-db-instance \
    --db-instance-identifier {DB_INSTANCE_ID} \
    --allocated-storage {NEW_SIZE_GB} \
    --apply-immediately
  ```

**RDS 정상인데 연결 실패:**
- Security Group 규칙 변경 여부 확인
- API 인스턴스의 네트워크(VPC/서브넷) 확인
- 최근 인프라 변경이 있었는지 확인

**RDS 재시작 필요 시:**
```bash
RUN_ENV aws rds reboot-db-instance \
  --db-instance-identifier {DB_INSTANCE_ID}
```
> 주의: 재시작은 1-5분 다운타임 발생. 최후의 수단.

### 복구 확인
```bash
curl -s https://api.1academy.co.kr/health   # 200 + "database": "connected"
```

### 에스컬레이션 기준
- RDS 상태가 10분 이상 비정상
- storage-full이 반복 발생
- 데이터 손실 의심

---

## 6. 배포 실패/롤백

### 증상
- GitHub Actions 워크플로우 실패
- 배포 후 `/healthz` 또는 `/health` 실패
- 배포 후 기능 장애

### 즉시 확인 (30초)
```bash
# CI/CD 실행 상태
gh run list -w "v1-build-and-push-latest.yml" -L 3

# 실패한 run 로그
gh run view --log-failed

# 현재 헬스 확인
curl -s https://api.1academy.co.kr/healthz
curl -s https://api.1academy.co.kr/health
```

### 즉시 조치 (5분)

**경우 A: 빌드/푸시 실패 (이미지가 바뀌지 않음)**
- 코드 수정 후 다시 push. 기존 서비스에 영향 없음.

**경우 B: Migration 실패**
- 배포가 자동 중단됨 (deploy-api가 migration에 의존)
- migration 오류 수정 후 다시 push

**경우 C: 배포 후 서비스 장애 → 롤백 필요**

```bash
# 1단계: 마지막 정상 SHA 이미지 확인
RUN_ENV aws ecr describe-images --repository-name academy-api \
  --query 'sort_by(imageDetails,&imagePushedAt)[-5:].{Tags:imageTags,Pushed:imagePushedAt}' \
  --output table

# 2단계: 정상 SHA 이미지를 :latest로 재태깅
MANIFEST=$(RUN_ENV aws ecr batch-get-image \
  --repository-name academy-api \
  --image-ids imageTag=sha-XXXXXXXX \
  --query 'images[0].imageManifest' --output text)

RUN_ENV aws ecr put-image \
  --repository-name academy-api \
  --image-tag latest \
  --image-manifest "$MANIFEST"

# 3단계: ASG 인스턴스 새로고침 (롤백 배포)
RUN_ENV aws autoscaling start-instance-refresh \
  --auto-scaling-group-name academy-v1-api-asg \
  --preferences '{"MinHealthyPercentage":100,"InstanceWarmup":300}'
```

> **워커 롤백도 동일 패턴.** repository-name과 ASG 이름만 변경:
> - Messaging: `academy-messaging-worker` / `academy-v1-messaging-worker-asg` (Warmup=120)
> - AI: `academy-ai-worker-cpu` / `academy-v1-ai-worker-asg` (Warmup=120)

**Migration 롤백 (필요 시):**
```bash
RUN_ENV aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --parameters 'commands=["docker exec academy-api python manage.py migrate {APP_NAME} {이전_MIGRATION_번호}"]' \
  --output text
```

### 복구 확인
```bash
# ASG refresh 완료 대기 (5-10분)
RUN_ENV aws autoscaling describe-instance-refreshes \
  --auto-scaling-group-name academy-v1-api-asg \
  --query "InstanceRefreshes[0].{Status:Status,Progress:PercentageComplete}" \
  --output table

# 헬스 확인
curl -s https://api.1academy.co.kr/healthz   # 200
curl -s https://api.1academy.co.kr/health     # 200
```

### 에스컬레이션 기준
- 롤백 후에도 서비스 복구 안 됨
- ASG instance refresh가 실패 또는 10분 이상 진행 없음
