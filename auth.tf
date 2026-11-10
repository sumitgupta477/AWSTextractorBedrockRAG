# ============================================================
# API key authentication for the query endpoint
#
# HTTP APIs don't support API Gateway usage-plan keys, so a small
# Lambda authorizer checks the x-api-key header against a key that
# Terraform generates. Responses are cached for 5 minutes per key,
# so the authorizer adds almost no latency or cost.
# ============================================================
resource "random_password" "api_key" {
  length  = 40
  special = false
}

data "archive_file" "authorizer_lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/authorizer"
  output_path = "${path.module}/authorizer.zip"
}

resource "aws_lambda_function" "api_authorizer" {
  count            = var.enable_api_gateway ? 1 : 0
  filename         = data.archive_file.authorizer_lambda_zip.output_path
  source_code_hash = data.archive_file.authorizer_lambda_zip.output_base64sha256
  function_name    = "${var.project_name}-api-authorizer"
  role             = aws_iam_role.lambda_general_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.12"
  timeout          = 5
  memory_size      = 128

  environment {
    variables = {
      API_KEY = random_password.api_key.result
    }
  }
}

resource "aws_cloudwatch_log_group" "api_authorizer_logs" {
  count             = var.enable_api_gateway ? 1 : 0
  name              = "/aws/lambda/${aws_lambda_function.api_authorizer[0].function_name}"
  retention_in_days = 14
}

resource "aws_apigatewayv2_authorizer" "api_key" {
  count                             = var.enable_api_gateway ? 1 : 0
  api_id                            = aws_apigatewayv2_api.rag_api[0].id
  name                              = "${var.project_name}-api-key"
  authorizer_type                   = "REQUEST"
  authorizer_uri                    = aws_lambda_function.api_authorizer[0].invoke_arn
  authorizer_payload_format_version = "2.0"
  enable_simple_responses           = true
  identity_sources                  = ["$request.header.x-api-key"]
  authorizer_result_ttl_in_seconds  = 300
}

resource "aws_lambda_permission" "allow_api_gateway_authorizer" {
  count         = var.enable_api_gateway ? 1 : 0
  statement_id  = "AllowAuthorizerInvokeFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_authorizer[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.rag_api[0].execution_arn}/authorizers/${aws_apigatewayv2_authorizer.api_key[0].id}"
}
