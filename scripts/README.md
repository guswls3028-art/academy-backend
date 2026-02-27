# scripts — 스크립트 (정식 = v4만)

**진입점은 이 파일.** 인프라 배포·검증은 **scripts/v4** 만 사용한다.

---

## 정식 (v4만)

| 용도 | 경로 |
|------|------|
| **배포** | `scripts/v4/deploy.ps1` |
| **새 PC 준비** | `scripts/v4/bootstrap.ps1` |
| **검증(5단계)** | `scripts/v4/verify.ps1` → reports/verify.latest.md |
| **옵션** | -Plan, -PruneLegacy, -PurgeAndRecreate, -PurgeAndRecreate -DryRun |

---

## 아카이브 (실행 금지, 참고용)

v4 제외한 모든 스크립트는 **scripts/archive/** 아래에 보관했다. **실행하지 말 것.**

| 하위 | 설명 |
|------|------|
| **archive/infra/** | 구 인프라 스크립트·JSON (v4/templates에 이전됨) |
| **archive/legacy/** | 구 scripts 루트 .ps1/.py/.sh 등 |
| **archive/redeploy/** | 구 redeploy 스크립트 |
| **archive/scripts_v3/** | 구 scripts_v3 풀스택 배포 (v4로 대체) |

상세: [scripts/archive/README.md](archive/README.md)

---

## 관련 문서

- 정식 SSOT: [docs/00-SSOT/v4/SSOT.md](../docs/00-SSOT/v4/SSOT.md)
- 문서 인덱스: [docs/README.md](../docs/README.md)
