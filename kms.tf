# =============================================================================
# Shared customer-managed KMS key
# Encrypts DynamoDB, CodeBuild projects, ECR, and CloudWatch log groups so the
# stack uses a CMK (with rotation) rather than AWS-owned/managed keys.
# =============================================================================

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_kms_key" "main" {
  description             = "${var.project_name}-${var.environment} shared encryption key"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  # Root account retains full control; CloudWatch Logs is granted use of the key
  # for log-group encryption (it requires an explicit key-policy grant, unlike
  # DynamoDB/CodeBuild/ECR which use the key via the calling principal).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${data.aws_region.current.name}.amazonaws.com" }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:*"
          }
        }
      }
    ]
  })

  tags = {
    Component = "security"
    Purpose   = "shared-encryption"
  }
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.project_name}-${var.environment}"
  target_key_id = aws_kms_key.main.key_id
}
