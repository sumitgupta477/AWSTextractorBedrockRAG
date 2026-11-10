#!/usr/bin/env bash
# Uploads the eval's test document to the raw documents bucket and waits for
# the ingestion pipeline (Textract -> chunking -> Knowledge Base sync) to finish.
# Run from the repo root after `terraform apply`.
set -euo pipefail

BUCKET="$(terraform output -raw raw_documents_bucket)"
SFN_ARN="$(terraform output -raw sfn_state_machine_arn)"
DOC="evals/sample_docs/northwind_travel_policy.pdf"

aws s3 cp "$DOC" "s3://${BUCKET}/documents/northwind_travel_policy.pdf"
echo "Uploaded. Waiting for the ingestion pipeline..."

sleep 10
for _ in $(seq 1 60); do
  STATUS="$(aws stepfunctions list-executions --state-machine-arn "$SFN_ARN" --max-results 1 \
    --query 'executions[0].status' --output text)"
  case "$STATUS" in
    SUCCEEDED) echo "Ingestion succeeded. Export RAG_API_URL=\$(terraform output -raw api_gateway_endpoint) and run the eval."; exit 0 ;;
    FAILED|TIMED_OUT|ABORTED) echo "Ingestion ${STATUS}. Check the Step Functions console." >&2; exit 1 ;;
  esac
  sleep 10
done
echo "Still running after 10 minutes; check the Step Functions console." >&2
exit 1
