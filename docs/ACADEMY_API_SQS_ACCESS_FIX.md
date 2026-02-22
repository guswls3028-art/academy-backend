# academy-api EC2 SQS 접근 불가 해결

## 현상

- academy-api가 **private subnet** (`subnet-049e711f41fdff71b`)에 위치
- NAT Gateway 없음, 인스턴스에 public IP 없음
- `curl https://sqs.ap-northeast-2.amazonaws.com` 타임아웃
- `boto3 sqs.send_message()` 타임아웃 → SQS Visible/NotVisible 0 유지

## 원인

- Route table에 IGW 경로는 있으나, public IP가 없어 IGW를 사용할 수 없음
- Private subnet은 NAT Gateway 또는 VPC Endpoint 없이는 인터넷/AWS API 접근 불가

## 해결

### Option A (권장): VPC Interface Endpoint for SQS

- SQS만 PrivateLink로 접근
- NAT Gateway 불필요, 비용 낮음 (~$7.20/월 + 데이터 처리 비용)

```powershell
.\infra\worker_asg\fix_academy_api_sqs_access.ps1 -Option SqsEndpoint
```

### Option B: NAT Gateway

- 전체 인터넷 아웃바운드 허용
- 비용: ~$0.045/hr + 데이터 전송 (~$35/월 수준)

```powershell
.\infra\worker_asg\fix_academy_api_sqs_access.ps1 -Option NatGateway
```

**조건**: 같은 AZ에 public subnet이 있어야 함 (IGW 경로 보유)

## 검증

academy-api EC2에서:

```bash
curl -m 5 https://sqs.ap-northeast-2.amazonaws.com
```

- **정상**: HTTP 403 또는 405 (연결 성공, 인증/메서드 관련 응답)
- **비정상**: 타임아웃

## 참고

- SQS만 필요한 경우 Option A 권장
- EC2 패키지 업데이트, 외부 API 호출 등 다른 인터넷 트래픽도 필요하면 Option B
