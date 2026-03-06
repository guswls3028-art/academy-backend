# ALB + Target Group for API

resource "aws_lb" "api" {
  name               = "${var.naming_prefix}-api-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  tags = {
    Name = "${var.naming_prefix}-api-alb"
  }
}

resource "aws_security_group" "alb" {
  name        = "${var.naming_prefix}-sg-alb"
  description = "ALB - HTTP 80 from internet"
  vpc_id      = local.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.naming_prefix}-sg-alb"
  }
}

resource "aws_lb_target_group" "api" {
  name        = "${var.naming_prefix}-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = local.vpc_id
  target_type = "instance"

  health_check {
    path                = "/healthz"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
  }

  tags = {
    Name = "${var.naming_prefix}-api-tg"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
