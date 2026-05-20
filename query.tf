# ============================================================
# Lambda Function: Query Handler
# ============================================================
data "archive_file" "query_lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/query"
  output_path = "${path.module}/query.zip"
}

resource "aws_lambda_function" "query_handler" {
  filename         = data.archive_file.query_lambda_zip.output_path
  function_name    = "${var.project_name}-query-handler"
  role             = aws_iam_role.lambda_general_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      KNOWLEDGE_BASE_ID = aws_bedrockagent_knowledge_base.rag.knowledge_base_id
      MODEL_ID          = "amazon.titan-text-premier-v1:0"  # Can be changed to any supported model
    }
  }
}

resource "aws_cloudwatch_log_group" "query_handler_logs" {
  name              = "/aws/lambda/${aws_lambda_function.query_handler.function_name}"
  retention_in_days = 14
}

resource "aws_lambda_permission" "allow_api_gateway" {
  count         = var.enable_api_gateway ? 1 : 0
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.rag_api[0].execution_arn}/*/*"
}

# ============================================================
# API Gateway (HTTP API)
# ============================================================
resource "aws_apigatewayv2_api" "rag_api" {
  count         = var.enable_api_gateway ? 1 : 0
  name          = "${var.project_name}-rag-api"
  protocol_type = "HTTP"
  description   = "API for RAG queries"
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST"]
    allow_headers = ["content-type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "query_integration" {
  count              = var.enable_api_gateway ? 1 : 0
  api_id             = aws_apigatewayv2_api.rag_api[0].id
  integration_type   = "AWS_PROXY"
  integration_method = "POST"
  integration_uri    = aws_lambda_function.query_handler.invoke_arn
  connection_type    = "INTERNET"
}

resource "aws_apigatewayv2_route" "query_route" {
  count     = var.enable_api_gateway ? 1 : 0
  api_id    = aws_apigatewayv2_api.rag_api[0].id
  route_key = "POST /query"
  target    = "integrations/${aws_apigatewayv2_integration.query_integration[0].id}"
}

resource "aws_apigatewayv2_stage" "api_stage" {
  count       = var.enable_api_gateway ? 1 : 0
  api_id      = aws_apigatewayv2_api.rag_api[0].id
  name        = var.api_stage_name
  auto_deploy = true
}