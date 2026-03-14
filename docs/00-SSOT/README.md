# 00-SSOT — 단일 진실(Single Source of Truth)

**현재 활성 버전: V1.1.0** (무중단 배포 인프라 전환)

---

## 버전 정책 (Va.b.c)

| 세그먼트 | 의미 | 변경 조건 |
|----------|------|-----------|
| **a** | 프로젝트 버전 | 프로젝트 철학·구조 근본 변경 시. 사실상 1 유지 |
| **b** | 인프라 버전 | AWS 인프라 구조 변경 시만 변경 |
| **c** | 패치 버전 | 기능 추가, 버그 수정, UI 개선 등 |

---

## 활성 문서

| 버전 | 상태 | 설명 |
|------|------|------|
| [v1.1.0/](v1.1.0/) | **ACTIVE** | 무중단 배포 인프라 |

---

## 아카이브 (봉인됨, 수정 불가)

| 버전 | 설명 |
|------|------|
| [archive/v1.0.3/](archive/v1.0.3/) | Video infrastructure hardening |
| [archive/v1.0.2/](archive/v1.0.2/) | Subscription/billing, video social |
| [archive/v1.0.1/](archive/v1.0.1/) | CSS tokens, tenant isolation |
| [archive/v1.0.0/](archive/v1.0.0/) | Initial v1 infrastructure setup |
| [archive/v4/](archive/v4/) | Legacy v4 infrastructure |
| [archive/v3/](archive/v3/) | Legacy v3 |

---

## 원칙

- **SSOT**: 인프라 스펙·이름·파라미터는 활성 버전 문서만 기준으로 한다.
- **봉인 버전**: 아카이브된 버전 문서는 수정하지 않는다.
- **충돌 시**: 실행 코드(scripts, workflows, Dockerfile) > SSOT 문서 > 기타 문서.
