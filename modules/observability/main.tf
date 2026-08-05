# =============================================================================
# Module: Observability - Dashboard, Alarms, Custom Metrics
# Tracks: pipeline throughput, success/failure rates, agent latency, cost
# =============================================================================

data "aws_caller_identity" "current" {}

# =============================================================================
# Custom Metric Namespace (agents publish here via embedded metric format)
# =============================================================================
locals {
  metric_namespace = "ModernizationFactory/${var.environment}"
  dashboard_name   = "${var.project_name}-dashboard-${var.environment}"
}

# =============================================================================
# CloudWatch Dashboard
# =============================================================================
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    widgets = [

      # --- Row 1: Pipeline Health Overview ---
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 8
        height = 6
        properties = {
          title  = "Pipeline Executions"
          region = var.aws_region
          metrics = [
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}", { stat = "Sum", label = "Started" }],
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}", { stat = "Sum", label = "Succeeded" }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}", { stat = "Sum", label = "Failed" }],
            ["AWS/States", "ExecutionsTimedOut", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}", { stat = "Sum", label = "TimedOut" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 0
        width  = 8
        height = 6
        properties = {
          title  = "Automation Success Rate (%)"
          region = var.aws_region
          metrics = [
            [{
              expression = "100 * succeeded / started"
              label      = "Success Rate"
              id         = "rate"
            }],
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}", { stat = "Sum", id = "succeeded", visible = false }],
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}", { stat = "Sum", id = "started", visible = false }]
          ]
          period = 3600
          view   = "gauge"
          yAxis  = { left = { min = 0, max = 100 } }
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 0
        width  = 8
        height = 6
        properties = {
          title  = "Pipeline Duration (avg)"
          region = var.aws_region
          metrics = [
            ["AWS/States", "ExecutionTime", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}", { stat = "Average", label = "Avg Duration (ms)" }],
            ["AWS/States", "ExecutionTime", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}", { stat = "p90", label = "p90 Duration (ms)" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },

      # --- Row 2: CodeBuild Metrics ---
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 8
        height = 6
        properties = {
          title  = "CodeBuild - Builds"
          region = var.aws_region
          metrics = [
            ["AWS/CodeBuild", "Builds", "ProjectName", var.codebuild_project_name, { stat = "Sum", label = "Total Builds" }],
            ["AWS/CodeBuild", "SucceededBuilds", "ProjectName", var.codebuild_project_name, { stat = "Sum", label = "Succeeded" }],
            ["AWS/CodeBuild", "FailedBuilds", "ProjectName", var.codebuild_project_name, { stat = "Sum", label = "Failed" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 6
        width  = 8
        height = 6
        properties = {
          title  = "CodeBuild - Duration"
          region = var.aws_region
          metrics = [
            ["AWS/CodeBuild", "Duration", "ProjectName", var.codebuild_project_name, { stat = "Average", label = "Avg (sec)" }],
            ["AWS/CodeBuild", "Duration", "ProjectName", var.codebuild_project_name, { stat = "Maximum", label = "Max (sec)" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 6
        width  = 8
        height = 6
        properties = {
          title  = "DynamoDB - State Table Operations"
          region = var.aws_region
          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", var.state_table_name, { stat = "Sum", label = "Writes" }],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", var.state_table_name, { stat = "Sum", label = "Reads" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },

      # --- Row 3: Custom Metrics (agents publish via EMF) ---
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 8
        height = 6
        properties = {
          title  = "Applications by Outcome"
          region = var.aws_region
          metrics = [
            [local.metric_namespace, "AppsCompleted", { stat = "Sum", label = "Completed (autonomous)" }],
            [local.metric_namespace, "AppsEscalated", { stat = "Sum", label = "Escalated (human)" }],
            [local.metric_namespace, "AppsInProgress", { stat = "Sum", label = "In Progress" }]
          ]
          period = 3600
          view   = "bar"
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 12
        width  = 8
        height = 6
        properties = {
          title  = "Retry Attempts Distribution"
          region = var.aws_region
          metrics = [
            [local.metric_namespace, "RetryAttempts", { stat = "Average", label = "Avg Retries" }],
            [local.metric_namespace, "RetryAttempts", { stat = "Maximum", label = "Max Retries" }]
          ]
          period = 3600
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 12
        width  = 8
        height = 6
        properties = {
          title  = "Complexity Score Distribution"
          region = var.aws_region
          metrics = [
            [local.metric_namespace, "ComplexityScore", { stat = "Average", label = "Avg Score" }],
            [local.metric_namespace, "ComplexityScore", { stat = "p50", label = "Median" }],
            [local.metric_namespace, "ComplexityScore", { stat = "p90", label = "p90" }]
          ]
          period = 3600
          view   = "timeSeries"
        }
      },

      # --- Row 4: Agent Performance ---
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          title  = "Agent Invocation Latency"
          region = var.aws_region
          metrics = [
            [local.metric_namespace, "AgentLatency", "AgentType", "ATX", { stat = "Average", label = "ATX (avg ms)" }],
            [local.metric_namespace, "AgentLatency", "AgentType", "Kiro", { stat = "Average", label = "Kiro (avg ms)" }],
            [local.metric_namespace, "AgentLatency", "AgentType", "CodeBuild", { stat = "Average", label = "CodeBuild Agent (avg ms)" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          title  = "Bedrock Token Usage"
          region = var.aws_region
          metrics = [
            ["AWS/Bedrock", "InputTokenCount", { stat = "Sum", label = "Input Tokens" }],
            ["AWS/Bedrock", "OutputTokenCount", { stat = "Sum", label = "Output Tokens" }]
          ]
          period = 3600
          view   = "timeSeries"
        }
      }
    ]
  })
}

# =============================================================================
# CloudWatch Alarms
# =============================================================================

# Alarm: Pipeline failure rate > 50%
resource "aws_cloudwatch_metric_alarm" "high_failure_rate" {
  alarm_name          = "${var.project_name}-high-failure-rate-${var.environment}"
  alarm_description   = "More than 50% of pipeline executions are failing"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 50
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "rate"
    expression  = "100 * failed / (failed + succeeded)"
    label       = "Failure Rate %"
    return_data = true
  }

  metric_query {
    id = "failed"
    metric {
      metric_name = "ExecutionsFailed"
      namespace   = "AWS/States"
      period      = 3600
      stat        = "Sum"
      dimensions = {
        StateMachineArn = "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}"
      }
    }
  }

  metric_query {
    id = "succeeded"
    metric {
      metric_name = "ExecutionsSucceeded"
      namespace   = "AWS/States"
      period      = 3600
      stat        = "Sum"
      dimensions = {
        StateMachineArn = "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}"
      }
    }
  }

  alarm_actions = var.sns_alarm_topic_arn != "" ? [var.sns_alarm_topic_arn] : []
  ok_actions    = var.sns_alarm_topic_arn != "" ? [var.sns_alarm_topic_arn] : []

  tags = {
    Component = "observability"
    Severity  = "high"
  }
}

# Alarm: CodeBuild builds taking too long (> 20 min avg)
resource "aws_cloudwatch_metric_alarm" "codebuild_slow" {
  alarm_name          = "${var.project_name}-codebuild-slow-${var.environment}"
  alarm_description   = "CodeBuild average duration exceeds 20 minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "Duration"
  namespace           = "AWS/CodeBuild"
  period              = 900
  statistic           = "Average"
  threshold           = 1200 # 20 minutes in seconds
  treat_missing_data  = "notBreaching"

  dimensions = {
    ProjectName = var.codebuild_project_name
  }

  alarm_actions = var.sns_alarm_topic_arn != "" ? [var.sns_alarm_topic_arn] : []

  tags = {
    Component = "observability"
    Severity  = "medium"
  }
}

# Alarm: High escalation rate (> 40% apps going to human)
resource "aws_cloudwatch_metric_alarm" "high_escalation" {
  alarm_name          = "${var.project_name}-high-escalation-${var.environment}"
  alarm_description   = "More than 40% of applications are being escalated to human review"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 40
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "rate"
    expression  = "100 * escalated / (escalated + completed)"
    label       = "Escalation Rate %"
    return_data = true
  }

  metric_query {
    id = "escalated"
    metric {
      metric_name = "AppsEscalated"
      namespace   = local.metric_namespace
      period      = 3600
      stat        = "Sum"
    }
  }

  metric_query {
    id = "completed"
    metric {
      metric_name = "AppsCompleted"
      namespace   = local.metric_namespace
      period      = 3600
      stat        = "Sum"
    }
  }

  alarm_actions = var.sns_alarm_topic_arn != "" ? [var.sns_alarm_topic_arn] : []

  tags = {
    Component = "observability"
    Severity  = "medium"
  }
}

# Alarm: Step Functions execution timeout
resource "aws_cloudwatch_metric_alarm" "execution_timeout" {
  alarm_name          = "${var.project_name}-execution-timeout-${var.environment}"
  alarm_description   = "Pipeline executions are timing out"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsTimedOut"
  namespace           = "AWS/States"
  period              = 3600
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.state_machine_name}"
  }

  alarm_actions = var.sns_alarm_topic_arn != "" ? [var.sns_alarm_topic_arn] : []

  tags = {
    Component = "observability"
    Severity  = "high"
  }
}

# Alarm: DynamoDB throttling
resource "aws_cloudwatch_metric_alarm" "dynamodb_throttle" {
  alarm_name          = "${var.project_name}-dynamo-throttle-${var.environment}"
  alarm_description   = "DynamoDB state table is being throttled"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ThrottledRequests"
  namespace           = "AWS/DynamoDB"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = var.state_table_name
  }

  alarm_actions = var.sns_alarm_topic_arn != "" ? [var.sns_alarm_topic_arn] : []

  tags = {
    Component = "observability"
    Severity  = "low"
  }
}
