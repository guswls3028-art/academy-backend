# Lambda VPC 연결 — academy-worker-queue-depth-metric

**목표:** academy-worker-queue-depth-metric Lambda를 academy-api EC2와 동일 VPC에 연결.

**기준:** 코드/인프라 정의 + AWS CLI로 실제 리소스 조회. 추측 없음.

---

## 1️⃣ academy-api EC2의 VPC, Subnet, SecurityGroup 조회

### AWS CLI (실제 값 산출)

```bash
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=academy-api" "Name=instance-state-name,Values=running" \
  --region ap-northeast-2 \
  --query "Reservations[].Instances[].{VpcId:VpcId,SubnetId:SubnetId,SecurityGroups:SecurityGroups}" \
  --output json
```

- 인스턴스 1대 기준: `VpcId` 1개, `SubnetId` 1개, `SecurityGroups` 배열(각 요소에 `GroupId`, `GroupName`).
- **SubnetId:** Lambda에 넣을 서브넷(아래 2️⃣에서 Private 여부 확인 후 사용).
- **ApiSecurityGroupId:** API EC2에 붙은 SG의 `GroupId`(예: `sg-xxxxxxxx`). Lambda→API 8000 허용 시 이 SG의 Inbound에 Lambda SG 허용 필요.

### 스크립트에 하드코딩된 참고 값 (academy-api와 무관할 수 있음)

| 출처 | 값 | 비고 |
|------|-----|------|
| `scripts/redeploy_worker_asg.ps1` L11 | `subnet-049e711f41fdff71b` | Lambda VPC용 예시 서브넷 |
| `scripts/redeploy_worker_asg.ps1` L11 | `academy-api-sg` | Lambda용 SG **이름** (ID 아님) |
| `scripts/redeploy_worker_asg.ps1` L15-16 | `SubnetIds=subnet-07a8427d3306ce910`, `SecurityGroupId=sg-02692600fbf8e26f7` | **워커 ASG**용 기본값. academy-api와 다를 수 있음. |

→ **academy-api용으로 쓸 값은 반드시 위 describe-instances 결과로 확인.**

---

## 2️⃣ 해당 Subnet이 Private인지 확인

Lambda가 VPC 안에 있으면 아웃바운드(인터넷)는 NAT를 타야 하므로, 같은 VPC의 **Private 서브넷**을 쓰는 것이 일반적. API(172.30.3.142)는 같은 VPC 내이므로 Private 서브넷에서도 접근 가능.

```bash
aws ec2 describe-route-tables \
  --filters "Name=association.subnet-id,Values=<SUBNET_ID>" \
  --region ap-northeast-2 \
  --query "RouteTables[].Entries[?DestinationCidrBlock=='0.0.0.0/0']" \
  --output json
```

- `0.0.0.0/0`에 `GatewayId`가 `igw-xxxx` → **Public**.
- `0.0.0.0/0`에 `NatGatewayId`가 `nat-xxxx` → **Private**.

Lambda를 API와 같은 VPC에 두기만 하면 되므로, academy-api가 있는 **그 서브넷**을 쓰면 됨. (Public이어도 Lambda→API(사설 IP) 통신은 가능. 다만 Lambda가 인터넷/SQS 등 접근 시 NAT 필요하면 같은 VPC의 Private 서브넷을 쓰는 편이 안전.)

---

## 3️⃣ Lambda 연결용 SG

- **필수:** Lambda가 API EC2(172.30.3.142:8000)에 접근하려면, **API EC2에 붙은 SG(ApiSecurityGroupId) Inbound**에 “Lambda에서 오는 TCP 8000”을 허용해야 함.
  - 방법 A: Lambda에 **API와 동일한 SG**를 붙인다 → API SG Inbound에 **자기 자신(Self)** TCP 8000 허용.
  - 방법 B: **Lambda 전용 SG**를 새로 만들고, API SG Inbound에 “Lambda SG → TCP 8000” 허용.

Lambda `update-function-configuration`의 `SecurityGroupIds`에는 **SG ID(sg-xxx)** 만 넣을 수 있음. `academy-api-sg`는 이름이므로, 아래처럼 GroupId로 조회해 넣는다.

```bash
aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=academy-api-sg" \
  --region ap-northeast-2 \
  --query "SecurityGroups[].GroupId" \
  --output text
```

---

## 4️⃣ 출력

### [FACT] — 위 1️⃣ CLI 실행 후 채울 값

| 항목 | 값 (describe-instances 결과 기준) |
|------|-----------------------------------|
| **VpcId** | `Reservations[0].Instances[0].VpcId` |
| **SubnetId** | `Reservations[0].Instances[0].SubnetId` |
| **ApiSecurityGroupId** | `Reservations[0].Instances[0].SecurityGroups[0].GroupId` (API에 붙은 SG) |

(인스턴스가 여러 개면 해당하는 인스턴스 인덱스 사용.)

### [ACTION] — Lambda update-function-configuration

- **SubnetIds:** academy-api가 있는 서브넷 1개만 써도 됨. Lambda HA를 위해 같은 VPC의 서브넷 2개를 쓰려면, 위 VPC의 다른 Private 서브넷 1개를 추가로 조회해 넣는다.
- **SecurityGroupIds:**  
  - **옵션 A:** API와 동일 SG 1개 → `ApiSecurityGroupId` 1개. (API SG Inbound에 Self TCP 8000 허용 필요.)  
  - **옵션 B:** Lambda 전용 SG 1개 생성 후, 그 SG ID 1개. (API SG Inbound에 Lambda SG → TCP 8000 허용 필요.)

**AWS CLI 예시 (값 치환 후 실행):**

```bash
# 변수 설정 (1️⃣ 결과로 채움)
VPC_ID=vpc-xxxxxxxx
SUBNET_ID=subnet-xxxxxxxx
API_SG_ID=sg-xxxxxxxx

# Lambda 전용 SG 사용 시: 새 SG 생성 후 아래 LAMBDA_SG_ID 사용
# LAMBDA_SG_ID=sg-yyyyyyyy

# Lambda를 API와 같은 SG로 붙이는 경우 (API SG Inbound에 Self 8000 허용 선행)
aws lambda update-function-configuration \
  --function-name academy-worker-queue-depth-metric \
  --vpc-config SubnetIds=$SUBNET_ID,SecurityGroupIds=$API_SG_ID \
  --region ap-northeast-2
```

**서브넷 2개 사용 예:**

```bash
aws lambda update-function-configuration \
  --function-name academy-worker-queue-depth-metric \
  --vpc-config "SubnetIds=subnet-aaaaaaaa,subnet-bbbbbbbb,SecurityGroupIds=sg-xxxxxxxx" \
  --region ap-northeast-2
```

### API SG Inbound 규칙 확인/추가 (Lambda → TCP 8000)

- Lambda에 API와 **같은 SG**를 쓴 경우: API SG Inbound에 **Source = 자기 자신(Self)** 또는 `0.0.0.0/0`(비권장), Port **8000** 허용.
- Lambda **전용 SG**를 쓴 경우: API SG Inbound에 **Source = Lambda SG ID**, Port **8000** 허용.

```bash
# 예: API SG(sg-xxxxxxxx)에 Lambda SG(sg-lambda) → 8000 허용
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxx \
  --protocol tcp \
  --port 8000 \
  --source-group sg-lambda \
  --region ap-northeast-2
```

---

## 요약

1. **1️⃣** `describe-instances` (tag Name=academy-api)로 **VpcId, SubnetId, ApiSecurityGroupId** 확보.
2. **2️⃣** `describe-route-tables`로 해당 Subnet이 Public/Private 확인 (선택).
3. **3️⃣** API SG Inbound에 Lambda → TCP 8000 허용 (Self 또는 Lambda 전용 SG).
4. **4️⃣** `aws lambda update-function-configuration --vpc-config SubnetIds=...,SecurityGroupIds=...` 로 **SubnetIds**·**SecurityGroupIds** 적용.

이 환경에서는 AWS 자격 증명이 없어 CLI를 대신 실행하지 못함. 위 명령은 로컬/CI에서 실행 후 나온 실제 값으로 [FACT]와 [ACTION]을 채우면 됨.

---

## 출력 형식 (CLI 실행 후 채움)

### [FACT]

```
VpcId              = <1️⃣ describe-instances 결과 Reservations[0].Instances[0].VpcId>
SubnetId           = <1️⃣ Reservations[0].Instances[0].SubnetId>
ApiSecurityGroupId = <1️⃣ Reservations[0].Instances[0].SecurityGroups[0].GroupId>
```

### [ACTION]

**Lambda update-function-configuration 시:**

- **SubnetIds:** `SubnetId` 1개, 또는 동일 VPC의 Private 서브넷 2개(쉼표 구분).
- **SecurityGroupIds:** `ApiSecurityGroupId` 1개(API와 동일 SG 사용 시), 또는 Lambda 전용 SG의 GroupId 1개.

**실행 예:**

```bash
aws lambda update-function-configuration \
  --function-name academy-worker-queue-depth-metric \
  --vpc-config "SubnetIds=<SubnetId>,SecurityGroupIds=<ApiSecurityGroupId 또는 Lambda SG ID>" \
  --region ap-northeast-2
```
