# =============================================================================
# Bootstrap - creates the Terraform S3 backend state bucket + DynamoDB lock
# table that the root module's `backend "s3"` block requires.
#
# Run ONCE before the first `make deploy`:
#     make bootstrap
#
# Uses a LOCAL backend (state kept in this folder) to avoid the chicken-and-egg
# problem of storing state in a bucket that doesn't exist yet. The resource
# names here MUST match the backend block in ../main.tf.
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Local backend (default) - do not configure the S3 backend here.
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "Region for the Terraform state backend (must match ../main.tf backend)"
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "S3 bucket for Terraform state. Leave empty to auto-name it modernization-factory-tfstate-<account-id> (S3 names are globally unique)."
  type        = string
  default     = ""
}

variable "lock_table_name" {
  description = "DynamoDB lock table name (must match the backend block in ../main.tf)"
  type        = string
  default     = "modernization-factory-locks"
}

data "aws_caller_identity" "current" {}

locals {
  # S3 bucket names are globally unique, so suffix the default with the account
  # ID. This lets the sample be bootstrapped into any account without collision.
  state_bucket = var.state_bucket_name != "" ? var.state_bucket_name : "modernization-factory-tfstate-${data.aws_caller_identity.current.account_id}"
}

# --- Customer-managed KMS key for the state bucket + lock table ---
resource "aws_kms_key" "backend" {
  description             = "modernization-factory Terraform backend encryption key"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  # Explicit least-privilege key policy (account root only). Without this, the
  # key falls back to the default policy, which scanners flag as too permissive.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      }
    ]
  })

  tags = {
    Project   = "modernization-factory"
    Component = "tf-backend"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_kms_alias" "backend" {
  name          = "alias/modernization-factory-tfstate"
  target_key_id = aws_kms_key.backend.key_id
}

# --- S3 bucket for Terraform state ---
resource "aws_s3_bucket" "tfstate" {
  bucket = local.state_bucket

  # Guard against accidental deletion of the state bucket.
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Project   = "modernization-factory"
    Component = "tf-backend"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.backend.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- DynamoDB table for state locking ---
resource "aws_dynamodb_table" "locks" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.backend.arn
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project   = "modernization-factory"
    Component = "tf-backend"
    ManagedBy = "terraform-bootstrap"
  }
}

output "state_bucket" {
  description = "Name of the created Terraform state S3 bucket"
  value       = aws_s3_bucket.tfstate.bucket
}

output "lock_table" {
  description = "Name of the created DynamoDB state-lock table"
  value       = aws_dynamodb_table.locks.name
}

# Copy this into the (commented-out) backend "s3" block in ../main.tf, then run
# `make deploy`. S3 backends can't use variables, so these must be literals.
output "backend_config" {
  description = "Ready-to-paste backend \"s3\" block for ../main.tf"
  value       = <<-EOT
    backend "s3" {
      bucket         = "${aws_s3_bucket.tfstate.bucket}"
      key            = "factory/terraform.tfstate"
      region         = "${var.aws_region}"
      dynamodb_table = "${aws_dynamodb_table.locks.name}"
      encrypt        = true
    }
  EOT
}
