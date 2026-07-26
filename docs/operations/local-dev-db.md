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

## 운영 DB 접근이 꼭 필요한 경우

운영 데이터 점검은 일반 개발 경로가 아니다. 작업 범위와 읽기/쓰기 권한을
먼저 승인받고, 현재 운영 런북이 지정한 SSM 또는 승인된 터널 경로만 사용한다.
RDS를 public으로 바꾸거나 `0.0.0.0/0`, 현재 공인 IP를 보안 그룹에 임시
추가하는 스크립트를 사용하지 않는다.

연결 실패 시 `.env.local`의 `DB_HOST`가 운영 RDS를 가리키지 않는지 먼저
확인한다. 운영 인벤토리의 실제 상태는 `docs/reports/` 최신 audit/drift
보고서와 `docs/ssot/params.yaml`을 따른다.
