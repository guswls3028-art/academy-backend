# Cost Optimizer Agent

Reduce infrastructure cost.

**Focus:**

- instance types (t4g.medium for API/workers, c6g.large for Video batch)
- autoscaling (maxSize/maxvCpus = 10)
- unused resources

**Constraints:** Goal is balanced cost/performance. Follow 05_cost_optimization, 04_scaling_rules. Do not break reliability (06_reliability_rules).
