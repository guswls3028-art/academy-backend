# 00-SSOT — 단일 진실(Single Source of Truth)

> **운영 기준 우선 읽기:** `../README.md`
>  
> **실행 기준(코드가 실제로 읽는 경로):** `docs/00-SSOT/params.yaml`
>  
> **alias 정책:** `PATH-ALIAS-POLICY.md`

**현재 운영 기준 문서 버전(배포/CI): V1.1.0**

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
| [v1.1.1/](v1.1.1/) | **DOMAIN SSOT** | 메시징/커뮤니티/운영 정책 등 도메인 기준 문서 묶음 (배포 파이프라인 기준 아님) |

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

- **SSOT(실행)**: 인프라 파라미터는 `params.yaml`을 기준으로 한다.
- **SSOT(배포/CI 문서)**: 배포 아키텍처는 `v1.1.0/`을 기준으로 한다.
- **봉인 버전**: 아카이브된 버전 문서는 수정하지 않는다.
- **충돌 시**: 실행 코드(scripts, workflows, Dockerfile) > SSOT 문서 > 기타 문서.

## stale 경로 경고

- `docs/00-SSOT/v1/...` 표기는 과거 별칭/레거시 표기입니다.
- 실제 폴더 기준 경로를 우선 사용하세요:
  - `docs/00-SSOT/params.yaml`
  - `docs/00-SSOT/v1.1.0/...`
