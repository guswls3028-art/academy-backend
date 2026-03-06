# Academy Infrastructure — Terraform (Reference)

**Note:** The primary deployment method is `scripts/v1/deploy.ps1` (PowerShell).  
This Terraform configuration is a **reference implementation** for the target architecture.

## Prerequisites

- Terraform >= 1.5
- AWS credentials configured
- Existing: RDS (academy-db), Redis (academy-v1-redis), SQS, DynamoDB, ECR, IAM roles

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your VPC/subnet IDs

terraform init
terraform plan   # Review changes
terraform apply  # Apply (will create/update resources)
```

## Resources NOT managed by Terraform

- RDS (academy-db) — DO NOT DELETE
- Redis (academy-v1-redis)
- DynamoDB tables
- SQS queues
- ECR repositories
- IAM roles (created by scripts/v1)
- VPC, subnets (existing)

## Files

| File | Purpose |
|------|---------|
| versions.tf | Provider, backend |
| variables.tf | Input variables |
| vpc.tf | VPC data sources |
| security_groups.tf | 5 SG design |
| alb.tf | ALB + Target Group |
| api_asg.tf | API ASG |
| worker_asg.tf | Messaging + AI worker ASGs |
| batch.tf | Batch CE + Queues |
| eventbridge.tf | EventBridge rules → Batch |
