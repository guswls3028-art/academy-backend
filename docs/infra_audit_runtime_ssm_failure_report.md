# AI/Messaging Worker Runtime 검사 실패 보고서 (infra_one_take_full_audit)

**작성일:** 2025-02-25  
**검사 스크립트:** `scripts/infra/infra_one_take_full_audit.ps1`  
**증상:** AI Worker / Messaging Worker 항목에서 **Runtime: FAIL**, 메시지 `SSM send-command failed`

---

## 1. 요약

- **Video Worker** Runtime은 **PASS** (netprobe job SUCCEEDED → Batch 워커는 API/DB/Redis 연결 정상).
- **AI Worker**, **Messaging Worker** Runtime만 **FAIL**: EC2 인스턴스에 대해 **SSM Send Command**가 실패하고 있음.

즉, **애플리케이션 자체 장애가 아니라**, ASG 워커 EC2가 **SSM(Systems Manager) 세션/명령 대상으로 등록되지 않았거나, 명령 전달이 불가한 상태**로 보는 것이 타당합니다.

---

## 2. 원인 분석

`send-command`가 실패하는 경우 AWS 측에서 나올 수 있는 대표 원인은 아래와 같습니다.

| 원인 | 설명 |
|------|------|
| **TargetNotConnected / Instance not registered** | SSM Agent가 기동 중이 아니거나, 인스턴스가 SSM “관리형 인스턴스”로 등록되지 않음. |
| **IAM Instance Profile** | EC2에 붙은 역할에 **AmazonSSMManagedInstanceCore** (또는 동등한 SSM 관련 정책)이 없으면 에이전트가 SSM 서비스와 등록/통신할 수 없음. |
| **네트워크** | 인스턴스가 SSM 엔드포인트(ssm, ec2messages, ssmmessages)에 도달하지 못함. 퍼블릭 IP 없으면 VPC 엔드포인트 또는 NAT 필요. |
| **SSM Agent 미설치/비활성** | Amazon Linux 2023/2 기본 포함이지만, 사용자 AMI/설정에서 비활성화되었을 수 있음. |
| **Caller 권한** | 감사 스크립트를 실행하는 IAM 사용자/역할에 `ssm:SendCommand` 등 권한이 없으면 실패. (이 경우 동일 계정 내 다른 리소스는 정상이어도 Send Command만 실패.) |

현재 상황에서는:

- **같은 계정·리전**에서 **SSM get-parameter**는 성공하고,
- **Batch netprobe**도 성공하므로 Batch 역할·네트워크는 정상이며,
- **실패하는 것은 “ASG 워커 EC2에 대한 Send Command”만** 실패하는 형태입니다.

따라서 **가장 유력한 원인**은 다음 두 가지입니다.

1. **ASG 워커 EC2의 IAM Instance Profile에 SSM 관리형 인스턴스용 정책이 없음**  
   - 예: `AmazonSSMManagedInstanceCore` 미연결, 또는 동일 권한을 주는 커스텀 정책 없음.
2. **해당 EC2가 SSM “관리형 인스턴스”로 등록되지 않은 상태**  
   - 위 1번이 해결되어야 에이전트가 SSM에 등록되고, 그 후에만 Send Command가 가능함.

추가 가능성:

- **VPC/서브넷**에서 SSM용 퍼블릭 라우트 또는 VPC 엔드포인트(ssm, ec2messages, ssmmessages)가 없어서, 에이전트가 SSM 서비스에 연결하지 못하는 경우.

---

## 3. 조치 방안

### 3.1 EC2 Instance Profile에 SSM 정책 추가 (우선 권장)

- ASG에서 사용하는 **Launch Template**에 지정된 **IAM Instance Profile**을 확인.
- 해당 역할에 **관리형 인스턴스 코어** 정책을 부여:
  - **관리형 정책:** `AmazonSSMManagedInstanceCore` (추가로 `AmazonSSMPatchAssociation` 등은 필요 시만.)
- 적용 방법:
  - AWS 콘솔: IAM → 역할 → 해당 인스턴스 프로필 역할 → 권한 추가 → `AmazonSSMManagedInstanceCore` 연결.
  - CLI 예:
    ```bash
    aws iam attach-role-policy --role-name <인스턴스_프로필_역할명> --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
    ```
- **기존 인스턴스**는 프로필만 바꿔도 되지 않고, **역할에 정책을 붙이면** 기동 중인 인스턴스에도 적용됨.  
  단, SSM Agent가 이미 돌고 있어야 하며, 몇 분 내에 “관리형 인스턴스”로 등록될 수 있음.
- **새로 기동되는 인스턴스**는 Launch Template의 Instance Profile이 위 역할을 쓰도록 되어 있으면 동일 정책으로 동작.

### 3.2 SSM Agent 및 네트워크 확인

- **이미지:** Amazon Linux 2023 등이라면 SSM Agent 기본 포함. 다른 AMI를 쓴다면 Agent 설치 및 기동 여부 확인.
- **네트워크:**
  - 퍼블릭 서브넷(퍼블릭 IP 있음): 인터넷 경유로 SSM 엔드포인트 접근 가능한지 확인.
  - 프라이빗 서브넷: **VPC 엔드포인트** (interface) 생성 권장:
    - `com.amazonaws.ap-northeast-2.ssm`
    - `com.amazonaws.ap-northeast-2.ec2messages`
    - `com.amazonaws.ap-northeast-2.ssmmessages`
  - 또는 NAT Gateway 등으로 아웃바운드 인터넷이 되면 SSM 공용 엔드포인트 접근 가능.

### 3.3 콘솔에서 관리형 인스턴스 확인

- **Systems Manager → Fleet Manager (또는 Managed Instances)** 에서 해당 리전·계정의 EC2 목록 확인.
- AI/Messaging 워커용 인스턴스 ID(`i-0b250d2c35301de66`, `i-010be8808e3de26ea` 등)가 **나타나는지** 확인.
- 목록에 없으면: 위 3.1·3.2가 해결된 뒤 몇 분 기다리거나, 인스턴스 재부팅/재기동 후 다시 확인.

### 3.4 감사 스크립트에서 실제 오류 메시지 확인

- 스크립트를 수정해 **Send Command 실패 시 AWS CLI의 stderr/stdout**을 그대로 Failure 메시지에 넣도록 했음.
- 재실행 시 실패 메시지에 예를 들어 다음과 같은 문구가 나올 수 있음:
  - `TargetNotConnected`: 인스턴스가 SSM에 연결되지 않음 → 3.1, 3.2, 3.3 점검.
  - `InvalidInstanceId`: 인스턴스 ID 오타/다른 계정 등 → 인스턴스 ID·리전·계정 확인.
  - `AccessDenied`: 호출자에게 `ssm:SendCommand` 등 권한 없음 → 감사 실행 IAM에 SSM 권한 추가.

---

## 4. 검사 결과에 대한 해석

- **Runtime: FAIL**이 AI/Messaging에만 있고 Video는 PASS이므로:
  - **API/DB/Redis** 및 **Batch 기반 Video 워커** 동작은 정상으로 볼 수 있음.
  - 문제는 **ASG 워커 EC2에 대한 SSM 원격 명령 가능 여부**로 한정됨.
- 따라서 **애플리케이션 장애**라기보다 **인프라 설정(SSM 관리형 인스턴스 + IAM + 네트워크)** 미비로 인한 **Runtime 검사 실패**로 보는 것이 맞습니다.
- 위 조치 후 동일 스크립트를 다시 실행하면, Send Command가 성공할 경우 AI/Messaging Worker Runtime이 **OK**로 바뀌어야 합니다.

---

## 5. 참고

- **스크립트:** `scripts/infra/infra_one_take_full_audit.ps1`
- **실행 예:**  
  `.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2`  
  `.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -Verbose`
- **관련 리소스:**  
  - ASG: `academy-ai-worker-asg`, `academy-messaging-worker-asg`  
  - Launch Template: `academy-ai-worker-lt`, `academy-messaging-worker-lt`  
  - Instance Profile은 Launch Template 또는 ASG 설정에서 확인.
