# Orphan Resources & Cleanup Plan

**Region:** ap-northeast-2  
**Generated:** 2026-03-06

---

## 1. Orphan Resources Identified

### 1.1 Security Groups (0 ENI attached)

| GroupId | GroupName | VPC | Action |
|---------|------------|-----|--------|
| sg-0f8d581baa7bc39c9 | academy-v1-vpce-sg | vpc-0831a2484f9b114c2 | Safe delete |
| sg-0051cc8f79c04b058 | academy-api-sg | vpc-0831a2484f9b114c2 | Safe delete |
| sg-02692600fbf8e26f7 | academy-worker-sg | vpc-0831a2484f9b114c2 | Safe delete |

### 1.2 Unused ENIs

| ENI | Description | Status | Action |
|-----|-------------|--------|--------|
| eni-0cba52698eb339acb | AWS Lambda VPC ENI-academy-worker-queue-depth-metric | available | Safe delete (Lambda deleted, ENI orphaned) |

### 1.3 Unattached EIPs

**None.** All 3 EIPs are attached:
- eipalloc-005028ec477ae0819 → ALB (service-managed)
- eipalloc-0cf9f6d0e100d6787 → ALB (service-managed)
- eipalloc-02bcb9e54f8f9cca3 → RDS (service-managed)

### 1.4 Duplicate / Legacy Security Groups

| Group | Issue |
|-------|-------|
| academy-api-sg | Replaced by academy-v1-sg-app |
| academy-worker-sg | Replaced by academy-v1-sg-app |
| academy-v1-vpce-sg | VPC endpoint SG, unused (endpoints use default) |

### 1.5 Legacy EventBridge Rules (DISABLED)

| Rule | Action |
|------|--------|
| academy-reconcile-video-jobs | Safe delete |
| academy-video-scan-stuck-rate | Safe delete |
| academy-worker-autoscale-rate | Safe delete |
| academy-worker-queue-depth-rate | Safe delete |

### 1.6 Legacy IAM Roles

| Role | Issue |
|------|-------|
| academy-eventbridge-batch-video-role | Duplicate of academy-v1-eventbridge-batch-video-role |

### 1.7 EC2 Instance (Non-SSOT)

| InstanceId | Name | Issue |
|------------|------|-------|
| i-0c11e7127e7ea03f8 | academy-build-arm64 | Build server: SSOT says 0 build servers (GitHub Actions only) |

### 1.8 Legacy VPC Resources (academy-v4)

| Resource | VPC | Action |
|----------|-----|--------|
| academy-v4-sg-data | vpc-00fb37f7f4bc98385 | Manual: verify no dependencies |
| academy-v4-sg-batch | vpc-00fb37f7f4bc98385 | Manual: verify no dependencies |
| academy-v4-sg-app | vpc-00fb37f7f4bc98385 | Manual: verify no dependencies |
| academy-lambda-metric-sg | vpc-009e3ea6265c7a203 | Manual: Lambda VPC |

---

## 2. Cleanup Plan

### 2.1 Safe Delete List (no dependencies)

| # | Resource Type | Identifier | Script |
|---|---------------|------------|--------|
| 1 | ENI | eni-0cba52698eb339acb | cleanup-orphans.ps1 |
| 2 | Security Group | sg-0f8d581baa7bc39c9 (academy-v1-vpce-sg) | cleanup-orphans.ps1 |
| 3 | Security Group | sg-0051cc8f79c04b058 (academy-api-sg) | cleanup-orphans.ps1 |
| 4 | Security Group | sg-02692600fbf8e26f7 (academy-worker-sg) | cleanup-orphans.ps1 |
| 5 | EventBridge Rule | academy-reconcile-video-jobs | cleanup-orphans.ps1 |
| 6 | EventBridge Rule | academy-video-scan-stuck-rate | cleanup-orphans.ps1 |
| 7 | EventBridge Rule | academy-worker-autoscale-rate | cleanup-orphans.ps1 |
| 8 | EventBridge Rule | academy-worker-queue-depth-rate | cleanup-orphans.ps1 |

### 2.2 Risky Delete List (requires verification)

| # | Resource Type | Identifier | Risk |
|---|---------------|------------|------|
| 1 | EC2 | i-0c11e7127e7ea03f8 (academy-build-arm64) | Build server: stop first, verify no CI dependency |
| 2 | IAM Role | academy-eventbridge-batch-video-role | Check if any rule targets it |

### 2.3 Manual Confirmation Required

| # | Resource Type | Identifier | Reason |
|---|---------------|------------|--------|
| 1 | VPC | vpc-00fb37f7f4bc98385 | academy-v4 VPC: may have other resources |
| 2 | VPC | vpc-009e3ea6265c7a203 | Lambda VPC |
| 3 | SG | academy-v4-sg-* | VPC v4 resources |
| 4 | SG | academy-lambda-metric-sg | Lambda VPC |

---

## 3. Batch CE Not Used by Queues

**None.** Both queues use their CEs:
- academy-v1-video-batch-queue → academy-v1-video-batch-ce ✓
- academy-v1-video-ops-queue → academy-v1-video-ops-ce ✓

---

## 4. ASGs Not Connected to ALB

| ASG | Expected | Actual |
|-----|----------|--------|
| academy-v1-api-asg | ALB | ✓ Connected |
| academy-v1-messaging-worker-asg | N/A | N/A (workers) |
| academy-v1-ai-worker-asg | N/A | N/A (workers) |

---

## 5. Execution Order

1. Delete orphan ENI (available)
2. Remove EventBridge rule targets (if any) → delete rules
3. Delete orphan SGs (0 ENI)
4. Verify academy-eventbridge-batch-video-role not in use → delete
5. Stop academy-build-arm64 (optional)
