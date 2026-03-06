# Architecture Diagram — Mermaid

```mermaid
flowchart TB
    subgraph Internet
        Client[Client]
    end

    subgraph VPC["VPC: academy-v1-vpc"]
        subgraph Public["Public Subnets"]
            ALB[ALB<br/>academy-v1-api-alb]
            API1[API Instance]
            MW[Messaging Worker]
            AW[AI Worker]
        end

        subgraph Private["Private Subnets"]
            RDS[(RDS<br/>academy-db)]
            Redis[(Redis<br/>academy-v1-redis)]
            BatchCE[Batch CE<br/>video + ops]
        end

        subgraph SG["Security Groups"]
            api_sg[api-sg]
            worker_sg[worker-sg]
            batch_sg[batch-sg]
            rds_sg[rds-sg]
            redis_sg[redis-sg]
        end
    end

    subgraph Queues["Queues (SQS)"]
        MQ[messaging-queue]
        AQ[ai-queue]
    end

    subgraph Batch["Batch"]
        VQ[video-batch-queue]
        OQ[video-ops-queue]
    end

    subgraph EventBridge["EventBridge"]
        EB1[reconcile 15m]
        EB2[scan-stuck 5m]
    end

    subgraph Storage["Storage"]
        Dyn[(DynamoDB)]
        R2[R2]
    end

    Client --> HTTP[HTTP 80]
    HTTP --> ALB
    ALB --> API1
    API1 --> api_sg
    MW --> worker_sg
    AW --> worker_sg
    API1 --> RDS
    API1 --> Redis
    API1 --> MQ
    API1 --> AQ
    MW --> MQ
    AW --> AQ
    API1 --> VQ
    VQ --> BatchCE
    VQ --> batch_sg
    EB1 --> OQ
    EB2 --> OQ
    OQ --> BatchCE
    BatchCE --> R2
    BatchCE --> Dyn
    RDS --> rds_sg
    Redis --> redis_sg
```

---

## Simplified Flow

```
Client → ALB → API ASG (EC2)
         ↓
    API → RDS, Redis, SQS, Batch
         ↓
    Workers (ASG) ← SQS (messaging, ai)
         ↓
    Batch (CE) ← EventBridge (reconcile, scan-stuck)
         ↓
    R2 (Cloudflare)
```
