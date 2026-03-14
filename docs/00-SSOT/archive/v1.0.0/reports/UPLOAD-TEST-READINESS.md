# 영상 업로드 테스트 준비 완료 체크리스트

**생성일:** 2026-03-06  
**목적:** 프론트엔드에서 실제 영상 업로드 테스트 전 인프라·파이프라인 검증 완료 확인

---

## 1. 인프라 검증 (완료)

| 항목 | 상태 | 비고 |
|------|------|------|
| API healthz | ✅ 200 | https://api.hakwonplus.com/healthz |
| /core/program | ✅ 200 | X-Tenant-Code: hakwonplus |
| ALB target health | ✅ 1/1 healthy | academy-v1-api-tg |
| Batch Queue | ✅ ENABLED, VALID | academy-v1-video-batch-queue |
| Batch JobDef | ✅ rev 20 | academy-v1-video-batch-jobdef |
| DynamoDB lock table | ✅ ACTIVE | academy-v1-video-job-lock |
| API EC2 IAM | ✅ Batch+DynamoDB | academy-api-video-upload 정책 |
| SSM API env | ✅ v1 | VIDEO_BATCH_JOB_QUEUE/DEFINITION = academy-v1-* |

---

## 2. 파이프라인 (CI)

| 항목 | 상태 |
|------|------|
| V1 Build and Push (OIDC) | 실행 중/완료 시 [Actions](https://github.com/guswls3028-art/academy-backend/actions) 확인 |

---

## 3. 프론트 업로드 테스트 절차

### 사전 조건
- hakwonplus.com 관리자 계정 로그인
- 강의 > 차시 > 영상 페이지 접근 권한

### 테스트 URL
```
https://hakwonplus.com/admin/lectures/{강의ID}/sessions/{차시ID}/videos
```

### 테스트 단계
1. **로그인**: https://hakwonplus.com 에서 관리자 로그인
2. **영상 페이지 이동**: 강의 → 해당 강의 → 차시 → 영상 탭
3. **영상 추가**: "영상 추가" 버튼 클릭
4. **파일 선택**: MP4 파일 선택 (권장: 10MB 이하 샘플로 먼저)
5. **업로드 대기**: R2 presigned PUT으로 청크 업로드 → 완료 시 `upload/complete` API 자동 호출
6. **결과 확인**:
   - 성공: "업로드 완료" → Batch Job 제출 → 인코딩 진행
   - 실패: "업로드 완료 처리 중 오류" 또는 "비디오 작업 등록 실패" → API 로그 확인

### 실패 시 확인
- 브라우저 Network 탭: `POST .../upload/complete/` 응답 코드/본문
- 503 시: API 인스턴스 `docker logs academy-api` 에서 `VIDEO_UPLOAD_COMPLETE_ERROR` 검색

---

## 4. 검증 명령 (로컬)

```powershell
# API health
curl -s -o NUL -w "%{http_code}" https://api.hakwonplus.com/healthz

# program (테넌트 도메인)
curl -s "https://api.hakwonplus.com/api/v1/core/program/" -H "X-Tenant-Code: hakwonplus"

# upload/complete API 직접 호출 (VideoId, JWT 필요)
pwsh scripts/v1/test-upload-complete.ps1 -VideoId 187 -Token "Bearer <JWT>"
# JWT: hakwonplus.com 로그인 후 DevTools > Application > Local Storage > access
```

---

## 5. 배포 검증 리포트

- **deploy-verification-latest.md**: `pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default`
- **V1-FINAL-REPORT.md**: 동일 실행 시 갱신

---

## 6. 프론트 업로드 테스트 준비 완료 체크

| # | 항목 | 상태 |
|---|------|------|
| 1 | 인프라 검증 (API, Batch, DynamoDB, IAM) | ✅ |
| 2 | CI 파이프라인 (V1 Build and Push) | 실행 후 [Actions](https://github.com/guswls3028-art/academy-backend/actions)에서 완료 확인 |
| 3 | ECR 이미지 (academy-api, academy-video-worker) | CI 완료 시 최신 푸시 |
| 4 | API Instance Refresh (선택) | 새 이미지 반영 시 `deploy.ps1 -Phase InstanceRefresh` |
| 5 | 프론트 업로드 테스트 | 위 §3 절차대로 hakwonplus.com에서 수행 |

**다음 단계:** CI 완료 → (선택) Instance Refresh → hakwonplus.com 관리자 로그인 → 강의/차시/영상 페이지에서 MP4 업로드

---

## 7. "대기 중" / 인스턴스 안 뜸 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| 영상 "대기 중" 멈춤 | upload/complete 미호출 또는 503 | 브라우저 Network 탭에서 `POST .../upload/complete/` 응답 확인 |
| Batch 인스턴스 안 뜸 | Job 미제출 또는 CE 스케일 지연 | `aws batch list-jobs --job-queue academy-v1-video-batch-queue` 로 Job 존재 여부 확인 |
| API env 구버전 | 인스턴스 부팅 시점 SSM 사용 | `pwsh scripts/v1/start-api-instance-refresh.ps1` 실행 후 5~10분 대기 |

**연결 검증:**
```powershell
# 1) API health
curl -s -o NUL -w "%{http_code}" https://api.hakwonplus.com/healthz

# 2) SSM env (VIDEO_BATCH 확인)
aws ssm get-parameter --name "/academy/api/env" --query "Parameter.Value" --output text | Select-String VIDEO_BATCH

# 3) Batch Job 제출 테스트 (VideoId, JWT 필요)
pwsh scripts/v1/test-upload-complete.ps1 -VideoId <ID> -Token "Bearer <JWT>"
```
