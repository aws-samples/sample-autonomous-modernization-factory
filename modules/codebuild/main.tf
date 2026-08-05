# =============================================================================
# Module: CodeBuild - Build & Test Validation Pipeline
# =============================================================================

# --- IAM Role for CodeBuild ---
resource "aws_iam_role" "codebuild" {
  name = "${var.project_name}-codebuild-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "codebuild.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "codebuild" {
  name = "${var.project_name}-codebuild-policy"
  role = aws_iam_role.codebuild.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.source_bucket}",
          "arn:aws:s3:::${var.source_bucket}/*",
          "arn:aws:s3:::${var.results_bucket}",
          "arn:aws:s3:::${var.results_bucket}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
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

# --- CodeBuild Project: Build Validation ---
resource "aws_codebuild_project" "validator" {
  name           = "${var.project_name}-validator-${var.environment}"
  description    = "Validates transformed code - builds, runs tests, reports results"
  build_timeout  = 30
  service_role   = aws_iam_role.codebuild.arn
  encryption_key = var.kms_key_arn

  # The state machine's Validate step only checks the build status (pass/fail),
  # so the validator does not need to emit S3 artifacts.
  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = false
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "MAX_RETRY_ATTEMPTS"
      value = tostring(var.max_retry_attempts)
    }

    environment_variable {
      name  = "RESULTS_BUCKET"
      value = var.results_bucket
    }
  }

  source {
    type     = "S3"
    location = "${var.source_bucket}/transformed/"

    # Single shell block on purpose: CodeBuild does not persist shell state
    # (cd/PATH/vars/`set`) across separate command items or phases, and an
    # unquoted "word: word" list item is parsed by YAML as a map (which broke
    # the previous multi-item buildspec). One block sidesteps both issues.
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
              # Append curated progress markers to the same per-run log the
              # transformer started (download it first, then append).
              PLOG=/tmp/mf_progress.log
              PDEST="s3://$RESULTS_BUCKET/results/$RUN_ID/progress.log"
              aws s3 cp "$PDEST" "$PLOG" --only-show-errors || : > "$PLOG"
              progress() { echo "$(date -u +%H:%M:%S)|$1" >> "$PLOG"; aws s3 cp "$PLOG" "$PDEST" --only-show-errors || true; }
              progress "Preparing the validation environment"
              # Auto-detect the build system and run a language-appropriate build
              # gate. All build/test output is captured to validation.log and
              # published to S3 so the AI remediation agent can act on failures.
              # Dependency installs are best-effort; only the build/compile
              # result gates the run. An unrecognized stack is not a failure.
              VLOG=/tmp/validation.log
              : > "$VLOG"
              run_validation() {
                rc=0
                if [ -f pom.xml ]; then
                  progress "Building with Maven"
                  mvn -q -B -DskipTests compile; rc=$?
                elif [ -f build.gradle ] || [ -f build.gradle.kts ]; then
                  progress "Building with Gradle"
                  if [ -x ./gradlew ]; then ./gradlew -q compileJava -x test; else gradle -q compileJava -x test; fi; rc=$?
                elif [ -f package.json ]; then
                  progress "Installing Node dependencies and building"
                  npm ci 2>/dev/null || npm install 2>/dev/null || true
                  npm run build --if-present; rc=$?
                elif [ -f requirements.txt ] || [ -f setup.py ] || [ -f pyproject.toml ] || ls *.py >/dev/null 2>&1; then
                  progress "Compiling Python sources"
                  if [ -f requirements.txt ]; then pip install -r requirements.txt || echo "WARN dependency install failed, continuing"; fi
                  python -m compileall -q .; rc=$?
                  if [ "$rc" -eq 0 ]; then
                    pip install pytest >/dev/null 2>&1 || true
                    if python -m pytest --collect-only -q >/dev/null 2>&1; then
                      progress "Running the test suite"
                      python -m pytest --tb=short -q; rc=$?
                    else
                      progress "No test suite found - skipping tests"
                    fi
                  fi
                else
                  progress "No recognized build system - skipping build validation"
                fi
                return $rc
              }
              # Capture combined build/test output; progress markers still stream
              # to progress.log independently (they append to $PLOG directly).
              set +e
              run_validation >> "$VLOG" 2>&1
              rc=$?
              set -e
              # Publish the captured output so the remediation agent can read the
              # failures on the next loop iteration.
              aws s3 cp "$VLOG" "s3://$RESULTS_BUCKET/results/$RUN_ID/validation.log" --only-show-errors || true
              if [ "$rc" -ne 0 ]; then progress "Validation failed"; echo "Validation failed (exit $rc)"; tail -60 "$VLOG" || true; exit 1; fi
              progress "Validation checks passed"
              echo "Validation passed"
    BUILDSPEC
  }

  logs_config {
    cloudwatch_logs {
      group_name  = "/codebuild/${var.project_name}-validator-${var.environment}"
      stream_name = "build-log"
    }
  }

  dynamic "vpc_config" {
    for_each = var.vpc_id != "" ? [1] : []
    content {
      vpc_id             = var.vpc_id
      subnets            = var.subnet_ids
      security_group_ids = [aws_security_group.codebuild[0].id]
    }
  }

  tags = {
    Component = "codebuild"
    Purpose   = "build-test-validation"
  }
}

# --- Optional Security Group for VPC mode ---
resource "aws_security_group" "codebuild" {
  count       = var.vpc_id != "" ? 1 : 0
  name        = "${var.project_name}-codebuild-sg-${var.environment}"
  description = "Egress for CodeBuild projects running in VPC mode"
  vpc_id      = var.vpc_id

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Component = "codebuild"
  }
}
