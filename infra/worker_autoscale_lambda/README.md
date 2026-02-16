# Worker Autoscale Lambda (500 스케일)

워커 EC2 오토스케일링(기동 전용): SQS **Visible** 메시지 >= 1 이면 non-stopped 없을 때만 해당 타입 stopped 1대 Start.

- **500 설계**: `docs/SSOT_0215/IMPORTANT/WORKER_AUTOSCALING_500_PLAN.md`
- **배포**: `lambda_function.py`만 zip으로 압축 후 Lambda 콘솔 업로드. (boto3 기본 포함)

```powershell
Compress-Archive -Path infra/worker_autoscale_lambda/lambda_function.py -DestinationPath worker_autoscale_lambda.zip
```

**Lambda 설정**: Handler `lambda_function.lambda_handler`, Runtime Python 3.11, Timeout 60초, **Reserved Concurrency = 1**.  
**EventBridge**: `rate(1 minute)` → 이 Lambda.  
**IAM**: 실행 역할에 `infra/worker_autoscale_lambda/iam_policy_500.json` 부여 (SQS, EC2, SSM, Logs).  
**배포**: `.\scripts\deploy_worker_autoscale.ps1` (최초 1회는 역할 생성 후 `-RoleArn` 전달).
