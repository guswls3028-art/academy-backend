# 로컬 개발 시 DB 연결 (RDS 타임아웃 해결)

로컬에서 "Academy Backend - Local Development"를 실행할 때 `connection to server at "...rds.amazonaws.com" (port 5432) failed: timeout expired` 가 나오는 경우의 대처 방법입니다.

## 원인

- RDS(academy-db)는 VPC 내부에 있으며, **보안 그룹(academy-rds)**에서 5432 포트를 허용한 IP만 접속할 수 있습니다.
- 로컬 PC의 공인 IP가 해당 보안 그룹에 없으면 연결이 **타임아웃**됩니다.
- `.env` / `.env.local` 의 `DB_HOST` 가 RDS 호스트일 때, 위 조건이 맞지 않으면 실패합니다.

## 해결 방법

### 1) RDS 보안 그룹에 현재 IP 추가 (RDS 직접 접속)

로컬 PC의 **현재 공인 IP**를 RDS 보안 그룹 인바운드(5432)에 추가합니다.

```powershell
cd C:\academy
.\scripts\archive\legacy\add_current_ip_to_rds.ps1 -Region ap-northeast-2 -SecurityGroupId sg-06cfb1f23372e2597
```

- 성공 후 같은 네트워크에서 `runserver` 를 다시 실행하면 RDS로 연결됩니다.
- **주의**: 공인 IP가 바뀌면(집/회사 전환, 재접속 등) 다시 실행해야 합니다.
- RDS가 **퍼블릭 액세스 비활성화** 상태면, 보안 그룹만으로는 인터넷에서 접속할 수 없습니다. 이 경우 2번(SSH 터널)을 사용하세요.

### 2) SSH 터널 사용 (RDS가 비공개일 때)

RDS가 퍼블릭 액세스가 꺼져 있거나, 보안상 직접 개방을 원하지 않을 때:

1. RDS에 접근 가능한 **Bastion/EC2**에 SSH로 접속한 뒤, 로컬 5432를 RDS 5432로 포워딩합니다.
   ```bash
   ssh -L 5432:academy-db.cbm4oqigwl80.ap-northeast-2.rds.amazonaws.com:5432 ec2-user@<Bastion-IP> -i <키경로>
   ```
2. **`.env.local`** 에서 DB를 로컬 포트로 가리키도록 설정:
   ```ini
   DB_HOST=127.0.0.1
   DB_PORT=5432
   DB_NAME=postgres
   DB_USER=admin97
   DB_PASSWORD=<RDS 비밀번호>
   ```
3. 터널을 연 상태에서 `runserver` 를 실행합니다.

### 3) 로컬 PostgreSQL 사용

RDS를 쓰지 않고 로컬에서만 개발할 때:

1. 로컬에 PostgreSQL 설치(또는 Docker 등) 후 DB 생성:
   ```sql
   CREATE DATABASE academy;
   -- 또는 .env.local에 맞춰 DB_NAME 등 설정
   ```
2. **`.env.local`** 에 로컬 DB만 설정 (RDS 설정은 주석 처리):
   ```ini
   DB_HOST=127.0.0.1
   DB_PORT=5432
   DB_NAME=academy
   DB_USER=postgres
   DB_PASSWORD=로컬비밀번호
   ```
3. 마이그레이션 실행:
   ```powershell
   python manage.py migrate
   ```
4. `runserver` 실행.

## 설정 우선순위

- `manage.py` 는 **`.env`** 를 먼저 읽고, 그 다음 **`.env.local`** 을 읽어 덮어씁니다.
- 로컬 전용 값(DB_HOST=127.0.0.1, 로컬용 DB_NAME/DB_USER/DB_PASSWORD 등)은 **`.env.local`** 에 두면 됩니다.

## 참고

- RDS 보안 그룹 이름: **academy-rds** (ID: sg-06cfb1f23372e2597)
- 인벤토리: `docs/00-SSOT/v1/reports/aws-resource-inventory.latest.md`
