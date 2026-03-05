# Optimize Cost

Use this prompt to propose cost optimizations without breaking reliability.

**Steps:**

1. Read 05_cost_optimization, 04_scaling_rules, 06_reliability_rules.
2. Review current instance types (params.yaml, INFRA-AND-SPECS.md): API/build/workers = t4g.medium, Video batch = c6g.large.
3. Identify: over-provisioned ASG/Batch, unused resources, right-sizing opportunities.
4. Constraint: max concurrency 10; ASG maxSize = 10, Batch maxvCpus = 10; medium reliability (minSize=1, Multi-AZ).
5. Propose changes as script/params edits; no ECS/EKS/S3.

**Output:** Concrete recommendations with file paths and code snippets; estimate impact (e.g. instance count or vCPU change).
