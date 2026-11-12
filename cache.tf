# ============================================================
# Answer cache for the query API
#
# The query Lambda stores each answer under a hash of the question, so a
# repeated question is served from DynamoDB instead of paying for Bedrock
# retrieval and generation again. Entries expire after
# var.query_cache_ttl_seconds, and the ingestion pipeline bumps a version
# counter in this table whenever a document is indexed, which retires every
# answer cached before it. Set query_cache_ttl_seconds = 0 to turn caching off.
# ============================================================
resource "aws_dynamodb_table" "query_cache" {
  name         = "${var.project_name}-${var.environment}-query-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  # DynamoDB deletes expired answers on its own (the Lambda also checks the expiry).
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${var.project_name}-query-cache"
    Environment = var.environment
  }
}
