# AWS 인증 — Cursor / 새 터미널에서 deploy 실행하기

**AI·Cursor 룰:** 본 문서를 포함한 리포지토리 내 **모든 문서·코드에 대해 AI(Cursor Agent)는 열람·수정 권한**이 있다. 배포·인증 설정 시 **.cursor/rules/** 내 해당 룰을 **적재적소에 항시 확인**한다.

Cursor에서 **Run Command**나 에이전트가 실행하는 명령은 **별도 셸**에서 돌아갑니다.  
그래서 수동으로 설정한 `$env:AWS_ACCESS_KEY_ID` 등은 **해당 터미널 세션에만** 있고, Cursor가 띄운 프로세스에는 전달되지 않습니다.

---

## 1. 원인 정리

| 원인 | 설명 |
|------|------|
| **세션 한정** | `$env:AWS_ACCESS_KEY_ID="..."` 는 현재 PowerShell 창에만 적용. Cursor가 새 프로세스로 실행하면 인증 없음. |
| **프로파일 미사용** | `deploy.ps1` 은 `--profile` 을 붙이지 않음. 기본적으로 **기본(default) 프로파일** 또는 **환경변수**만 사용. |
| **Region** | `params.yaml` 의 region 은 스크립트 내부용. `aws` CLI 기본 region 은 `AWS_DEFAULT_REGION` 또는 `aws configure get region`. |

---

## 2. 해결 방법 (Cursor에서 쓰려면)

### 방법 A: 기본 프로파일에 자격증명 저장 (권장)

**한 번만** 설정하면, Cursor 포함 **어떤 프로세스**에서든 `aws` 가 같은 자격증명을 씁니다.

```powershell
aws configure
# Access Key, Secret Key, region(ap-northeast-2) 입력
```

설정 후:

- `%USERPROFILE%\.aws\credentials` 에 [default] 저장
- Cursor에서 `pwsh scripts/v1/deploy.ps1 -Env prod` 만 실행하면 됨

### 방법 B: named 프로파일 + `-AwsProfile` (SSO 등)

이미 `aws configure --profile prod` 또는 SSO 프로파일을 쓰는 경우:

1. 해당 터미널에서 한 번 로그인:
   ```powershell
   aws sso login --profile prod
   ```
2. Cursor / 새 셸에서는 **프로파일 이름**만 넘기면 됨:
   ```powershell
   pwsh scripts/v1/deploy.ps1 -Env prod -AwsProfile prod
   ```
3. 검증도 같은 프로파일로:
   ```powershell
   pwsh scripts/v1/verify.ps1 -AwsProfile prod
   ```

`-AwsProfile` 이 있으면 스크립트가 `$env:AWS_PROFILE` 를 설정하고, 모든 `aws` 호출이 그 프로파일을 사용합니다.

### 방법 C: 사용자/시스템 환경변수 (자동화용)

환경변수로 쓰려면 **프로세스가 아니라 사용자/시스템**에 설정해야 Cursor가 띄운 새 프로세스에도 보입니다.

- **사용자 변수**: `setx AWS_ACCESS_KEY_ID "AKIA..."` (다음 로그인부터 적용)
- **시스템 변수**: 제어판 → 시스템 → 고급 → 환경 변수

보안상 자동화용 IAM 사용자만 쓰고, 권한은 최소화하는 것을 권장합니다.

---

## 3. 문제 진단

**Cursor가 쓰는 것과 같은 환경**에서 아래를 실행해 보세요.

```powershell
pwsh scripts/v1/aws-diagnose.ps1
```

출력 내용:

- `aws configure list` — credential source (env / profile 등)
- `aws configure list-profiles` — 프로파일 목록
- `AWS_ACCESS_KEY_ID` / `AWS_PROFILE` 등 env (일부 마스킹)
- `aws sts get-caller-identity` **실행 결과와 에러 메시지**

에러가 나면:

- **InvalidClientTokenId** → 토큰 무효/만료 (env 또는 프로파일 재설정)
- **Unable to locate credentials** → 이 프로세스에는 자격증명 없음 → 방법 A/B/C 적용
- **AccessDenied** → IAM 권한 부족 (sts:GetCallerIdentity 등 확인)

---

## 4. deploy.ps1 / verify.ps1 에서 프로파일 사용

- **deploy.ps1**  
  - `-AwsProfile <이름>` 지원.  
  - 지정 시 `$env:AWS_PROFILE` 설정하고, region 이 없으면 `ap-northeast-2` 로 설정.
- **verify.ps1**  
  - `-AwsProfile <이름>` 지원.  
  - 내부에서 호출하는 `deploy.ps1` / bootstrap 에 동일 프로파일이 적용되도록 전달.

스크립트 내부에서는 **`--profile` 을 직접 붙이지 않습니다.**  
`$env:AWS_PROFILE` 만 설정하면, 모든 `aws` 호출이 그 프로파일을 따릅니다.

---

## 5. 요약

| 목표 | 조치 |
|------|------|
| Cursor에서 그냥 deploy 돌리기 | 방법 A: `aws configure` 로 default 프로파일 설정 |
| SSO/이름 있는 프로파일 쓰기 | 방법 B: `aws sso login` 후 `-AwsProfile prod` 로 실행 |
| 왜 실패하는지 확인 | `pwsh scripts/v1/aws-diagnose.ps1` 실행 후 에러 메시지 확인 |
