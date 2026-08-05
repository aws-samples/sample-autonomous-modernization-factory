output "state_machine_arn" {
  description = "ARN of the closed-loop Step Functions state machine"
  value       = aws_sfn_state_machine.modernization_loop.arn
}

output "state_machine_name" {
  description = "Name of the closed-loop Step Functions state machine"
  value       = aws_sfn_state_machine.modernization_loop.name
}

output "log_group_name" {
  description = "CloudWatch log group name for the state machine"
  value       = aws_cloudwatch_log_group.orchestration.name
}
