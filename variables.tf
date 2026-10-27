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
