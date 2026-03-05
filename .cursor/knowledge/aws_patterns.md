# AWS Patterns (Academy)

**Approved AWS services:**

- EC2 (ASG for API, Messaging, AI workers)
- AWS Batch (Video workers)
- SQS
- EventBridge
- RDS PostgreSQL
- DynamoDB (locking)
- Redis (ElastiCache)

**Disallowed:**

- ECS, EKS
- Lambda (except legacy)
- AWS S3 (use Cloudflare R2)
- AWS CloudFront (use Cloudflare CDN)

**Region:** ap-northeast-2  
**Account:** single account  
**SSOT:** docs/00-SSOT/v4
