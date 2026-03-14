# AI/메시징 워커 복구 조치 (2026-03-07)

## 원인 요약

1. **EC2 역할에 SSM GetParameter 권한 없음**  
   워커 LT UserData에서 `aws ssm get-parameter --name /academy/workers/env` 로 환경변수를 가져오는데, `academy-ec2-role`에 `ssm:GetParameter` 권한이 없어 부팅 시 env 조회 실패 → `/opt/workers.env` 미생성 → 컨테이너가 SOLAPI/SQS 등 env 없이 기동되어 실패하거나 동작하지 않음.

2. **기존 인스턴스가 구 LT 사용**  
   IamInstanceProfile·UserData가 반영된 LT로 교체된 뒤에도, 이미 떠 있던 인스턴스는 예전 LT로 띄워져 있어 UserData·IAM이 반영되지 않음. instance-refresh로 새 인스턴스로 교체해야 함.

---

## 적용한 수정

### 1. EC2 역할에 SSM GetParameter 정책 추가

- **파일:** `scripts/v1/templates/iam/policy_ec2_ssm_get_parameters.json`  
  - `ssm:GetParameter`, `ssm:GetParameters`  
  - Resource: `arn:aws:ssm:ap-northeast-2:809466760795:parameter/academy/*`
- **적용:** `scripts/v1/resources/iam.ps1` 의 `Ensure-EC2InstanceProfileSSM` 에서 위 정책을 인라인 정책 `academy-ec2-ssm-get-parameters` 로 붙이도록 추가.

### 2. 워커 instance-refresh 스크립트 추가

- **파일:** `scripts/v1/restart-workers.ps1`
- **역할:** Messaging/AI 워커 ASG에 대해 instance-refresh 실행.
- **옵션:** `-UpdateSsm` 시 먼저 `update-workers-env-sqs.ps1` 로 SSM `/academy/workers/env` 갱신 후 instance-refresh 실행.

---

## 실행 순서 (에이전트/운영자)

1. **IAM 반영 (EC2 역할에 SSM 정책 붙이기)**  
   ```powershell
   cd C:\academy
   pwsh scripts/v1/deploy.ps1 -AwsProfile default
   ```  
   → 배포 흐름에서 `Ensure-EC2InstanceProfileSSM` 이 호출되며 `academy-ec2-ssm-get-parameters` 인라인 정책이 적용됨.

2. **SSM 갱신 + 워커 instance-refresh**  
   ```powershell
   pwsh scripts/v1/restart-workers.ps1 -AwsProfile default -UpdateSsm
   ```  
   - `-UpdateSsm`: `/academy/workers/env` 에 SQS 큐 이름·SOLAPI_* 등 갱신  
   - Messaging ASG, AI ASG 각각 `start-instance-refresh` 실행  
   - scale-in protection 때문에 완료까지 약 10~15분 걸릴 수 있음.

3. **완료 후 확인**  
   - AWS Console > EC2 > Auto Scaling Groups > `academy-v1-messaging-worker-asg`, `academy-v1-ai-worker-asg` > Instance refresh 상태  
   - 새 인스턴스에 접속 시 `/var/log/academy-worker-userdata.log` 에서 UserData·env 로드 성공 여부 확인  
   - SQS: `academy-v1-messaging-queue`, `academy-v1-ai-queue` 메시지 소비 여부 확인

---

## 요약

| 항목 | 내용 |
|------|------|
| 원인 | EC2 역할에 SSM GetParameter 없음 + 구 인스턴스가 UserData/IAM 미반영 LT 사용 |
| 조치 | policy_ec2_ssm_get_parameters.json 추가, iam.ps1에서 해당 정책 부여, restart-workers.ps1 추가 |
| 다음 단계 | deploy.ps1 실행 후 `restart-workers.ps1 -UpdateSsm` 실행 |
