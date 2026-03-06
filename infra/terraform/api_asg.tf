# API ASG — EC2 + Launch Template

data "aws_ami" "amazon_linux_arm" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-arm64"]
  }
}

resource "aws_launch_template" "api" {
  name_prefix   = "${var.naming_prefix}-api-lt-"
  image_id      = data.aws_ami.amazon_linux_arm.id
  instance_type = "t4g.medium"

  vpc_security_group_ids = [aws_security_group.api.id]
  iam_instance_profile {
    name = data.aws_iam_instance_profile.api.instance_profile_name
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.naming_prefix}-api"
    }
  }
}

resource "aws_autoscaling_group" "api" {
  name                = "${var.naming_prefix}-api-asg"
  vpc_zone_identifier = var.private_subnet_ids
  min_size            = 1
  max_size            = 2
  desired_capacity    = 1

  target_group_arns = [aws_lb_target_group.api.arn]

  launch_template {
    id      = aws_launch_template.api.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.naming_prefix}-api"
    propagate_at_launch = true
  }
}

# IAM: Use existing academy-ec2-role (created by scripts/v1/resources/iam.ps1)
data "aws_iam_instance_profile" "api" {
  name = "academy-ec2-role"
}
