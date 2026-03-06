# Worker ASGs — Messaging + AI (use existing IAM, SQS)

resource "aws_launch_template" "messaging_worker" {
  name_prefix   = "${var.naming_prefix}-messaging-worker-lt-"
  image_id      = data.aws_ami.amazon_linux_arm.id
  instance_type = "t4g.medium"

  vpc_security_group_ids = [aws_security_group.worker.id]
  iam_instance_profile {
    name = "academy-ec2-role"
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.naming_prefix}-messaging-worker"
    }
  }
}

resource "aws_launch_template" "ai_worker" {
  name_prefix   = "${var.naming_prefix}-ai-worker-lt-"
  image_id      = data.aws_ami.amazon_linux_arm.id
  instance_type = "t4g.medium"

  vpc_security_group_ids = [aws_security_group.worker.id]
  iam_instance_profile {
    name = "academy-ec2-role"
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.naming_prefix}-ai-worker"
    }
  }
}

resource "aws_autoscaling_group" "messaging_worker" {
  name                = "${var.naming_prefix}-messaging-worker-asg"
  vpc_zone_identifier = var.public_subnet_ids
  min_size            = 1
  max_size            = 3
  desired_capacity    = 1

  launch_template {
    id      = aws_launch_template.messaging_worker.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.naming_prefix}-messaging-worker"
    propagate_at_launch = true
  }
}

resource "aws_autoscaling_group" "ai_worker" {
  name                = "${var.naming_prefix}-ai-worker-asg"
  vpc_zone_identifier = var.public_subnet_ids
  min_size            = 1
  max_size            = 5
  desired_capacity    = 1

  launch_template {
    id      = aws_launch_template.ai_worker.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.naming_prefix}-ai-worker"
    propagate_at_launch = true
  }
}
