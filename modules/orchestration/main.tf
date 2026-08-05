# =============================================================================
# Module: Orchestration - Step Functions Closed-Loop State Machine
# Implements: Assess → Route → Transform → Build → [Fix Loop] → Complete/Escalate
# =============================================================================

# --- IAM Role for Step Functions ---
resource "aws_iam_role" "stepfunctions" {
  name = "${var.project_name}-sfn-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "stepfunctions" {
  name = "${var.project_name}-sfn-policy"
  role = aws_iam_role.stepfunctions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "codebuild:StartBuild",
          "codebuild:BatchGetBuilds",
          "codebuild:StopBuild"
        ]
        Resource = [
          "arn:aws:codebuild:*:*:project/${var.codebuild_project}",
          "arn:aws:codebuild:*:*:project/${var.transformer_project}",
          "arn:aws:codebuild:*:*:project/${var.remediator_project}"
        ]
      },
      {
        # Required for the codebuild:startBuild.sync (.sync) integration pattern:
        # Step Functions creates an EventBridge managed rule to receive CodeBuild
        # state-change events and know when the build completes.
        Effect = "Allow"
        Action = [
          "events:PutTargets",
          "events:PutRule",
          "events:DescribeRule"
        ]
        Resource = "arn:aws:events:*:*:rule/StepFunctionsGetEventForCodeBuildStartBuildRule"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = [
          "arn:aws:s3:::${var.artifacts_bucket}/*",
          "arn:aws:s3:::${var.results_bucket}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.sns_escalation_topic_arn != "" ? var.sns_escalation_topic_arn : "arn:aws:sns:*:*:no-op"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

# --- CloudWatch Log Group ---
resource "aws_cloudwatch_log_group" "orchestration" {
  name              = "/stepfunctions/${var.project_name}-${var.environment}"
  retention_in_days = 365
  kms_key_id        = var.kms_key_arn
}

# --- Step Functions State Machine ---
resource "aws_sfn_state_machine" "modernization_loop" {
  name     = "${var.project_name}-loop-${var.environment}"
  role_arn = aws_iam_role.stepfunctions.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.orchestration.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  # ---------------------------------------------------------------------------
  # Closed-loop pipeline: Transform -> Validate -> [AI Remediate loop] -> terminal.
  #
  #   Transform (real ATX transformer CodeBuild)
  #     -> Validate (build/test gate on results/<run_id>/modernized.zip)
  #          SUCCEEDED -> Completed
  #          FAILED    -> CheckAttempts
  #                         attempt >= max_retry_attempts -> Escalated
  #                         else -> Remediate (Bedrock AI auto-fix in CodeBuild)
  #                                   -> NextAttempt (attempt + 1) -> Validate  (loop)
  #
  # The validator publishes results/<run_id>/validation.log; on failure the
  # remediator reads it plus the code, asks Bedrock for fixes, overwrites
  # modernized.zip, and the loop re-validates. Escalation happens when the
  # remediation budget is exhausted (attempt >= max) or the remediator can
  # produce no fix (its build exits non-zero -> Catch -> Escalated).
  #
  # codebuild:startBuild.sync raises a task error on any non-SUCCEEDED build, so
  # failure branches use Catch. The loop counter `attempt` is supplied by the
  # Run_Manager (starts at 0) and incremented with the States.MathAdd intrinsic.
  #
  # Each terminal Pass emits {"outcome": ...} as the execution output so the web
  # app's refresh_status can map it: COMPLETED -> reads result.json; ESCALATED ->
  # human review; FAILED -> transform build error.
  # ---------------------------------------------------------------------------
  definition = jsonencode({
    Comment = "Modernization Factory - Transform -> Validate -> [AI Remediate loop] -> Complete/Escalate"
    StartAt = "Transform"
    States = {

      # Step 1: Transform via the AWS Transform (atx) CLI in CodeBuild. The
      # chosen transformation and optional target are passed per run; RESULTS_BUCKET
      # comes from the transformer project's own env.
      Transform = {
        Type     = "Task"
        Resource = "arn:aws:states:::codebuild:startBuild.sync"
        Parameters = {
          ProjectName = var.transformer_project
          EnvironmentVariablesOverride = [
            { Name = "RUN_ID", "Value.$" = "$.run_id", Type = "PLAINTEXT" },
            { Name = "SOURCE_KEY", "Value.$" = "$.source_key", Type = "PLAINTEXT" },
            { Name = "TRANSFORMATION_NAME", "Value.$" = "$.transformation_name", Type = "PLAINTEXT" },
            { Name = "TARGET", "Value.$" = "$.target", Type = "PLAINTEXT" },
          ]
        }
        ResultPath = "$.transform"
        Catch = [
          { ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "Failed" }
        ]
        Next = "Validate"
      }

      # Step 2: Validate the (possibly remediated) output - build + run tests.
      # Source is overridden to the run's modernized.zip. On failure, loop to
      # CheckAttempts instead of escalating immediately.
      Validate = {
        Type     = "Task"
        Resource = "arn:aws:states:::codebuild:startBuild.sync"
        Parameters = {
          ProjectName                = var.codebuild_project
          SourceTypeOverride         = "S3"
          "SourceLocationOverride.$" = "States.Format('${var.results_bucket}/results/{}/modernized.zip', $.run_id)"
          # RUN_ID lets the validator append to the run's progress log.
          EnvironmentVariablesOverride = [
            { Name = "RUN_ID", "Value.$" = "$.run_id", Type = "PLAINTEXT" },
          ]
        }
        ResultPath = "$.validate"
        Catch = [
          { ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "CheckAttempts" }
        ]
        Next = "Completed"
      }

      # Step 3: Is the remediation budget exhausted?
      CheckAttempts = {
        Type = "Choice"
        Choices = [
          {
            Variable                 = "$.attempt"
            NumericGreaterThanEquals = var.max_retry_attempts
            Next                     = "Escalated"
          }
        ]
        Default = "Remediate"
      }

      # Step 4: AI auto-fix. The remediator reads validation.log + the code,
      # applies Bedrock-produced fixes, and overwrites modernized.zip. If it
      # cannot fix (exit != 0), the build errors and Catch escalates.
      Remediate = {
        Type     = "Task"
        Resource = "arn:aws:states:::codebuild:startBuild.sync"
        Parameters = {
          ProjectName                = var.remediator_project
          SourceTypeOverride         = "S3"
          "SourceLocationOverride.$" = "States.Format('${var.results_bucket}/results/{}/modernized.zip', $.run_id)"
          EnvironmentVariablesOverride = [
            { Name = "RUN_ID", "Value.$" = "$.run_id", Type = "PLAINTEXT" },
          ]
        }
        ResultPath = "$.remediate"
        Catch = [
          { ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "Escalated" }
        ]
        Next = "NextAttempt"
      }

      # Step 5: Increment the loop counter and re-validate.
      NextAttempt = {
        Type = "Pass"
        Parameters = {
          "run_id.$"              = "$.run_id"
          "source_key.$"          = "$.source_key"
          "transformation_name.$" = "$.transformation_name"
          "target.$"              = "$.target"
          "attempt.$"             = "States.MathAdd($.attempt, 1)"
        }
        Next = "Validate"
      }

      # Terminal: validation passed. The web app reads result.json for the
      # transformer's authoritative status + artifact/report keys.
      Completed = {
        Type = "Pass"
        Parameters = {
          outcome    = "COMPLETED"
          "run_id.$" = "$.run_id"
        }
        End = true
      }

      # Terminal: validation still failing after automated remediation -> human review.
      Escalated = {
        Type = "Pass"
        Parameters = {
          outcome    = "ESCALATED"
          reason     = "transformed code failed build/test validation after automated remediation"
          "run_id.$" = "$.run_id"
        }
        End = true
      }

      # Terminal: the transform build itself failed.
      Failed = {
        Type = "Pass"
        Parameters = {
          outcome    = "FAILED"
          reason     = "transform build failed"
          "run_id.$" = "$.run_id"
        }
        End = true
      }
    }
  })

  tags = {
    Component = "orchestration"
    Purpose   = "closed-loop-modernization"
  }
}
