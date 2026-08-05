# =============================================================================
# CodeBuild: Transformer - runs the AWS Transform CLI (atx) in a sandbox
# Requirement 11: the actual transformation runs here, never in the API tier.
# =============================================================================

# --- IAM role for the transformer build ---
resource "aws_iam_role" "transformer" {
  name = "${var.project_name}-transformer-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Least-privilege inline policy: S3 scoped to the run's source/result prefixes
# and CloudWatch Logs (Requirement 10.3, 10.5).
resource "aws_iam_role_policy" "transformer" {
  name = "${var.project_name}-transformer-policy"
  role = aws_iam_role.transformer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::${var.source_bucket}",
          "arn:aws:s3:::${var.source_bucket}/uploads/*",
          "arn:aws:s3:::${var.source_bucket}/scripts/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.results_bucket}/results/*"]
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

# AWS Transform custom execution permissions (managed policy).
resource "aws_iam_role_policy_attachment" "transformer_atx" {
  role       = aws_iam_role.transformer.name
  policy_arn = "arn:aws:iam::aws:policy/AWSTransformCustomExecuteTransformations"
}

# --- Ship the (stdlib-only) change-report builder into the sandbox via S3 ---
resource "aws_s3_object" "report_script" {
  bucket = var.source_bucket
  key    = "scripts/report.py"
  source = "${path.module}/../../src/report.py"
  etag   = filemd5("${path.module}/../../src/report.py")
}

# --- Transformer CodeBuild project ---
resource "aws_codebuild_project" "transformer" {
  name           = "${var.project_name}-transformer-${var.environment}"
  description    = "Runs the AWS Transform CLI (atx) against an uploaded codebase in an isolated sandbox"
  build_timeout  = var.transform_build_timeout
  service_role   = aws_iam_role.transformer.arn
  encryption_key = var.kms_key_arn

  artifacts {
    type = "NO_ARTIFACTS" # results are uploaded to S3 by the buildspec itself
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
      name  = "REPORT_SCRIPT_KEY"
      value = aws_s3_object.report_script.key
    }
  }

  source {
    type = "NO_SOURCE"
    # Per-build env overrides (from the Run_Manager): RUN_ID, SOURCE_KEY,
    # TARGET_RUNTIME, TRANSFORMATION_NAME, RESULTS_BUCKET.
    buildspec = <<-BUILDSPEC
      version: 0.2
      # IMPORTANT: CodeBuild runs each phase in a SEPARATE shell - the working
      # directory, PATH, and shell variables do NOT persist across phases. The
      # transform must therefore run entirely within a single phase so that
      # `cd work`, the atx PATH, and $BASE remain valid throughout.
      phases:
        install:
          commands:
            # ATX CLI requires Node.js 22+ (install via n) and Git. The binaries
            # persist on disk; PATH is re-established in the build phase below.
            - npm install -g n >/dev/null 2>&1 || true
            - n 22 >/dev/null 2>&1 || true
            - export PATH="/usr/local/bin:$PATH"; hash -r || true
            - node --version && git --version
            - curl -fsSL https://transform-cli.awsstatic.com/install.sh | bash
        build:
          # A single shell block so PATH, cd, $BASE and the atx exit-code capture
          # all share one shell. CodeBuild only checks this block's final exit
          # code, so a non-zero atx exit is recorded (for report.py) without
          # aborting the build; genuine failures still abort via `set -e`.
          commands:
            - |
              set -e
              export PATH="$HOME/.local/bin:$HOME/.atx/bin:/usr/local/bin:$PATH"; hash -r || true
              # Customer-facing progress markers: ONLY these curated, hand-written
              # strings are streamed to S3 (never raw tool output, code, or paths).
              PLOG=/tmp/mf_progress.log; : > "$PLOG"
              PDEST="s3://$RESULTS_BUCKET/results/$RUN_ID/progress.log"
              progress() { echo "$(date -u +%H:%M:%S)|$1" >> "$PLOG"; aws s3 cp "$PLOG" "$PDEST" --only-show-errors || true; }
              progress "Preparing the transformation workspace"
              atx --version
              progress "Fetching your uploaded source"
              # Fetch the uploaded source + the change-report builder.
              aws s3 cp "s3://$SOURCE_BUCKET/$SOURCE_KEY" source.zip
              aws s3 cp "s3://$SOURCE_BUCKET/$REPORT_SCRIPT_KEY" report.py
              mkdir -p work && cd work && unzip -q ../source.zip
              progress "Creating a baseline snapshot of your code"
              # Baseline_Commit - the ATX CLI needs a local git working dir (Req 11.5).
              git config --global user.email "factory@modernization.local"
              git config --global user.name "Modernization Factory"
              git init -q
              # Keep transformer scratch files out of the baseline and the change
              # diff so the report lists only real transformed source files.
              printf '%s\n' atx_report.txt atx_exit.txt diff_namestatus.txt diff_full.patch report.py change_report.json change_report.md result.json > .git/info/exclude
              git add -A && git commit -q -m "pre-transform baseline"
              BASE=$(git rev-parse HEAD)
              progress "Running the AWS Transform engine (this can take a few minutes)"
              # Pick a language-appropriate build/validation command for atx (-c)
              # from the build system detected in the uploaded project. Must be a
              # single-line, standard-ASCII string (atx rejects control chars).
              if [ -f pom.xml ]; then BUILD_CMD="mvn -q -B -DskipTests compile";
              elif [ -f build.gradle ] || [ -f build.gradle.kts ]; then if [ -x ./gradlew ]; then BUILD_CMD="./gradlew -q compileJava -x test"; else BUILD_CMD="gradle -q compileJava -x test"; fi;
              elif [ -f package.json ]; then BUILD_CMD="npm ci --silent && npm run build --if-present";
              elif [ -f requirements.txt ] || [ -f setup.py ] || [ -f pyproject.toml ] || ls *.py >/dev/null 2>&1; then BUILD_CMD="python3 -m compileall -q .";
              else BUILD_CMD="true"; fi
              echo "detected build-command: $BUILD_CMD"
              # Run the transform; tolerate a non-zero atx exit (captured for the
              # report). The optional target is passed via additionalPlanContext
              # only when provided (SDK/framework migrations need no target).
              set +e
              if [ -n "$TARGET" ]; then
                atx custom def exec -p . -n "$TRANSFORMATION_NAME" --configuration "additionalPlanContext=Target: $TARGET" -c "$BUILD_CMD" -x -t > atx_report.txt 2>&1
              else
                atx custom def exec -p . -n "$TRANSFORMATION_NAME" -c "$BUILD_CMD" -x -t > atx_report.txt 2>&1
              fi
              echo $? > atx_exit.txt
              set -e
              progress "Transformation engine finished"
              cat atx_report.txt || true
              # Derive the change report + result manifest (Req 12).
              git add -A
              git diff --name-status "$BASE" > diff_namestatus.txt || true
              git diff "$BASE" > diff_full.patch || true
              progress "Generating the change report"
              cp ../report.py .
              RUN_ID="$RUN_ID" python3 report.py
              progress "Packaging the modernized code"
              # Package the modernized code (excluding VCS + report scratch).
              # Package only the transformed source: exclude VCS, transformer
              # scratch files, the report artifacts, and compile byproducts.
              zip -qr ../modernized.zip . \
                -x '.git/*' 'report.py' 'diff_*' 'atx_*' \
                   'change_report.*' 'result.json' \
                   '*/__pycache__/*' '__pycache__/*' '*.pyc'
              cd ..
              aws s3 cp modernized.zip           "s3://$RESULTS_BUCKET/results/$RUN_ID/modernized.zip"
              aws s3 cp work/change_report.json  "s3://$RESULTS_BUCKET/results/$RUN_ID/change_report.json"
              aws s3 cp work/change_report.md    "s3://$RESULTS_BUCKET/results/$RUN_ID/change_report.md" || true
              aws s3 cp work/result.json         "s3://$RESULTS_BUCKET/results/$RUN_ID/result.json"
    BUILDSPEC
  }

  logs_config {
    cloudwatch_logs {
      group_name  = "/codebuild/${var.project_name}-transformer-${var.environment}"
      stream_name = "build-log"
    }
  }

  tags = {
    Component = "codebuild"
    Purpose   = "atx-transform"
  }
}
