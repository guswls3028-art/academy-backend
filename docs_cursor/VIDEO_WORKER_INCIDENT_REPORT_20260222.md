# VIDEO WORKER PIPELINE INCIDENT REPORT (FACT-ONLY)

**상태:** 이 보고서는 `.\scripts\collect_video_worker_incident_data.ps1` 실행 시 생성됩니다.

**실행 방법:**
```powershell
# AWS CLI 자격증명 설정 후 (aws configure 또는 환경변수)
.\scripts\collect_video_worker_incident_data.ps1
```

스크립트가 수집하는 데이터:
1. ASG 인스턴스 상태 / 스케일링 활동 20건
2. Launch Template (UserData, IAM, SecurityGroup, Subnet)
3. SSM 등록 vs ASG 인스턴스 (ASG_NOT_SSM)
4. Runtime 조사 (investigate_video_worker_runtime.ps1)
5. SQS attributes + CloudWatch 메트릭 (15분)
6. ASG_NOT_SSM 인스턴스별 콘솔 로그

**출력:**
- `backups/video_worker/incident_YYYYMMDD_HHMMSS/` — 수집 데이터
- `docs_cursor/VIDEO_WORKER_INCIDENT_REPORT_YYYYMMDD.md` — 본 보고서 (덮어쓰기)
