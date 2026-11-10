# ============================================================
# Bedrock Guardrails
#
# Applied to every RetrieveAndGenerate call. Screens questions and
# answers for prompt-injection attempts and harmful content, masks
# personal data in answers, and blocks answers that aren't grounded
# in the retrieved documents (contextual grounding).
# ============================================================
resource "aws_bedrock_guardrail" "rag" {
  count                     = var.enable_guardrail ? 1 : 0
  name                      = "${var.project_name}-${var.environment}-guardrail"
  description               = "Guardrail for the RAG query API"
  blocked_input_messaging   = "Sorry, I can't help with that request."
  blocked_outputs_messaging = "Sorry, I can't provide that answer. Please rephrase your question."

  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE" # prompt-attack filtering only applies to input
    }
    dynamic "filters_config" {
      for_each = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]
      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = "HIGH"
      }
    }
  }

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = ["EMAIL", "PHONE", "US_SOCIAL_SECURITY_NUMBER", "CREDIT_DEBIT_CARD_NUMBER", "AWS_ACCESS_KEY", "AWS_SECRET_KEY", "PASSWORD"]
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }

  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = var.guardrail_grounding_threshold
    }
    filters_config {
      type      = "RELEVANCE"
      threshold = var.guardrail_relevance_threshold
    }
  }
}

resource "aws_bedrock_guardrail_version" "rag" {
  count         = var.enable_guardrail ? 1 : 0
  guardrail_arn = aws_bedrock_guardrail.rag[0].guardrail_arn
  description   = "Managed by Terraform"

  lifecycle {
    # Publish a new version whenever the guardrail definition changes.
    replace_triggered_by = [aws_bedrock_guardrail.rag[0]]
  }
}

resource "aws_iam_role_policy" "lambda_guardrail" {
  count = var.enable_guardrail ? 1 : 0
  name  = "${var.project_name}-lambda-apply-guardrail"
  role  = aws_iam_role.lambda_general_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:ApplyGuardrail"]
      Resource = aws_bedrock_guardrail.rag[0].guardrail_arn
    }]
  })
}
