# ============================================================
# Observability: alerts, API access logs, tracing and a dashboard
# ============================================================

locals {
  obs_prefix     = "${var.project_name}-${var.environment}"
  lambda_names   = [
    aws_lambda_function.sfn_trigger.function_name,
    aws_lambda_function.chunking.function_name,
    aws_lambda_function.query_handler.function_name,
  ]
}

# ------------------------------------------------------------
# Alert topic (subscribe an email with var.alarm_email)
# ------------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name = "${local.obs_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email # AWS sends a confirmation email that must be accepted
}

# ------------------------------------------------------------
# Alarms
# ------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "ingestion_failed" {
  alarm_name          = "${local.obs_prefix}-ingestion-failed"
  alarm_description   = "A document failed to ingest (Textract, chunking or Knowledge Base sync). Check the Step Functions execution history."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.ingestion_pipeline.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = toset(local.lambda_names)
  alarm_name          = "${each.value}-errors"
  alarm_description   = "Lambda ${each.value} is raising errors. Check its CloudWatch logs."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  count               = var.enable_api_gateway ? 1 : 0
  alarm_name          = "${local.obs_prefix}-api-5xx"
  alarm_description   = "The query API is returning server errors."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.rag_api[0].id, Stage = var.api_stage_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "query_latency" {
  alarm_name          = "${local.obs_prefix}-query-latency-high"
  alarm_description   = "p90 query latency is above ${var.query_latency_alarm_ms} ms."
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = aws_lambda_function.query_handler.function_name }
  extended_statistic  = "p90"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = var.query_latency_alarm_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# ------------------------------------------------------------
# API Gateway access logs (one JSON line per request)
# ------------------------------------------------------------
resource "aws_cloudwatch_log_group" "api_access_logs" {
  count             = var.enable_api_gateway ? 1 : 0
  name              = "/aws/apigateway/${local.obs_prefix}-rag-api"
  retention_in_days = 14
}

# ------------------------------------------------------------
# X-Ray tracing permissions for the Lambdas
# ------------------------------------------------------------
resource "aws_iam_role_policy_attachment" "lambda_xray" {
  role       = aws_iam_role.lambda_general_role.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "rag" {
  dashboard_name = "${local.obs_prefix}-rag"
  dashboard_body = jsonencode({
    widgets = concat(
      [
        {
          type = "metric", x = 0, y = 0, width = 12, height = 6
          properties = {
            title  = "Ingestion pipeline"
            region = var.aws_region
            stat   = "Sum"
            period = 300
            metrics = [
              ["AWS/States", "ExecutionsStarted", "StateMachineArn", aws_sfn_state_machine.ingestion_pipeline.arn],
              [".", "ExecutionsSucceeded", ".", "."],
              [".", "ExecutionsFailed", ".", "."],
            ]
          }
        },
        {
          type = "metric", x = 12, y = 0, width = 12, height = 6
          properties = {
            title  = "Query latency (ms)"
            region = var.aws_region
            period = 300
            metrics = [
              ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.query_handler.function_name, { stat = "p50", label = "p50" }],
              ["...", { stat = "p90", label = "p90" }],
              ["...", { stat = "Maximum", label = "max" }],
            ]
          }
        },
        {
          type = "metric", x = 0, y = 6, width = 12, height = 6
          properties = {
            title   = "Lambda errors"
            region  = var.aws_region
            stat    = "Sum"
            period  = 300
            metrics = [for name in local.lambda_names : ["AWS/Lambda", "Errors", "FunctionName", name]]
          }
        },
        {
          type = "metric", x = 12, y = 6, width = 12, height = 6
          properties = {
            title  = "Bedrock tokens (all models in region)"
            region = var.aws_region
            stat   = "Sum"
            period = 300
            metrics = [
              ["AWS/Bedrock", "InputTokenCount"],
              [".", "OutputTokenCount"],
            ]
          }
        },
      ],
      var.enable_api_gateway ? [
        {
          type = "metric", x = 0, y = 12, width = 24, height = 6
          properties = {
            title  = "Query API requests"
            region = var.aws_region
            stat   = "Sum"
            period = 300
            metrics = [
              ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.rag_api[0].id, "Stage", var.api_stage_name],
              [".", "4xx", ".", ".", ".", "."],
              [".", "5xx", ".", ".", ".", "."],
            ]
          }
        },
      ] : []
    )
  })
}
