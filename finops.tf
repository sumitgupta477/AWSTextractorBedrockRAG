# ============================================================
# FinOps: monthly budget for this project
#
# Tracks spend on resources tagged Project = var.project_name (every
# resource gets this tag via the provider's default_tags).
# For the filter to work, activate "Project" as a cost allocation tag
# once in the Billing console (Cost allocation tags); AWS can take up
# to 24 hours to start reporting it.
# ============================================================
resource "aws_budgets_budget" "rag" {
  count        = var.monthly_budget_usd > 0 ? 1 : 0
  name         = "${var.project_name}-${var.environment}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$${var.project_name}"]
  }

  # Early warning when actual spend passes 80% of the budget
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }

  # Forecast says the month will end over budget
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }
}

# Allow AWS Budgets to publish to the alerts topic
resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowBudgetsPublish"
        Effect    = "Allow"
        Principal = { Service = "budgets.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      },
      {
        Sid       = "AllowCloudWatchAlarmsPublish"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

# Guardrail interventions: a spike usually means abuse attempts or
# questions the documents can't answer. Both are worth a look.
resource "aws_cloudwatch_metric_alarm" "guardrail_interventions" {
  count               = var.enable_guardrail ? 1 : 0
  alarm_name          = "${var.project_name}-${var.environment}-guardrail-interventions"
  alarm_description   = "The Bedrock Guardrail is blocking or masking an unusual number of requests."
  namespace           = "AWS/Bedrock/Guardrails"
  metric_name         = "InvocationsIntervened"
  dimensions          = { GuardrailArn = aws_bedrock_guardrail.rag[0].guardrail_arn, GuardrailVersion = aws_bedrock_guardrail_version.rag[0].version }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.guardrail_intervention_alarm_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
