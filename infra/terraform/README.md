# Academy Infrastructure — Terraform (Reference)

**Ownership:** `scripts/v1/deploy.ps1` and `docs/ssot/params.yaml` are the only
authoritative infrastructure path. This directory has no remote state or lock
configuration and is a non-executable reference snapshot.

**Do not run `terraform apply` or import production resources from this
directory.** Doing so would create split ownership with the PowerShell deploy
path.

## Prerequisites

- Terraform >= 1.5
- AWS credentials configured
- Existing: RDS (academy-db), Redis (academy-v1-redis), SQS, DynamoDB, ECR, IAM roles

## Safe inspection

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

`terraform plan` is not production evidence because this reference has no
authoritative state. Production planning uses
`pwsh scripts/v1/deploy.ps1 -Plan -AwsProfile default`.

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
