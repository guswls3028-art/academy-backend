# ──────────────────────────────────────────────
# Messaging scheduled notification batch — process_scheduled_notifications
#
# Scheduling: every minute
# Trigger: EventBridge → SSM RunCommand → API EC2(docker exec)
#
# This drains ScheduledNotification rows created by AutoSendConfig.delay_mode
# such as video_encoding_complete "N분 후" / "지정 시각" delivery.
# ──────────────────────────────────────────────

resource "aws_iam_role" "eventbridge_ssm_messaging" {
  name = "${var.naming_prefix}-eventbridge-ssm-messaging-role"

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

resource "aws_iam_role_policy" "eventbridge_ssm_messaging" {
  name = "${var.naming_prefix}-eventbridge-ssm-messaging-policy"
  role = aws_iam_role.eventbridge_ssm_messaging.id

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

resource "aws_cloudwatch_event_rule" "process_scheduled_notifications" {
  name                = "${var.naming_prefix}-process-scheduled-notifications"
  description         = "Drain due ScheduledNotification rows into the messaging SQS queue"
  schedule_expression = "rate(1 minute)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "process_scheduled_notifications" {
  rule      = aws_cloudwatch_event_rule.process_scheduled_notifications.name
  target_id = "SsmRunCommandProcessScheduledNotifications"
  arn       = "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"
  role_arn  = aws_iam_role.eventbridge_ssm_messaging.arn

  run_command_targets {
    key    = "tag:Name"
    values = ["academy-v1-api"]
  }

  input = jsonencode({
    commands = [
      "docker exec academy-api python manage.py process_scheduled_notifications --batch-size 100"
    ]
  })
}
