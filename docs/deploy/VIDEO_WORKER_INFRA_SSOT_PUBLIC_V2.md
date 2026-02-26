# Video Worker 인프라 SSOT v2.0 — Public Model (고정)

**Private+NAT 모델 완전 폐기. Public Subnet + Internet Gateway 통일.**

- Region: ap-northeast-2
- VPC: vpc-0831a2484f9b114c2
- API Elastic IP: 15.165.147.157
- API_BASE_URL: http://15.165.147.157:8000

---

## Network

- **NAT Gateway 사용 금지**
- 모든 EC2 기반 리소스는 **Public Subnet**
- Subnet **MapPublicIpOnLaunch=true**
- RouteTable: **0.0.0.0/0 → igw-xxxx**
- Internet Gateway 연결 필수
- S3 Gateway Endpoint 강제 아님

---

## API Server

- Elastic IP: **15.165.147.157** 유지
- Public Subnet
- SG: inbound 80/443/8000, egress 0.0.0.0/0
- API_BASE_URL = http://15.165.147.157:8000

---

## Build Server

- Public Subnet
- Public IP 자동 할당 또는 Elastic IP
- SG egress 0.0.0.0/0
- STS/ECR 접근 성공 필수
- SSM 검증: `aws sts get-caller-identity` 성공, `curl https://sts.ap-northeast-2.amazonaws.com` 성공

---

## Video Batch

- CE: **academy-video-batch-ce-final**
- MANAGED/EC2, c6g.large only, min=0 max=32
- **Public Subnet only**, Public IP 할당 활성화
- VALID + ENABLED
- Queue: **academy-video-batch-queue**, CE 단일 연결
- JobDef: **academy-video-batch-jobdef** — vcpus=2, memory=3072, timeout=14400, retry=1, **immutable image tag (latest 금지)**

---

## Ops Batch

- CE: **academy-video-ops-ce** — default_arm64, min=0 max=2, Public Subnet, VALID+ENABLED
- Queue: **academy-video-ops-queue**, CE 단일 연결
- JobDefs: reconcile (900,1,2048,1), scanstuck (900,1,2048,1), netprobe (120,1,512,1)

---

## EventBridge

- **academy-reconcile-video-jobs** → rate(15 minutes) → Ops Queue
- **academy-video-scan-stuck-rate** → rate(5 minutes) → Ops Queue
- EnableSchedulers 옵션으로 ENABLE/DISABLE 제어

---

## 원테이크 스크립트

`scripts/infra/infra_full_alignment_public_one_take.ps1`

**실행 예시:**

```powershell
.\scripts\infra\infra_full_alignment_public_one_take.ps1 `
  -Region ap-northeast-2 `
  -VpcId vpc-0831a2484f9b114c2 `
  -EcrRepoUri "<acct>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:<gitsha>" `
  -FixMode `
  -EnableSchedulers
```

**성공 기준:** Netprobe SUCCEEDED, CE VALID, RUNNABLE backlog 없음, STS timeout 없음, FINAL RESULT: PASS.

---

## 금지

- Private+NAT 복귀
- latest 이미지 허용
- 부분 성공 PASS 처리
