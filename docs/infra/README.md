# Academy AWS Infrastructure — SSOT Clean Architecture

**Region:** ap-northeast-2 (Seoul)  
**Primary deployment:** `scripts/v1/deploy.ps1`

---

## Deliverables

| # | Document | Description |
|---|----------|-------------|
| 1 | [01-dependency-graph.md](01-dependency-graph.md) | EC2, ASG, SG, ENI, ALB, Batch, EventBridge, IAM dependency graph |
| 2 | [02-orphan-resources-and-cleanup-plan.md](02-orphan-resources-and-cleanup-plan.md) | Orphan resources, safe/risky/manual deletion plan |
| 3 | [03-target-architecture.md](03-target-architecture.md) | Minimal 5-SG target architecture |
| 4 | [04-architecture-diagram.md](04-architecture-diagram.md) | Mermaid diagram |

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/v1/cleanup-orphans.ps1` | Safely remove orphan ENIs, SGs, EventBridge rules, EIPs |
| `scripts/v1/deploy.ps1 -PruneLegacy` | Remove non-SSOT resources |
| `scripts/v1/deploy.ps1 -PurgeAndRecreate` | Purge SSOT compute + full Ensure |

---

## Terraform (Reference)

`infra/terraform/` — Reference implementation. Primary deployment uses PowerShell.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform plan
```

---

## Constraints

**DO NOT DELETE:**
- RDS (academy-db)
- Redis (academy-v1-redis)
- DynamoDB tables
- SQS queues

**Allowed to rebuild:**
- EC2, ASG
- Security Groups
- Batch CE
- EventBridge rules
- Orphan EIPs, ENIs, legacy SGs
