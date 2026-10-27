output "raw_documents_bucket" {
  description = "S3 bucket for uploading raw documents"
  value       = aws_s3_bucket.raw_documents.bucket
}

output "textract_output_bucket" {
  description = "S3 bucket for Textract output"
  value       = aws_s3_bucket.textract_output.bucket
}

output "knowledge_base_id" {
  description = "Bedrock Knowledge Base ID"
  value       = aws_bedrockagent_knowledge_base.rag.knowledge_base_id
}

output "api_gateway_endpoint" {
  description = "API Gateway endpoint for queries"
  value       = var.enable_api_gateway ? "${aws_apigatewayv2_api.rag_api[0].api_endpoint}/${var.api_stage_name}/query" : "API Gateway disabled"
}

output "sfn_state_machine_arn" {
  description = "Step Functions state machine ARN"
  value       = aws_sfn_state_machine.ingestion_pipeline.arn
}
output "api_key" {
  description = "API key for the query endpoint; send it in the x-api-key header (read with: terraform output -raw api_key)"
  value       = random_password.api_key.result
  sensitive   = true
}
