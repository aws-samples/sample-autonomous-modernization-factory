# =============================================================================
# Module: Web App - ECR (first) -> build/push image -> ECS/Fargate + ALB
# ECR-first ordered deploy (Task 13): a single apply provisions the repo,
# builds and pushes the image (depends_on repo), then the service (depends_on
# the push). Docker must be available where `terraform apply` runs.
# =============================================================================

locals {
  name = "${var.project_name}-webapp-${var.environment}"
}

data "aws_caller_identity" "current" {}

# Resolve the ALB ingress CIDRs. When allowed_web_cidrs is left empty the ALB
# is restricted to the VPC CIDR (private access only) - deployers must opt in
# to wider access by setting allowed_web_cidrs explicitly.
data "aws_vpc" "selected" {
  id = var.vpc_id
}

locals {
  web_ingress_cidrs = length(var.allowed_web_cidrs) > 0 ? var.allowed_web_cidrs : [data.aws_vpc.selected.cidr_block]
}

# --------------------------------------------------------------------------- #
# 1) ECR repository
# --------------------------------------------------------------------------- #
resource "aws_ecr_repository" "api" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }

  tags = { Component = "webapp" }
}

locals {
  image_uri = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
}

# --------------------------------------------------------------------------- #
# 2) Build & push the image (ordered after the ECR repo exists)
# --------------------------------------------------------------------------- #
resource "null_resource" "image_push" {
  triggers = {
    image_uri  = local.image_uri
    dockerfile = filemd5("${var.context_dir}/Dockerfile")
    # Rebuild when the API source or the frontend changes.
    src_hash      = sha1(join("", [for f in fileset("${var.context_dir}/src", "**") : filemd5("${var.context_dir}/src/${f}")]))
    frontend_hash = sha1(join("", [for f in fileset("${var.context_dir}/frontend", "**") : filemd5("${var.context_dir}/frontend/${f}")]))
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      REGISTRY="${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
      aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin "$REGISTRY"
      docker build --platform linux/amd64 -t "${local.image_uri}" "${var.context_dir}"
      docker push "${local.image_uri}"
    EOT
  }

  depends_on = [aws_ecr_repository.api]
}

# --------------------------------------------------------------------------- #
# 3) IAM: task execution role (ECR pull + logs) and task role (app perms)
# --------------------------------------------------------------------------- #
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# App permissions (Task 13.5): scoped S3, CodeBuild start/describe, DynamoDB.
resource "aws_iam_role_policy" "task" {
  name = "${local.name}-task-policy"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [
          var.artifacts_bucket_arn, "${var.artifacts_bucket_arn}/*",
          var.results_bucket_arn, "${var.results_bucket_arn}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["codebuild:StartBuild", "codebuild:BatchGetBuilds"]
        Resource = "arn:aws:codebuild:${var.aws_region}:${data.aws_caller_identity.current.account_id}:project/${var.transformer_project_name}"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:Query", "dynamodb:DeleteItem"
        ]
        Resource = [var.state_table_arn, "${var.state_table_arn}/index/*"]
      },
      {
        # Access the CMK that encrypts the DynamoDB state table.
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = var.kms_key_arn
      },
      # Tier 1: drive the closed-loop pipeline via Step Functions.
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = var.state_machine_arn != "" ? var.state_machine_arn : "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:no-op"
      },
      {
        Effect = "Allow"
        Action = ["states:DescribeExecution", "states:StopExecution", "states:GetExecutionHistory"]
        # Execution ARNs are the state-machine ARN with :execution:<name> appended.
        Resource = var.state_machine_arn != "" ? "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:execution:${element(split(":", var.state_machine_arn), 6)}:*" : "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:execution:no-op:*"
      }
    ]
  })
}

# --------------------------------------------------------------------------- #
# 4) Networking: security groups
# --------------------------------------------------------------------------- #
resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "Ingress to the public ALB for the modernization web app"
  vpc_id      = var.vpc_id

  # Create the replacement SG before destroying the old one so the ALB/service
  # references migrate cleanly (avoids DependencyViolation on delete).
  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description = "HTTP (redirected to HTTPS) from allowed CIDRs (defaults to the VPC CIDR)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = local.web_ingress_cidrs
  }
  ingress {
    description = "HTTPS from allowed CIDRs (defaults to the VPC CIDR)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = local.web_ingress_cidrs
  }
  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Component = "webapp" }
}

resource "aws_security_group" "service" {
  name_prefix = "${local.name}-svc-"
  description = "Ingress to the Fargate service from the ALB only"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description     = "ALB to container"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Component = "webapp" }
}

# --------------------------------------------------------------------------- #
# 5) ALB
# --------------------------------------------------------------------------- #
resource "aws_lb" "this" {
  name                       = "${var.project_name}-web-${var.environment}"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = var.public_subnet_ids
  drop_invalid_header_fields = true
  enable_deletion_protection = var.alb_deletion_protection
  tags                       = { Component = "webapp" }
}

# ---------------------------------------------------------------------------
# TLS: a self-signed certificate so the ALB can serve HTTPS out of the box.
# A clone-and-run sample has no domain/ACM-issued cert, so we generate one.
# Browsers will warn about the self-signed cert; for production, replace this
# with an ACM-issued certificate for your domain.
# ---------------------------------------------------------------------------
resource "tls_private_key" "alb" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "alb" {
  private_key_pem = tls_private_key.alb.private_key_pem

  subject {
    common_name  = "${var.project_name}-${var.environment}.example.com"
    organization = "Modernization Factory (sample)"
  }

  validity_period_hours = 8760 # 1 year
  early_renewal_hours   = 720

  allowed_uses = ["key_encipherment", "digital_signature", "server_auth"]
}

resource "aws_acm_certificate" "alb" {
  private_key      = tls_private_key.alb.private_key_pem
  certificate_body = tls_self_signed_cert.alb.cert_pem

  tags = { Component = "webapp" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_target_group" "this" {
  name        = "${var.project_name}-web-${var.environment}"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/docs"
    matcher             = "200-399"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }
}

# HTTP listener redirects to HTTPS (no plaintext traffic is served).
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"
  certificate_arn   = aws_acm_certificate.alb.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }
}

# --------------------------------------------------------------------------- #
# 6) ECS cluster, task definition, service
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = 365
  kms_key_id        = var.kms_key_arn
}

resource "aws_ecs_cluster" "this" {
  name = local.name
}

resource "aws_ecs_task_definition" "this" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = local.image_uri
      essential = true
      portMappings = [{
        containerPort = var.container_port
        protocol      = "tcp"
      }]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "MF_ARTIFACTS_BUCKET", value = var.artifacts_bucket },
        { name = "MF_RESULTS_BUCKET", value = var.results_bucket },
        { name = "MF_STATE_TABLE", value = var.state_table },
        { name = "MF_TRANSFORMER_PROJECT", value = var.transformer_project_name },
        { name = "MF_TRANSFORM_TIMEOUT_MIN", value = tostring(var.transformation_timeout_min) },
        { name = "MF_STATE_MACHINE_ARN", value = var.state_machine_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
        }
      }
    }
  ])

  depends_on = [null_resource.image_push]
}

resource "aws_ecs_service" "this" {
  name            = local.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # Wait for the new deployment to reach steady state so tasks migrate onto the
  # replacement security group (releasing ENIs on the old SG) before Terraform
  # deletes it.
  wait_for_steady_state = true

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = "api"
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.https, aws_lb_listener.http, null_resource.image_push]
}
