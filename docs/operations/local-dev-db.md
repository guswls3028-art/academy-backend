# 로컬 개발 DB 연결

운영 RDS는 private VPC 안에 있고 `publiclyAccessible: false`가 기준이다. 노트북의
공인 IP를 RDS 보안 그룹에 추가하는 직접 접속 방식은 지원하지 않는다.

## 빠른 코드 검증

외부 DB가 필요 없는 Django 검사와 테스트는 test settings를 사용한다.

```powershell
cd C:\academy\backend
.\.venv\Scripts\python.exe manage.py check --settings apps.api.config.settings.test
.\.venv\Scripts\python.exe -m pytest tests/test_smoke.py -v --tb=short -x
```

test settings는 SQLite in-memory DB와 테스트용 외부 서비스 값을 사용한다.

## 전체 로컬 실행

PostgreSQL 동작까지 확인해야 할 때는 로컬 PostgreSQL을 사용한다. Docker가
설치되어 있으면 백엔드 루트에서 다음처럼 기본 스택을 시작할 수 있다.

```powershell
cd C:\academy\backend
docker compose up --build postgres redis api messaging-worker
```

호스트에서 Django를 직접 실행하려면 PostgreSQL을 별도로 시작하고
`.env.local`에 로컬 전용 연결 값을 둔다.

```ini
DJANGO_SETTINGS_MODULE=apps.api.config.settings.dev
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=academy
DB_USER=postgres
DB_PASSWORD=<로컬 전용 비밀번호>
```

```powershell
cd C:\academy\backend
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

`manage.py`는 `.env`를 먼저 읽고 `.env.local`로 덮어쓴다. `.env.local`은
커밋하지 않는다.

### 로컬 로그인 계정 준비와 점검

로컬 전용 관리자 계정이 필요하면 비밀번호를 명령행 인자로 넘기거나 소스에
적지 않는다. 커밋되지 않는 `.env.local`에 다음 값을 두고 명시한 로컬
테넌트만 준비한다.

```ini
ACADEMY_DEV_USER_PASSWORD=<로컬 전용 비밀번호>
```

```powershell
cd C:\academy\backend
.\.venv\Scripts\python.exe manage.py ensure_dev_user --tenant dev-local --username dev-admin
```

명령은 SQLite 또는 localhost DB에서만 허용되고 원격 DB 우회 옵션은 없다. 기존 계정의 비밀번호를
바꿀 때 `token_version`을 증가시켜 기존 토큰을 무효화한다. 비밀번호는 출력하지
않고 앞뒤 공백도 임의로 제거하지 않는다.

계정 연결 상태만 확인할 때는 아래 비밀값·개인정보 비노출 진단 명령을 사용한다.
출력은 활성 테넌트 코드로 한정한 도메인, 프로그램, 사용자, 멤버십의 최소 상태만
포함하며 조회 입력인 로그인 ID도 되비치지 않는다. 비밀번호 검증이나 이름·전화번호·
이메일 덤프도 하지 않는다.

```powershell
.\.venv\Scripts\python.exe manage.py dump_tenant_and_user --tenant-code dev-local --username dev-admin
```

## 운영 DB 접근이 꼭 필요한 경우

운영 데이터 점검은 일반 개발 경로가 아니다. 작업 범위와 읽기/쓰기 권한을
먼저 승인받고, 현재 운영 런북이 지정한 SSM 또는 승인된 터널 경로만 사용한다.
RDS를 public으로 바꾸거나 `0.0.0.0/0`, 현재 공인 IP를 보안 그룹에 임시
추가하는 스크립트를 사용하지 않는다.

연결 실패 시 `.env.local`의 `DB_HOST`가 운영 RDS를 가리키지 않는지 먼저
확인한다. 운영 인벤토리의 실제 상태는 `docs/reports/` 최신 audit/drift
보고서와 `docs/ssot/params.yaml`을 따른다.
