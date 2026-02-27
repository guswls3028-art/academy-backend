# SSOT 문서 기준 변경 로그

**역할:** SSOT 배포/인프라 문서의 기준 변경을 날짜·요약으로 기록. 추측 없이 "무엇을 언제 바꿨는지"만 기술.

---

## 형식

- **날짜:** YYYY-MM-DD
- **문서/범위:** 변경된 문서 또는 "전체 SSOT" 등
- **변경 요약:** 한 줄~몇 줄
- **근거:** 커밋, 이슈, 또는 "신규 작성" 등

---

## 2026-02-26

- **문서/범위:** 신규 작성 — SSOT-ONE-TAKE-DEPLOYMENT.md, SSOT-RESOURCE-INVENTORY.md, SSOT-IDEMPOTENCY-RULES.md, SSOT-RUNBOOK.md, SSOT-CHANGELOG.md
- **변경 요약:** 흩어진 배포/인프라 문서를 통합하여 원테이크 멱등성 배포 SSOT 5종 생성. 발견 목록·정리 이슈·아키텍처·환경 파라미터·리소스 인벤토리·멱등성 규칙·런북·변경 로그를 단일 체계로 정리.
- **근거:** 사용자 요청(DevOps 아키텍트/테크라이터 역할). 기반: docs/deploy/VIDEO_WORKER_INFRA_SSOT_PUBLIC_V2.md, scripts/infra/infra_full_alignment_public_one_take.ps1, video_batch_production_runbook.md, deploy_preflight.ps1, recreate_batch_in_api_vpc.ps1, batch_ops_setup.ps1 등.

---

## 이후 변경 시 템플릿

```markdown
## YYYY-MM-DD

- **문서/범위:** (파일명 또는 "전체 SSOT")
- **변경 요약:** (무엇을 추가/수정/삭제했는지)
- **근거:** (커밋 해시, 이슈 번호, 또는 결정 근거)
```
