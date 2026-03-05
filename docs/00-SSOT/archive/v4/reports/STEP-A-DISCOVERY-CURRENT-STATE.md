# Step A: Discovery — 현재 인프라 상태 보고서

**목적:** Final Design v1.0 적용 전, 코드·SSOT 기준 현재 구성을 정리하고, AWS Describe로 확인할 항목 및 "출구 없음" 가설을 제시한다.  
**실행:** 코드 분석만으로 작성. AWS 실행이 필요한 부분은 명령·체크리스트로 안내.

---

## 1. 코드/SSOT 기준 현재 네트워크 구성

### 1.1 params.yaml (docs/00-SSOT/v4/params.yaml)

| 항목 | 현재 값 | 비고 |
|------|---------|------|
| **VPC** | vpc-0831a2484f9b114c2 | 고정 ID, 생성 로직 없음(Validate만) |
| **Public Subnets** | 4개 (subnet-049e711f41fdff71b, subnet-07a8427d3306ce910, subnet-09231ed7ecf59cfa4, subnet-0548571ac21b3bbf3) | network.publicSubnets 리스트 |
| **Private Subnets** | 없음 | params에 정의 없음 |
| **NAT Gateway** | 없음 | params·스크립트 모두 미사용 |
| **ALB / Target Group** | api.albName / api.targetGroupName 빈 문자열 | ALB 미사용 |
| **Security Groups** | network.securityGroups.batch = sg-011ed1d9eb4a65b8f, api/workers = ""(비어 있으면 batch와 동일 사용) | 단일 SG로 API/Build/ASG/Batch 공용 |

### 1.2 network.ps1 동작

- **Ensure-NetworkVpc:** describe-vpcs로 VPC 존재만 검증. **생성/수정 없음.**
- **Confirm-SubnetsMatchSSOT:** describe-subnets로 publicSubnets 4개 존재·개수 일치 검증, IGW 첨부 여부 확인. **라우팅 테이블·NAT·Private 서브넷 미검사.**

→ **결론:** 현재 코드는 “기존 VPC + 기존 Public 서브넷 4개”가 있다고 가정하고, **2-tier(Public/Private)·NAT·ALB·라우팅은 전혀 정의·Ensure 되지 않음.**

---

## 2. 리소스별 사용 Subnet / SG (코드 기준)

| 리소스 | Subnet 소스 | SG 소스 | 코드 위치 |
|--------|--------------|---------|-----------|
| **API ASG** | PublicSubnets (vpc-zone-identifier) | api.securityGroupId → batch SG | api.ps1, ssot |
| **Build EC2** | BuildSubnetId → PublicSubnets[0] | BuildSecurityGroupId → batch SG | build.ps1, ssot |
| **Messaging ASG** | PublicSubnets | (LT에 SG 없음 → ssot에서 Messaging 전용 SG 없음, asg_*.ps1은 AMI/InstanceType만 설정) | asg_messaging.ps1 | 
| **AI ASG** | PublicSubnets | 동일, LT에 SG 미지정 | asg_ai.ps1 |
| **Video Batch CE** | PublicSubnets (전체를 JSON 배열로 치환) | BatchSecurityGroupId | batch.ps1 New-VideoCE |
| **Ops Batch CE** | PublicSubnets | BatchSecurityGroupId | batch.ps1 New-OpsCE |

**참고:** asg_ai.ps1 / asg_messaging.ps1의 Launch Template 생성 시 SecurityGroupIds를 넣지 않음. EC2 기본 VPC default SG 또는 LT 기본 동작에 의존할 수 있음. params에는 workers 전용 SG가 비어 있어, 실제로는 api와 동일하게 batch SG를 쓰도록 ssot에서 채우는 부분이 없음(api만 ApiSecurityGroupId 폴백 처리).

→ **실제 배포 시:** API/Build는 명시적으로 sg-011ed1d9eb4a65b8f 사용. Batch CE도 동일 SG. Messaging/AI ASG LT는 **코드상 SG 미지정**이면 describe 시 빈 값 또는 계정 기본 동작에 따름.

---

## 3. Batch CE 상세 (템플릿 기준)

| CE | 템플릿 | minvCpus | maxvCpus | instanceTypes | subnets | securityGroupIds |
|----|--------|----------|----------|---------------|---------|------------------|
| Video | video_compute_env.json | 0 | 32 | ["c6g.large"] | PLACEHOLDER → script:PublicSubnets | PLACEHOLDER → BatchSecurityGroupId |
| Ops | ops_compute_env.json | 0 | 2 | ["c6g.large"] | 동일 | 동일 |

- **서브넷:** batch.ps1에서 `$script:PublicSubnets`를 JSON 배열 형태로 넣음. 즉 **현재는 4개 Public 서브넷 모두 사용.**
- **SG:** `$script:BatchSecurityGroupId` 1개 (sg-011ed1d9eb4a65b8f).

---

## 4. AWS Describe로 확인할 항목 (실행 시 사용)

코드만으로는 “실제 계정 상태”를 알 수 없으므로, 아래 명령으로 현재 상태를 채우면 됨.

### 4.1 VPC / 서브넷 / 라우팅

```bash
REGION=ap-northeast-2
VPC_ID=vpc-0831a2484f9b114c2

aws ec2 describe-vpcs --vpc-ids $VPC_ID --region $REGION --output json
aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --region $REGION --output json
aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$VPC_ID" --region $REGION --output json
aws ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$VPC_ID" --region $REGION --output json
aws ec2 describe-nat-gateways --filter "Name=vpc-id,Values=$VPC_ID" --region $REGION --output json
```

- **확인 목적:** Public/Private 구분 여부, 각 서브넷의 라우팅(0.0.0.0/0 → IGW vs NAT), NAT 존재 여부.

### 4.2 보안 그룹

```bash
aws ec2 describe-security-groups --group-ids sg-011ed1d9eb4a65b8f --region $REGION --output json
aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC_ID" --region $REGION --output json
```

- **확인 목적:** sg-011ed1d9eb4a65b8f의 Ingress/Egress 규칙(특히 Egress 0.0.0.0/0 허용 여부).

### 4.3 ALB / Target Group

```bash
aws elbv2 describe-load-balancers --region $REGION --output json
aws elbv2 describe-target-groups --region $REGION --output json
```

- **현재 코드:** ALB 미사용. 결과가 비어 있으면 설계와 일치.

### 4.4 Batch CE 실제 사용 서브넷/SG

```bash
aws batch describe-compute-environments --compute-environments academy-video-batch-ce-final academy-video-ops-ce --region $REGION --output json
```

- **추출 항목:** 각 CE의 `computeResources.subnets`, `computeResources.securityGroupIds`, `status`, `state`.

### 4.5 ASG 실제 사용 서브넷/SG

```bash
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-api-asg academy-messaging-worker-asg academy-ai-worker-asg --region $REGION --output json
```

- **추출 항목:** 각 ASG의 `VPCZoneIdentifier`(서브넷 목록), Launch Template에서 사용하는 SG(describe-launch-template-versions로 확인).

---

## 5. “출구 없음” 증상 — 네트워크 관점 가설 3개

**증상 정의:** Batch Job이 RUNNABLE에서 정체하거나, ECS 에이전트/이미지 풀·로그 전송이 되지 않는 등 “아웃바운드 통신 실패”로 보이는 상황.

### 가설 1: Public 서브넷의 0.0.0.0/0 라우팅 부재 또는 IGW 미연결

- **내용:** params에 적힌 4개 서브넷이 “Public”으로 가정되나, 실제 라우팅 테이블에 0.0.0.0/0이 IGW가 아닌 다른 대상(예: local만)으로 되어 있거나, 서브넷이 IGW가 붙은 라우트 테이블과 연결되지 않았을 수 있음.
- **검증:** `describe-route-tables`로 위 4개 서브넷과 연관된 라우트 테이블에서 0.0.0.0/0 → igw-xxx 존재 여부 확인.
- **조치:** Public 서브넷용 라우트 테이블에 0.0.0.0/0 → IGW 추가, 서브넷-라우트 테이블 연결 확인.

### 가설 2: Batch용 SG(sg-011ed1d9eb4a65b8f) Egress 제한

- **내용:** Batch CE가 사용하는 SG의 아웃바운드가 특정 포트(예: 443) 또는 특정 prefix만 허용하고, ECS/ECR/CloudWatch 등에 필요한 대역·포트가 막혀 있을 수 있음.
- **검증:** `describe-security-groups --group-ids sg-011ed1d9eb4a65b8f`로 Egress 규칙 확인. 0.0.0.0/0 All 또는 최소 443 허용 여부.
- **조치:** Batch CE 전용 SG는 아웃바운드 0.0.0.0/0 허용(또는 필요한 서비스 엔드포인트만 명시)하도록 수정.

### 가설 3: 서브넷이 실제로는 Private인데 NAT 없음

- **내용:** params에는 “publicSubnets”로만 나오지만, 실제 계정에서는 해당 서브넷이 Private 라우트 테이블(0.0.0.0/0 → nat-xxx)에 연결되어 있고, NAT Gateway가 없거나 비연결 상태일 수 있음. 그러면 Public으로 쓰려 해도 나가는 경로가 없음.
- **검증:** `describe-subnets`로 4개 서브넷의 MapPublicIpOnLaunch, `describe-route-tables`로 각 서브넷 연결 라우트 테이블의 0.0.0.0/0 대상 확인. `describe-nat-gateways`로 NAT 존재·Available 여부.
- **조치:** 2-tier 설계로 갈 경우 Private 서브넷용 NAT 1대 확보 후, 해당 라우트 테이블에 0.0.0.0/0 → NAT 연결. 또는 당장은 Batch/ASG를 “진짜” Public 서브넷(IGW 라우트 있음)으로 이전.

---

## 6. 요약 표 — 현재 vs Final Design v1.0 (참고)

| 항목 | 현재 (코드 기준) | Final Design v1.0 |
|------|-------------------|-------------------|
| 네트워크 | 1-tier, Public 4개, NAT 없음, VPC Endpoint 없음 | 2-tier, Public 2 + Private 2, NAT 1개, VPC Endpoint 미사용 |
| API | EIP + ASG(1/1/1), Public | ALB + ASG(1/2/1), Private, EIP 제거 |
| Workers | ASG 0/4/0, Public, SG 미지정 가능 | ASG 1/10/1, Private, scale-in protection, SQS 기반 스케일 |
| Batch Video CE | PublicSubnets, maxvCpus=32 | Private subnets, maxvCpus=10 |
| ALB | 없음 | 사용 |
| desired | SSOT 값으로 덮어씀 | min/max clamp, 덮어쓰기 금지 |

---

**다음 단계:** Step B에서 params.yaml을 Final Design v1.0에 맞게 재정의하고, Step C에서 2-tier 네트워크 Ensure를 구현한다.
