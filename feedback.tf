# ============================================================
# User feedback: POST /feedback (thumbs up / down per answer)
#
# A live accuracy signal: the share of thumbs-down answers is tracked as
# a CloudWatch metric, shown on the dashboard and alarmed on.
# ============================================================
locals {
  feedback_namespace = "RAG/${var.project_name}-${var.environment}"
}

data "archive_file" "feedback_lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/feedback"
  output_path = "${path.module}/feedback.zip"
}

resource "aws_lambda_function" "feedback" {
  count            = var.enable_api_gateway ? 1 : 0
  filename         = data.archive_file.feedback_lambda_zip.output_path
  source_code_hash = data.archive_file.feedback_lambda_zip.output_base64sha256
  function_name    = "${var.project_name}-feedback"
  role             = aws_iam_role.lambda_general_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.12"
  timeout          = 5
  memory_size      = 128

  environment {
    variables = {
      METRIC_NAMESPACE = local.feedback_namespace
      PROJECT_NAME     = "${var.project_name}-${var.environment}"
    }
  }
}

resource "aws_cloudwatch_log_group" "feedback_logs" {
  count             = var.enable_api_gateway ? 1 : 0
  name              = "/aws/lambda/${aws_lambda_function.feedback[0].function_name}"
  retention_in_days = 90 # kept longer than other logs: feedback is useful for trend analysis
}

resource "aws_apigatewayv2_integration" "feedback_integration" {
  count                  = var.enable_api_gateway ? 1 : 0
  api_id                 = aws_apigatewayv2_api.rag_api[0].id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.feedback[0].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "feedback_route" {
  count              = var.enable_api_gateway ? 1 : 0
  api_id             = aws_apigatewayv2_api.rag_api[0].id
  route_key          = "POST /feedback"
  target             = "integrations/${aws_apigatewayv2_integration.feedback_integration[0].id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.api_key[0].id
}

resource "aws_lambda_permission" "allow_api_gateway_feedback" {
  count         = var.enable_api_gateway ? 1 : 0
  statement_id  = "AllowFeedbackInvokeFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.feedback[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.rag_api[0].execution_arn}/*/*"
}

# Alarm when too many answers are rated thumbs-down (only once there's
# enough feedback in the window to be meaningful).
resource "aws_cloudwatch_metric_alarm" "negative_feedback_rate" {
  count               = var.enable_api_gateway ? 1 : 0
  alarm_name          = "${var.project_name}-${var.environment}-negative-feedback-rate"
  alarm_description   = "More than ${var.negative_feedback_alarm_pct}% of rated answers were thumbs-down. Check recent feedback comments and the eval."
  evaluation_periods  = 1
  threshold           = var.negative_feedback_alarm_pct
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "rate"
    expression  = "IF((up + down) >= ${var.negative_feedback_min_ratings}, 100 * down / (up + down), 0)"
    label       = "Negative feedback %"
    return_data = true
  }
  metric_query {
    id = "up"
    metric {
      namespace   = local.feedback_namespace
      metric_name = "FeedbackPositive"
      dimensions  = { Project = "${var.project_name}-${var.environment}" }
      stat        = "Sum"
      period      = 86400
    }
  }
  metric_query {
    id = "down"
    metric {
      namespace   = local.feedback_namespace
      metric_name = "FeedbackNegative"
      dimensions  = { Project = "${var.project_name}-${var.environment}" }
      stat        = "Sum"
      period      = 86400
    }
  }
}
