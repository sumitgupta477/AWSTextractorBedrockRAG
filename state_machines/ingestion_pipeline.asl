{
  "Comment": "RAG Ingestion Pipeline with Async Textract",
  "StartAt": "StartTextract",
  "States": {
    "StartTextract": {
      "Type": "Task",
      "Parameters": {
        "DocumentLocation": {
          "S3Object": {
            "Bucket.$": "$.bucket",
            "Name.$": "$.key"
          }
        },
        "OutputConfig": {
          "S3Bucket": "${textract_output_bucket}"
        }
      },
      "Resource": "arn:aws:states:::aws-sdk:textract:startDocumentTextDetection",
      "Next": "WaitForTextractCompletion",
      "ResultPath": "$.textractJob"
    },
    "WaitForTextractCompletion": {
      "Type": "Wait",
      "Seconds": 5,
      "Next": "CheckTextractStatus"
    },
    "CheckTextractStatus": {
      "Type": "Task",
      "Parameters": {
        "JobId.$": "$.textractJob.JobId"
      },
      "Resource": "arn:aws:states:::aws-sdk:textract:getDocumentTextDetection",
      "Next": "IsTextractComplete",
      "ResultPath": "$.textractResult"
    },
    "IsTextractComplete": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.textractResult.JobStatus",
          "StringEquals": "SUCCEEDED",
          "Next": "Chunking"
        },
        {
          "Variable": "$.textractResult.JobStatus",
          "StringEquals": "FAILED",
          "Next": "FailPipeline"
        }
      ],
      "Default": "WaitForTextractCompletion"
    },
    "Chunking": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "OutputPath": "$.Payload",
      "Parameters": {
        "FunctionName": "${chunking_function_arn}",
        "Payload": {
          "textract_output.$": "$.textractResult",
          "source_bucket.$": "$.bucket",
          "source_key.$": "$.key",
          "target_bucket": "${textract_output_bucket}"
        }
      },
      "Next": "SyncKnowledgeBase",
      "ResultPath": "$.chunkingResult"
    },
    "SyncKnowledgeBase": {
      "Type": "Task",
      "Parameters": {
        "KnowledgeBaseId": "${knowledge_base_id}",
        "DataSourceId": "${data_source_id}",
        "Description": "Ingestion for $.key"
      },
      "Resource": "arn:aws:states:::aws-sdk:bedrock:startIngestionJob",
      "End": true,
      "ResultPath": "$.syncResult"
    },
    "FailPipeline": {
      "Type": "Fail",
      "Cause": "Textract job failed"
    }
  }
}
