# EventBridge — Reconcile + Scan Stuck rules → Batch

data "aws_iam_role" "eventbridge_batch" {
  name = "academy-v1-eventbridge-batch-video-role"
}

resource "aws_cloudwatch_event_rule" "reconcile" {
  name                = "${var.naming_prefix}-reconcile-video-jobs"
  description         = "Reconcile video batch jobs"
  schedule_expression  = "rate(15 minutes)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_rule" "scan_stuck" {
  name                = "${var.naming_prefix}-video-scan-stuck-rate"
  description         = "Scan stuck video jobs"
  schedule_expression = "rate(5 minutes)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "reconcile" {
  rule      = aws_cloudwatch_event_rule.reconcile.name
  target_id = "BatchReconcile"
  arn       = aws_batch_job_queue.ops.arn
  role_arn  = data.aws_iam_role.eventbridge_batch.arn

  batch_target {
    job_definition = "academy-v1-video-ops-reconcile"
    job_name       = "reconcile-scheduled"
  }
}

resource "aws_cloudwatch_event_target" "scan_stuck" {
  rule      = aws_cloudwatch_event_rule.scan_stuck.name
  target_id = "BatchScanStuck"
  arn       = aws_batch_job_queue.ops.arn
  role_arn  = data.aws_iam_role.eventbridge_batch.arn

  batch_target {
    job_definition = "academy-v1-video-ops-scanstuck"
    job_name       = "scanstuck-scheduled"
  }
}
