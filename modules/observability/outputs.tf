output "dashboard_name" {
  description = "Name of the CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}

output "dashboard_url" {
  description = "Console URL for the CloudWatch dashboard"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${local.dashboard_name}"
}

output "metric_namespace" {
  description = "CloudWatch metric namespace for pipeline metrics"
  value       = local.metric_namespace
}

output "alarm_arns" {
  description = "ARNs of the CloudWatch alarms created by this module"
  value = {
    high_failure_rate = aws_cloudwatch_metric_alarm.high_failure_rate.arn
    codebuild_slow    = aws_cloudwatch_metric_alarm.codebuild_slow.arn
    high_escalation   = aws_cloudwatch_metric_alarm.high_escalation.arn
    execution_timeout = aws_cloudwatch_metric_alarm.execution_timeout.arn
    dynamodb_throttle = aws_cloudwatch_metric_alarm.dynamodb_throttle.arn
  }
}
