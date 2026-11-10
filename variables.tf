variable "project_name" {
  description = "Unique project name for all resources"
  type        = string
}

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "embedding_dimensions" {
  description = "Vector dimensions for Titan Embeddings"
  type        = number
  default     = 1024
}

variable "chunk_size_chars" {
  description = "Target size of each text chunk, in characters (roughly 4 characters per token)"
  type        = number
  default     = 2000
}

variable "chunk_overlap_chars" {
  description = "Characters of overlap between consecutive chunks"
  type        = number
  default     = 200
}

variable "generation_model_id" {
  description = "Bedrock model used to generate answers: a foundation model ID or a model / inference-profile ARN"
  type        = string
  default     = "amazon.titan-text-premier-v1:0"
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "api_stage_name" {
  description = "API Gateway stage name"
  type        = string
  default     = "v1"
}

variable "enable_api_gateway" {
  description = "Enable API Gateway for query endpoint"
  type        = bool
  default     = true
}
variable "api_throttle_rate_limit" {
  description = "Steady-state requests per second allowed on the query API (caps Bedrock spend)"
  type        = number
  default     = 5
}

variable "api_throttle_burst_limit" {
  description = "Maximum burst of concurrent requests allowed on the query API"
  type        = number
  default     = 10
}

variable "alarm_email" {
  description = "Email address for alarm notifications (leave empty to skip; AWS sends a confirmation email)"
  type        = string
  default     = ""
}

variable "query_latency_alarm_ms" {
  description = "Alarm when p90 query latency exceeds this many milliseconds"
  type        = number
  default     = 15000
}

variable "enable_guardrail" {
  description = "Apply a Bedrock Guardrail (prompt-attack, harmful content, PII masking, grounding checks) to every query"
  type        = bool
  default     = true
}

variable "guardrail_grounding_threshold" {
  description = "Block answers whose grounding score in the retrieved documents is below this (0-0.99)"
  type        = number
  default     = 0.7
}

variable "guardrail_relevance_threshold" {
  description = "Block answers whose relevance to the question is below this (0-0.99)"
  type        = number
  default     = 0.5
}

variable "monthly_budget_usd" {
  description = "Monthly AWS Budget for this project in USD; alerts at 80% actual and 100% forecast (0 disables)"
  type        = number
  default     = 25
}

variable "guardrail_intervention_alarm_threshold" {
  description = "Alarm when the guardrail intervenes on more than this many requests in an hour"
  type        = number
  default     = 20
}
