# ──────────────────────────────────────────────
# Clinic reminder batch — send_clinic_reminders
#
# Scheduling: every minute
# Trigger: EventBridge → SSM RunCommand → API EC2(docker exec)
#
# The command itself uses AutoSendConfig.minutes_before and a 5-minute grace
# window. Message delivery is protected by the messaging worker's business
# idempotency key, so duplicate API instances do not create duplicate sends.
# ──────────────────────────────────────────────

resource "aws_iam_role" "eventbridge_ssm_clinic" {
  name = "${var.naming_prefix}-eventbridge-ssm-clinic-role"

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

resource "aws_iam_role_policy" "eventbridge_ssm_clinic" {
  name = "${var.naming_prefix}-eventbridge-ssm-clinic-policy"
  role = aws_iam_role.eventbridge_ssm_clinic.id

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

resource "aws_cloudwatch_event_rule" "send_clinic_reminders" {
  name                = "${var.naming_prefix}-send-clinic-reminders"
  description         = "Send clinic reminders according to AutoSendConfig.minutes_before"
  schedule_expression = "rate(1 minute)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "send_clinic_reminders" {
  rule      = aws_cloudwatch_event_rule.send_clinic_reminders.name
  target_id = "SsmRunCommandSendClinicReminders"
  arn       = "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"
  role_arn  = aws_iam_role.eventbridge_ssm_clinic.arn

  run_command_targets {
    key    = "tag:Name"
    values = ["academy-v1-api"]
  }

  input = jsonencode({
    commands = [
      "docker exec academy-api python manage.py send_clinic_reminders --window-minutes 5"
    ]
  })
}
