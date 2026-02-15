# Docker Compose 로컬 실행

## 사전 요구사항

- 프로젝트 루트(`C:\academy`)에 `.env` 파일이 있어야 합니다.
- Docker Desktop(또는 Docker Engine + Compose) 설치.

## 한 번에 빌드하고 실행

### 1) 베이스 이미지 빌드 (최초 1회 또는 Dockerfile.base 변경 시)

```powershell
cd C:\academy
docker build -f docker/Dockerfile.base -t academy-base:latest .
```

### 2) 전체 시스템 기동 (postgres, redis, api, video-worker, messaging-worker 등)

```powershell
cd C:\academy
docker compose up --build
```

백그라운드 실행:

```powershell
docker compose up --build -d
```

### 3) api / video-worker / messaging-worker만 기동

```powershell
docker compose up --build api video-worker messaging-worker
```

(필요 시 postgres, redis도 함께 띄우려면 `docker compose up --build` 그대로 사용.)

## 참고

- 각 서비스는 **`.env`** 를 로드합니다 (`env_file: .env`).
- **api**, **video-worker**, **messaging-worker**는 로컬 소스(`src`, `apps`, `libs`, `manage.py`)를 컨테이너 `/app`에 볼륨 매핑하여, 코드 수정 시 이미지 재빌드 없이 반영됩니다.
- 베이스 이미지(`academy-base:latest`)가 없으면 `docker compose up --build` 시 서비스 빌드가 실패할 수 있으므로, 위 1단계를 먼저 실행하세요.
