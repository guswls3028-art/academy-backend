# AWS Infra Forensic Report (Full Copy-Paste)

Region: ap-northeast-2  |  OutDir: C:\academy\forensic_20260226

---
## 1. Network structure

| Item | Evidence file |
|------|---------------|
| VPC | 02_vpcs.json |
| Subnets | 02_subnets.json |
| Route Tables | 02_route_tables.json |
| NAT Gateways | 02_nat_gateways.json |
| Internet Gateways | 02_internet_gateways.json |
| VPC Endpoints | 02_vpc_endpoints.json |
| Security Groups | 02_security_groups.json |

## 2. Internet path (API / Build / Worker)

- API: 03_api_instances.json -> SubnetId -> 02_route_tables / 02_nat_gateways
- Build: 04_build_instances.json, 04_build_subnet_route_tables.json
- Worker: 05_batch_compute_environments.json -> subnets -> 02_route_tables

## 3. SSOT check list

- Video CE: academy-video-batch-ce-final, state ENABLED, status VALID, instanceTypes c6g.large only -> 05_batch_compute_environments.json
- Video Queue: single CE only -> 05_batch_job_queues.json
- JobDef: vcpus 2, memory 3072, retryStrategy attempts 1 -> 05_batch_job_definitions.json
- EventBridge reconcile: rate 15 minutes, target Ops Queue -> 07_eventbridge_*.json

## 4. Potential failure points

- Build: no 0.0.0.0/0 to nat or igw -> STS/ECR timeout
- Batch CE INVALID -> 05_batch_compute_environments.json statusReason
- ECS container instances 0 with desiredvCpus gt 0 -> RUNNABLE stuck

## 5. Rebuild needed?

Review JSON in this folder for sections 2-4.

---
## Evidence: 01_caller_identity.json

```json
{"UserId":"AIDA3Y572RZN7SEXGFCJP","Account":"809466760795","Arn":"arn:aws:iam::809466760795:user/admin97"}
```

---
## Evidence: 02_vpcs.json

(Re-run: `.\scripts\infra\infra_forensic_collect.ps1 -Region ap-northeast-2 -OutDir C:\academy\forensic_20260226` then paste file content here)

---
## Evidence: 02_subnets.json

(Re-run script then paste file content here)

---
## Evidence: 02_route_tables.json

(Re-run script then paste file content here)

---
## Evidence: 02_nat_gateways.json

(Re-run script then paste file content here)

---
## Evidence: 02_internet_gateways.json

(Re-run script then paste file content here)

---
## Evidence: 02_vpc_endpoints.json

(Re-run script then paste file content here)

---
## Evidence: 02_security_groups.json

(Re-run script then paste file content here)

---
## Evidence: 03_api_instances.json

(Re-run script then paste file content here)

---
## Evidence: 04_build_instances.json

(Re-run script then paste file content here)

---
## Evidence: 05_batch_compute_environments.json

(Re-run script then paste file content here)

---
## Evidence: 05_batch_job_queues.json

(Re-run script then paste file content here)

---
## Evidence: 05_batch_job_definitions.json

(Re-run script then paste file content here)

---
## Evidence: 07_eventbridge_reconcile.json

(Re-run script then paste file content here)

---
## Evidence: 08_ecr_video_worker_images.json

(Re-run script then paste file content here)

---
