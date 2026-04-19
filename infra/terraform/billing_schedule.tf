# ──────────────────────────────────────────────
# Billing daily batch — process_billing
#
# Scheduling: 매일 00:05 KST = 15:05 UTC
# Trigger: EventBridge → SSM RunCommand → API EC2(docker exec)
#
# 설계 이유:
#  - 기존 Batch 잡은 video-worker 이미지 기반. API 이미지로 별도 잡 정의를 만드는 건
#    빌링 한 건을 위해 과도함.
#  - academy-api 컨테이너는 ASG로 항상 떠 있으므로 SSM RunCommand로 exec.
#  - Tag(Name=academy-v1-api)로 타겟 지정. ASG 여러 인스턴스가 있더라도
#    `--max-concurrency 1` + idempotent 로직으로 안전.
# ──────────────────────────────────────────────

resource "aws_iam_role" "eventbridge_ssm_billing" {
  name = "${var.naming_prefix}-eventbridge-ssm-billing-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "eventbridge_ssm_billing" {
  name = "${var.naming_prefix}-eventbridge-ssm-billing-policy"
  role = aws_iam_role.eventbridge_ssm_billing.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:SendCommand",
        ]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:*:document/AWS-RunShellScript",
          "arn:aws:ec2:${var.aws_region}:*:instance/*",
        ]
      }
    ]
  })
}

resource "aws_cloudwatch_event_rule" "process_billing" {
  name                = "${var.naming_prefix}-process-billing"
  description         = "Daily billing batch at 00:05 KST"
  schedule_expression = "cron(5 15 * * ? *)" # 15:05 UTC = 00:05 KST
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "process_billing" {
  rule      = aws_cloudwatch_event_rule.process_billing.name
  target_id = "SsmRunCommandProcessBilling"
  arn       = "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"
  role_arn  = aws_iam_role.eventbridge_ssm_billing.arn

  run_command_targets {
    key    = "tag:Name"
    values = ["academy-v1-api"]
  }

  input = jsonencode({
    commands = [
      "docker exec academy-api python manage.py process_billing"
    ]
  })
}
