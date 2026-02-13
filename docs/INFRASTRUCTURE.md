# Infrastructure

## Overview

AWS infrastructure setup for production deployment. All resources are designed for cost efficiency and scalability.

## Required Resources

### 1. Compute

#### API Server
- **Type**: ECS Fargate (or EC2)
- **Spec**: 0.5 vCPU, 1GB RAM (initial)
- **Quantity**: 2 (high availability)
- **Cost**: ~$30/month (Fargate) or ~$30/month (EC2 t4g.small × 2)

#### Video Worker
- **Type**: ECS Fargate Spot (or EC2)
- **Spec**: 0.5 vCPU, 1GB RAM
- **Quantity**: 1-2 (depending on load)
- **Cost**: ~$10/month (Fargate Spot) or ~$15/month (EC2 t4g.small)

#### AI Worker CPU
- **Type**: ECS Fargate Spot (or EC2)
- **Spec**: 1 vCPU, 2GB RAM
- **Quantity**: 1-2 (depending on load)
- **Cost**: ~$20/month (Fargate Spot) or ~$30/month (EC2 t4g.medium)

#### AI Worker GPU (Future)
- **Type**: EC2 (GPU instance)
- **Spec**: g4dn.xlarge (1 GPU)
- **Quantity**: 1 (as needed)
- **Cost**: ~$200/month

#### Messaging Worker
- **Type**: ECS Fargate (or EC2)
- **Spec**: 0.25 vCPU, 512MB RAM
- **Quantity**: 1 (24/7 상시 운영 권장)
- **Purpose**: SMS/LMS 발송 (Solapi), 예약 발송, 알림톡
- **Cost**: ~$10/month

### 2. Load Balancer

#### Application Load Balancer (ALB)
- **Type**: Application Load Balancer
- **Purpose**: API server load balancing
- **Cost**: ~$20/month

### 3. Database

#### RDS PostgreSQL
- **Type**: db.t4g.micro (initial) → db.t4g.medium (10k DAU)
- **Multi-AZ**: No (initial) → Yes (10k DAU)
- **Backup**: 7-day retention
- **Cost**: ~$15/month (initial) → ~$80/month (10k DAU)

**Connection Pooling:**
- Initial: Not required
- 10k DAU: PgBouncer recommended

### 4. Storage

#### S3 Bucket (Media)
- **Name**: `academy-media-prod`
- **Purpose**: Media file storage
- **Versioning**: Enabled
- **Lifecycle**: IA after 30 days, Glacier after 90 days
- **Cost**: ~$10/month (initial) → ~$100/month (10k DAU)

#### S3 Bucket (Frontend)
- **Name**: `academy-frontend-prod`
- **Purpose**: React app static hosting
- **Cost**: ~$1/month

#### CloudFront Distribution
- **Origin**: S3 Buckets (Media + Frontend)
- **Purpose**: Static assets and media CDN
- **Cost**: ~$15/month (initial) → ~$200/month (10k DAU)

### 5. Queue

#### SQS Queues
- **Video Queue**: `academy-video-jobs`
- **AI Lite Queue**: `academy-ai-jobs-lite`
- **AI Basic Queue**: `academy-ai-jobs-basic`
- **AI Premium Queue**: `academy-ai-jobs-premium`
- **Messaging Queue**: `academy-messaging-jobs`
- **Cost**: ~$2/month (initial) → ~$10/month (10k DAU)

#### Dead Letter Queues (DLQ)
- One DLQ per queue (5 total)
- **Cost**: Included (SQS request cost)

### 6. Networking

#### VPC
- **CIDR**: 10.0.0.0/16
- **Subnets**: Public × 2, Private × 2 (per AZ)
- **Cost**: Free

#### Internet Gateway
- **Purpose**: Public subnet internet access
- **Cost**: Free

#### NAT Gateway (Optional)
- **Purpose**: Private subnet internet access
- **Cost**: ~$32/month (unnecessary initially)

### 7. Monitoring & Logging

#### CloudWatch Logs
- **Purpose**: Application log collection
- **Retention**: 7 days
- **Cost**: ~$1/month (initial) → ~$10/month (10k DAU)

#### CloudWatch Metrics
- **Purpose**: Metrics collection
- **Cost**: Included (basic metrics)

#### SNS Topics
- **Purpose**: Alert delivery
- **Cost**: ~$0.50/month

### 8. Security

#### IAM Roles
- **API Role**: SQS, S3, RDS access
- **Worker Role**: SQS, S3 access
- **Cost**: Free

#### Secrets Manager (Optional)
- **Purpose**: Sensitive information management
- **Cost**: ~$0.40/month per secret

## Resource Creation Scripts

### SQS Queue Creation

**Video + Messaging Queues:**
```bash
python scripts/create_sqs_resources.py ap-northeast-2
```

**AI Queues (3-Tier):**
```bash
python scripts/create_ai_sqs_resources.py ap-northeast-2
```

### CloudFormation Template (Optional)

Infrastructure can be codified with CloudFormation templates in the future.

## Resource Creation Order

1. **VPC and Networking** (VPC, subnets, IGW)
2. **RDS** (PostgreSQL)
3. **S3 Buckets** (Media, Frontend)
4. **SQS Queues** (Video, AI Tier-based)
5. **IAM Roles** (per-service permissions)
6. **ECS Cluster** (or EC2)
7. **ALB** (API load balancer)
8. **CloudFront** (CDN)
9. **CloudWatch** (logging, metrics)

## Cost Summary

### Initial (500 DAU)

| Item | Monthly Cost |
|------|--------------|
| Compute (API + Workers) | $60 |
| ALB | $20 |
| RDS | $15 |
| S3 | $11 |
| CloudFront | $20 |
| SQS | $2 |
| CloudWatch | $1 |
| **Total** | **~$129/month** |

### Target (10k DAU)

| Item | Monthly Cost |
|------|--------------|
| Compute (API + Workers) | $200 |
| ALB | $20 |
| RDS (Multi-AZ) | $80 |
| S3 | $100 |
| CloudFront | $250 |
| SQS | $10 |
| CloudWatch | $10 |
| **Total** | **~$670/month** |

## IAM Roles

### API Server Role

**Permissions:**
- SQS: `SendMessage`, `ReceiveMessage`, `DeleteMessage`
- S3: `GetObject`, `PutObject`, `DeleteObject`
- RDS: Database connection

**Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage"
      ],
      "Resource": "arn:aws:sqs:ap-northeast-2:*:academy-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::academy-media-prod/*"
    }
  ]
}
```

### Worker Role

**Permissions:**
- SQS: `ReceiveMessage`, `DeleteMessage`
- S3: `GetObject`, `PutObject`

## Network Architecture

```
Internet
   │
   ▼
CloudFront (CDN)
   │
   ├─── Frontend (S3 Static)
   │
   └─── ALB
         │
         ├─── API Server (ECS/EC2)
         │      │
         │      ├─── RDS PostgreSQL
         │      └─── S3 (Media)
         │
         └─── Workers (ECS/EC2)
                │
                ├─── SQS Queues
                └─── S3 (Media)
```

## Security Considerations

1. **VPC**: Isolate resources in private subnets where possible
2. **IAM**: Least privilege principle
3. **Secrets**: Use AWS Secrets Manager or environment variables
4. **Encryption**: Enable encryption at rest (RDS, S3)
5. **HTTPS**: CloudFront and ALB enforce HTTPS
6. **WAF**: Consider AWS WAF for DDoS protection (10k DAU)

## Scaling Considerations

### Horizontal Scaling
- API Server: Stateless, easy horizontal scaling
- Workers: Stateless, easy horizontal scaling
- RDS: Read replicas (future)

### Vertical Scaling
- RDS: Upgrade instance type
- Workers: Increase CPU/memory allocation

### Cost Optimization
- Use Fargate Spot for workers
- S3 lifecycle policies
- CloudFront caching
- RDS reserved instances (if stable)
