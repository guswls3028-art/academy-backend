# Cost Forecast

## Overview

Cost estimates for production deployment at different scales. All costs are monthly estimates in USD.

## Initial Deployment (500 DAU)

### Cost Breakdown

| Item | Specification | Monthly Cost |
|------|--------------|--------------|
| **Frontend** | | |
| S3 (Static) | 1GB storage | $1 |
| CloudFront | 10GB transfer | $5 |
| **API Server** | | |
| ECS Fargate | 0.5 vCPU, 1GB × 2 | $30 |
| ALB | Application Load Balancer | $20 |
| **Database** | | |
| RDS PostgreSQL | db.t4g.micro (Single AZ) | $15 |
| **Storage** | | |
| S3 (Media) | 100GB storage | $10 |
| CloudFront (Media) | 50GB transfer | $15 |
| **Queue** | | |
| SQS | ~100K requests/month | $2 |
| **Workers** | | |
| Video Worker | Fargate Spot, 0.5 vCPU, 1GB | $5 |
| AI Worker CPU | Fargate Spot, 1 vCPU, 2GB | $10 |
| **Monitoring** | | |
| CloudWatch Logs | 5GB/month | $1 |
| CloudWatch Metrics | Basic metrics | $0 |
| SNS | Alert delivery | $0.50 |
| **Total** | | **~$114/month** |

### Alternative: EC2 Deployment

| Item | Specification | Monthly Cost |
|------|--------------|--------------|
| API Server | t4g.small × 2 | $30 |
| Video Worker | t4g.small × 1 | $15 |
| AI Worker CPU | t4g.medium × 1 | $30 |
| ALB | Application Load Balancer | $20 |
| RDS | db.t4g.micro | $15 |
| S3 + CloudFront | Same as above | $26 |
| SQS | Same as above | $2 |
| CloudWatch | Same as above | $1.50 |
| **Total** | | **~$140/month** |

**Savings with Fargate Spot:** ~$26/month

## Target Scale (10k DAU)

### Cost Breakdown

| Item | Specification | Monthly Cost |
|------|--------------|--------------|
| **Frontend** | | |
| S3 (Static) | 5GB storage | $5 |
| CloudFront | 500GB transfer | $50 |
| **API Server** | | |
| ECS Fargate | 0.5 vCPU, 1GB × 4 | $60 |
| ALB | Application Load Balancer | $20 |
| **Database** | | |
| RDS PostgreSQL | db.t4g.medium (Multi-AZ) | $80 |
| PgBouncer | t4g.small (optional) | $15 |
| **Storage** | | |
| S3 (Media) | 1TB storage | $100 |
| CloudFront (Media) | 2TB transfer | $200 |
| **Queue** | | |
| SQS | ~1M requests/month | $10 |
| **Workers** | | |
| Video Worker | Fargate Spot × 2 | $10 |
| AI Worker CPU | Fargate Spot × 2 | $20 |
| AI Worker GPU | g4dn.xlarge × 1 (future) | $200 |
| **Monitoring** | | |
| CloudWatch Logs | 50GB/month | $10 |
| CloudWatch Metrics | Basic metrics | $0 |
| SNS | Alert delivery | $0.50 |
| **Total** | | **~$780/month** |

### Without GPU Worker

**Total:** ~$580/month

## Cost Optimization Strategies

### 1. Use Fargate Spot for Workers

**Savings:** 70% reduction on worker costs
- Video Worker: $5/month (vs $15/month)
- AI Worker CPU: $10/month (vs $30/month)

### 2. S3 Lifecycle Policies

**Savings:** ~30% reduction on storage costs
- IA after 30 days: ~$0.0125/GB/month (vs $0.023/GB/month)
- Glacier after 90 days: ~$0.004/GB/month

### 3. CloudFront Caching

**Savings:** Reduced origin requests
- Cache hit ratio: 80-90%
- Reduced S3 requests: ~80% savings

### 4. RDS Reserved Instances

**Savings:** ~30% reduction (1-year term)
- db.t4g.medium: $56/month (vs $80/month)

### 5. ECS Spot Instances

**Savings:** ~70% reduction on compute costs
- Workers only (not API servers)

## Cost Scaling Factors

### API Requests
- 500 DAU: ~10K requests/day = ~300K/month
- 10k DAU: ~200K requests/day = ~6M/month
- **Cost Impact:** Minimal (ALB fixed cost)

### Database
- 500 DAU: db.t4g.micro sufficient
- 10k DAU: db.t4g.medium required
- **Cost Impact:** +$65/month

### Storage
- 500 DAU: ~100GB media
- 10k DAU: ~1TB media
- **Cost Impact:** +$90/month

### CloudFront Transfer
- 500 DAU: ~50GB/month
- 10k DAU: ~2TB/month
- **Cost Impact:** +$185/month

### Workers
- 500 DAU: 1 worker each
- 10k DAU: 2 workers each
- **Cost Impact:** +$15/month (with Spot)

## Budget Alerts

**Recommended Thresholds:**
- Warning: $150/month (500 DAU)
- Critical: $200/month (500 DAU)
- Warning: $800/month (10k DAU)
- Critical: $1000/month (10k DAU)

**Setup:**
```bash
aws budgets create-budget \
  --account-id <account-id> \
  --budget '{
    "BudgetName": "academy-monthly-budget",
    "BudgetLimit": {"Amount": "200", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }' \
  --notifications-with-subscribers '[
    {
      "Notification": {
        "NotificationType": "ACTUAL",
        "ComparisonOperator": "GREATER_THAN",
        "Threshold": 80
      },
      "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "admin@example.com"}]
    }
  ]'
```

## Cost Monitoring

**CloudWatch Cost Explorer:**
- View costs by service
- View costs by time period
- Forecast future costs

**Key Metrics:**
- Daily cost
- Cost by service
- Cost trends

## Cost Comparison: EC2 vs ECS Fargate

### EC2 (Reserved Instances)

**500 DAU:**
- t4g.small × 3 (1-year term): ~$30/month
- **Total:** ~$140/month

**10k DAU:**
- t4g.medium × 4 (1-year term): ~$80/month
- **Total:** ~$580/month

### ECS Fargate

**500 DAU:**
- Fargate × 2 + Spot × 2: ~$45/month
- **Total:** ~$114/month

**10k DAU:**
- Fargate × 4 + Spot × 4: ~$90/month
- **Total:** ~$780/month

**Recommendation:**
- 500 DAU: ECS Fargate (simpler, cost-effective)
- 10k DAU: EC2 Reserved Instances (better cost control)
