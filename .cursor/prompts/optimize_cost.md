# Optimize Cost

Use this prompt to propose cost optimizations without breaking reliability.

**Steps:**

1. Read 04_cost_engine, 03_scaling_logic, 05_reliability_engine.
2. Review current instance types (params.yaml, INFRA-AND-SPECS.md): API/Messaging/AI = t4g.medium, Video batch = c6g.large.
3. Identify: over-provisioned ASG/Batch, unused resources, right-sizing opportunities.
4. Constraint: max concurrency 10; ASG max = 10, Batch maxvCpus = 10; medium reliability (minSize=1, Multi-AZ).
5. Prefer ARM (t4g, c6g). Avoid m5/c5/large fleets.
6. Propose changes as script/params edits; no ECS/EKS/S3.

**Output:** Concrete recommendations with file paths and code snippets; estimate impact.
