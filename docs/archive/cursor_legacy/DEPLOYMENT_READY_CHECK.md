# 배포 준비 상태 확인 및 배포 가이드

**작업일**: 2026-02-18  
**상태**: ✅ 배포 가능

---

## ✅ 코드 반영 상태

### 백엔드 변경사항
- ✅ Redis 상태 캐싱 헬퍼 생성 (Video, AI Job)
- ✅ Progress 엔드포인트 추가 (VideoProgressView, JobProgressView)
- ✅ Worker Redis 상태 저장 추가 (complete_video, fail_video, mark_processing, repositories_ai)
- ✅ VideoProgressAdapter, RedisProgressAdapter tenant_id 지원
- ✅ encoding_progress.py tenant-aware 수정
- ✅ get_video_for_update() select_related 추가

### 프론트엔드 변경사항
- ✅ useWorkerJobPoller.ts 폴링 전환 (Redis-only 엔드포인트 사용)

**모든 코드 변경 완료, 배포 가능 상태** ✅

---

## 🔍 RDS 스펙 확인 방법

### AWS CLI로 확인
```powershell
# RDS 인스턴스 목록 및 스펙 확인
aws rds describe-db-instances \
  --region ap-northeast-2 \
  --query 'DBInstances[*].[DBInstanceIdentifier,DBInstanceClass,Engine,EngineVersion,AllocatedStorage]' \
  --output table
```

### 현재 예상 스펙 (문서 기준)
- **db.t4g.micro**: max_connections=87, vCPU=2, RAM=1GB
- **db.t4g.small**: max_connections=125, vCPU=2, RAM=2GB  
- **db.t4g.medium**: max_connections=250, vCPU=2, RAM=4GB

**실제 스펙은 위 명령어로 확인 필요**

---

## 🚀 배포 방법

### 1. 배포 전 검증 (권장)
```powershell
cd C:\academy
.\scripts\deploy_preflight.ps1
```

### 2. 풀배포 실행 (백엔드 + 프론트엔드)

#### 백엔드 배포
```powershell
# AWS 키 설정
$env:AWS_ACCESS_KEY_ID = "YOUR_ROOT_ACCESS_KEY_ID"
$env:AWS_SECRET_ACCESS_KEY = "YOUR_ROOT_SECRET_KEY"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"

# 배포 실행
cd C:\academy
.\scripts\full_redeploy.ps1 -GitRepoUrl "https://github.com/guswls3028-art/academy-backend.git" -WorkersViaASG
```

**옵션**:
- `-NoCache`: 캐시 없이 빌드 (의존성 변경 시)
- `-SkipBuild`: 빌드 생략, 워커만 리프레시

#### 프론트엔드 배포
```powershell
cd C:\academyfront
# 프론트엔드 배포 스크립트 실행 (프로젝트에 따라 다름)
# 예: npm run build && 배포 스크립트 실행
```

---

## 📋 배포 체크리스트

### 배포 전
- [ ] 코드 변경사항 커밋 및 푸시 완료
- [ ] 배포 전 검증 (`deploy_preflight.ps1`) 통과
- [ ] AWS 키 설정 확인

### 배포 중
- [ ] 백엔드 빌드 성공 확인
- [ ] ECR 이미지 푸시 성공 확인
- [ ] API 서버 배포 성공 확인
- [ ] 워커 ASG 리프레시 성공 확인

### 배포 후
- [ ] API 서버 정상 작동 확인
- [ ] 워커 정상 작동 확인
- [ ] 프론트엔드 진행률 표시 정상 확인
- [ ] Redis 연결 정상 확인

---

## ⚠️ 주의사항

1. **배포 중 키 변경 금지**: 배포 시작 후 같은 세션에서 같은 키 유지
2. **워커 리프레시 시간**: 인스턴스 교체에 5-10분 소요
3. **다운타임**: API 서버 재시작 시 일시적 다운타임 가능

---

## 🔍 배포 후 확인 사항

### 1. API 엔드포인트 확인
```bash
# Video Progress 엔드포인트
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://your-api-domain/media/videos/{video_id}/progress/

# Job Progress 엔드포인트  
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://your-api-domain/api/v1/jobs/{job_id}/progress/
```

### 2. Redis 연결 확인
- API 서버 로그에서 Redis 연결 확인
- Redis 키 생성 확인 (`tenant:{tenant_id}:video:{video_id}:status`)

### 3. 프론트엔드 동작 확인
- 영상 업로드 후 진행률 표시 확인
- 엑셀 파싱 후 진행률 표시 확인
- 완료/실패 상태 정확히 표시 확인

---

## 📊 예상 효과 (배포 후)

### 즉시 확인 가능
- ✅ 프론트엔드 진행률 정상 표시
- ✅ Redis 키 생성 확인

### 24시간 후 확인
- ✅ DB 부하 감소 (CloudWatch에서 확인 가능)
- ✅ CPUUtilization 감소 예상 (30-50%)
- ✅ DatabaseConnections 감소 예상 (20-30%)

---

**배포 준비 완료. 위 명령어로 배포 진행하세요.** ✅
