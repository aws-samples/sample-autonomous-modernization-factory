# =============================================================================
# Module: Storage - Artifacts, Results, and State Tracking
# =============================================================================

# --- S3: Source/Transformed Code Artifacts ---
resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.project_name}-artifacts-${var.environment}-${data.aws_caller_identity.current.account_id}"

  tags = {
    Component = "storage"
    Purpose   = "source-and-transformed-code"
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

# Block all public access to the artifacts bucket (customer source + transformed code).
resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "archive-old-versions"
    status = "Enabled"

    # Apply to all objects in the bucket.
    filter {}

    # Clean up incomplete multipart uploads to avoid indefinite storage cost (CKV_AWS_300).
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# --- S3: Build/Test Results ---
resource "aws_s3_bucket" "results" {
  bucket = "${var.project_name}-results-${var.environment}-${data.aws_caller_identity.current.account_id}"

  tags = {
    Component = "storage"
    Purpose   = "build-test-results"
  }
}

resource "aws_s3_bucket_versioning" "results" {
  bucket = aws_s3_bucket.results.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

# Block all public access to the results bucket (build/test output).
resource "aws_s3_bucket_public_access_block" "results" {
  bucket                  = aws_s3_bucket.results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    # Apply to all objects in the bucket.
    filter {}

    # Clean up incomplete multipart uploads to avoid indefinite storage cost (CKV_AWS_300).
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# --- DynamoDB: Application Modernization State ---
resource "aws_dynamodb_table" "state" {
  name         = "${var.project_name}-state-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "app_id"
  range_key    = "run_id"

  attribute {
    name = "app_id"
    type = "S"
  }

  attribute {
    name = "run_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "complexity_tier"
    type = "S"
  }

  global_secondary_index {
    name            = "status-index"
    hash_key        = "status"
    range_key       = "app_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "complexity-index"
    hash_key        = "complexity_tier"
    range_key       = "app_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  # TTL for web-app run records - retained 30 days after terminal status (Req 9.7).
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Component = "storage"
    Purpose   = "modernization-state-tracking"
  }
}

# --- Data Sources ---
data "aws_caller_identity" "current" {}
