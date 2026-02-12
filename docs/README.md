# Documentation

프로덕션 배포 및 운영을 위한 핵심 문서 디렉토리입니다.

---

## 📋 핵심 문서

### ⭐ 배포 가이드 (필수)
- **[DEPLOYMENT_MASTER_GUIDE.md](DEPLOYMENT_MASTER_GUIDE.md)** - **메인 배포 가이드**
  - 인프라 아키텍처
  - 비용 방어 전략
  - 배포 절차
  - 환경 변수 리스트
  - 확장 로드맵
  - 모니터링 및 검증
  - 트러블슈팅

### 아키텍처 문서
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - 전체 시스템 아키텍처 개요

### 인프라 문서
- **[INFRASTRUCTURE.md](INFRASTRUCTURE.md)** - AWS 리소스 및 인프라 설정
- **[COST_FORECAST.md](COST_FORECAST.md)** - 비용 예측 (500 DAU, 10k DAU)
- **[QUEUE_SYSTEM.md](QUEUE_SYSTEM.md)** - SQS 큐 시스템 상세

### Architecture Decision Records (ADR)
- **[adr/ADR-001](adr/ADR-001)**
- **[adr/ADR-002](adr/ADR-002)**
- **[adr/ADR-003](adr/ADR-003)**
- **[adr/ADR-004](adr/ADR-004)**

---

## 🚀 빠른 시작

1. **배포 준비**: [`DEPLOYMENT_MASTER_GUIDE.md`](DEPLOYMENT_MASTER_GUIDE.md) 읽기
2. **아키텍처 이해**: [`ARCHITECTURE.md`](ARCHITECTURE.md) 읽기
3. **인프라 설정**: [`INFRASTRUCTURE.md`](INFRASTRUCTURE.md) 참조
4. **비용 계획**: [`COST_FORECAST.md`](COST_FORECAST.md) 참조

---

## 📌 핵심 원칙

- **단일 진실의 원천**: `DEPLOYMENT_MASTER_GUIDE.md` 하나로 배포 가능
- **현재 상태 반영**: 실제 구현만 문서화
- **프로덕션 등급**: 간결하지만 완전한 문서

---

**최종 업데이트**: 2026-02-12
