# V1 최종 배포 검증 보고서

**명칭:** V1 통일. **SSOT:** docs/00-SSOT/v1/params.yaml. **배포:** scripts/v1/deploy.ps1. **리전:** ap-northeast-2.

## 리소스 정리 진행 상황 (최근 실행)
- **API ASG:** 1/1/2 반영 완료 (run-resource-cleanup.ps1 -Execute + deploy.ps1).
- **SG:** academy-v1-vpce-sg 삭제 완료. academy-api-sg·academy-worker-sg는 DependencyViolation으로 유지.
- **EIP:** 4개 모두 ALB(2)·NAT(1)·RDS(1) 연동 중이라 해제하지 않음 (Association 없을 때만 Release 규칙).
- **진짜 문제(미해결):** API /health unreachable, TG healthy 0/2. TG 헬스체크는 `/healthz`:8000으로 설정됨. 타깃이 200을 반환하지 않음 → **인스턴스에서 컨테이너 기동·8000 포트·CloudWatch/SSM 로그 확인 필요.**

---

## 요약
| 항목 | 값 |
|------|-----|
| 검증 시각 | 2026-03-06T15:26:57.5656294+09:00 |
| 최종 상태 | FAIL |
| SSOT↔Actual 정합성 | **WARNING** |
| GO/NO-GO | **NO-GO** |

FAIL 항목 해결 후 재검증 필요.

## 합의사항 체크
| 항목 | 결과 |
|------|------|
| API ASG min/desired=1 | PASS |
| AI ASG min/desired=1 | PASS |
| Messaging ASG min/desired=1 | PASS |
| Solapi 고정 IP(NAT/EIP) 취소 | WARNING(EIP 잔여) |
| 빌드 서버 최종 0대 목표 | PASS |

## Front V1 연결
프론트를 V1 인프라(app/api 도메인, CORS, CDN/R2) 기준으로 연결한 검증 결과: **[front-connection.latest.md](./front-connection.latest.md)**

| 항목 | 결과 |
|------|------|
| app 도메인 200 |  |
| API 공개 /health | not checked |
| CORS/Cache | not checked / - |

## 남은 WARNING 및 후속 작업
- Drift 1건 이상 시: SSOT 반영 또는 합의된 예외 문서화 후 drift.latest.md 갱신.
- EIP/NAT 잔여: Solapi 고정 IP 요구 취소에 따라 제거 검토(비용·불필요 리소스).
- [WARNING] Drift: SSOT와 불일치 1건: API LT/academy-v1-api-lt
- [FAIL] API: /health unreachable: The request was canceled due to the configured HttpClient.Timeout of 10 seconds elapsing.
- [FAIL] API: ALB target healthy 0 / 2

## 상세 보고서
- [deploy-verification-latest.md](./deploy-verification-latest.md) — 인프라·Smoke·프론트/R2/CDN·SQS·Video·관측·GO/NO-GO 상세
- [consistency.latest.md](./consistency.latest.md) — SSOT↔실제↔합의사항 정합성
- [front-connection.latest.md](./front-connection.latest.md) — Front V1 연결 검증·근거
- [scale-policy.latest.md](./scale-policy.latest.md) — API ASG 스케일 정책 (런칭 전 min/desired=1)
- [resource-cleanup.latest.md](./resource-cleanup.latest.md) — 리소스 정리 기록 (EIP/EBS/SG/ASG)
- [cleanup-run.latest.md](./cleanup-run.latest.md) — 정리 스크립트 실행 결과
- [front-pipeline-mapping.latest.md](./front-pipeline-mapping.latest.md) — 프론트 Git 파이프라인 ↔ SSOT 매핑
- [audit.latest.md](./audit.latest.md) — 리소스·지표 스냅샷
- [drift.latest.md](./drift.latest.md) — SSOT 대비 drift


