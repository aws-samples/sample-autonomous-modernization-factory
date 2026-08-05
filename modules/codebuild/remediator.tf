# =============================================================================
# CodeBuild: Remediator - AI auto-fix stage of the closed loop
# On a failed validation, this project asks Amazon Bedrock (Claude) to fix the
# transformed code, applies the fixes, and re-packages modernized.zip so the
# state machine can re-validate. Exhausted/unfixable runs escalate to a human.
# =============================================================================

# --- IAM role for the remediator build ---
resource "aws_iam_role" "remediator" {
  name = "${var.project_name}-remediator-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "remediator" {
  name = "${var.project_name}-remediator-policy"
  role = aws_iam_role.remediator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Read the remediation script from the source bucket.
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::${var.source_bucket}",
          "arn:aws:s3:::${var.source_bucket}/scripts/*"
        ]
      },
      {
        # Read the current modernized.zip + validation log; write the fixed zip.
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["arn:aws:s3:::${var.results_bucket}/results/*"]
      },
      {
        # The remediation agent reasons with a Bedrock foundation model. On-demand
        # access to newer Claude models is via a cross-region inference profile.
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:*:inference-profile/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        # Use the shared CMK that encrypts this CodeBuild project.
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:ReEncrypt*", "kms:DescribeKey"]
        Resource = var.kms_key_arn
      }
    ]
  })
}

# --- Ship the (stdlib + boto3) remediation agent into the sandbox via S3 ---
resource "aws_s3_object" "remediate_script" {
  bucket = var.source_bucket
  key    = "scripts/remediate.py"
  source = "${path.module}/../../src/remediate.py"
  etag   = filemd5("${path.module}/../../src/remediate.py")
}

# --- Remediator CodeBuild project ---
resource "aws_codebuild_project" "remediator" {
  name           = "${var.project_name}-remediator-${var.environment}"
  description    = "AI auto-remediation: fixes failed validations via Bedrock and re-packages the code"
  build_timeout  = 20
  service_role   = aws_iam_role.remediator.arn
  encryption_key = var.kms_key_arn

  artifacts {
    type = "NO_ARTIFACTS" # writes the fixed modernized.zip to S3 itself
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = false
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "SOURCE_BUCKET"
      value = var.source_bucket
    }
    environment_variable {
      name  = "RESULTS_BUCKET"
      value = var.results_bucket
    }
    environment_variable {
      name  = "BEDROCK_MODEL_ID"
      value = var.bedrock_model_id
    }
    environment_variable {
      name  = "REMEDIATE_SCRIPT_KEY"
      value = aws_s3_object.remediate_script.key
    }
  }

  source {
    type     = "S3"
    location = "${var.results_bucket}/results/"

    # The state machine overrides SourceLocationOverride to the run's
    # results/<run_id>/modernized.zip and injects RUN_ID. Single shell block:
    # CodeBuild does not persist cd/PATH/vars across command items.
    buildspec = <<-BUILDSPEC
      version: 0.2
      phases:
        install:
          runtime-versions:
            python: 3.12
        build:
          commands:
            - |
              set -e
              PLOG=/tmp/mf_progress.log
              PDEST="s3://$RESULTS_BUCKET/results/$RUN_ID/progress.log"
              aws s3 cp "$PDEST" "$PLOG" --only-show-errors || : > "$PLOG"
              progress() { echo "$(date -u +%H:%M:%S)|$1" >> "$PLOG"; aws s3 cp "$PLOG" "$PDEST" --only-show-errors || true; }
              progress "Auto-remediating with AI (analyzing the build failures)"
              pip install --quiet boto3 >/dev/null 2>&1 || true
              aws s3 cp "s3://$SOURCE_BUCKET/$REMEDIATE_SCRIPT_KEY" remediate.py --only-show-errors
              aws s3 cp "s3://$RESULTS_BUCKET/results/$RUN_ID/validation.log" validation.log --only-show-errors || : > validation.log
              set +e
              REMEDIATE_ROOT="$PWD" python3 remediate.py
              rc=$?
              set -e
              if [ "$rc" -ne 0 ]; then progress "No automated fix available - routing to human review"; echo "remediation produced no fix (exit $rc)"; exit 1; fi
              progress "Applied automated fixes - re-validating"
              # Re-package the fixed tree (build the archive in /tmp to avoid
              # self-inclusion) and overwrite the run's modernized.zip.
              rm -f remediate.py validation.log
              zip -qr /tmp/modernized.zip . -x '.git/*' 'remediate.py' 'validation.log' '*/__pycache__/*' '__pycache__/*' '*.pyc'
              aws s3 cp /tmp/modernized.zip "s3://$RESULTS_BUCKET/results/$RUN_ID/modernized.zip" --only-show-errors
    BUILDSPEC
  }

  logs_config {
    cloudwatch_logs {
      group_name  = "/codebuild/${var.project_name}-remediator-${var.environment}"
      stream_name = "build-log"
    }
  }

  tags = {
    Component = "codebuild"
    Purpose   = "ai-auto-remediation"
  }
}
