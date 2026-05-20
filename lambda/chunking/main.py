import json
import boto3
from datetime import datetime

s3 = boto3.client('s3')

def lambda_handler(event, context):
    """Simple chunking function called by Step Functions."""
    # Extract parameters from Step Function payload
    textract_output = event.get('textract_output', {})
    source_bucket = event.get('source_bucket')
    source_key = event.get('source_key')
    
    # Process the Textract output and create chunks
    # (Simplified - in production, you'd parse the Textract JSON)
    chunks = extract_chunks_from_textract(textract_output)
    
    # Store chunks in S3 for Bedrock sync
    for i, chunk in enumerate(chunks):
        chunk_key = f"chunks/{source_key.replace('/', '_')}_chunk_{i}.txt"
        s3.put_object(
            Bucket=os.environ['SOURCE_BUCKET'],
            Key=chunk_key,
            Body=chunk.encode('utf-8')
        )
    
    return {
        'status': 'SUCCESS',
        'chunks_created': len(chunks),
        'source_document': source_key
    }

def extract_chunks_from_textract(textract_json):
    """Extract and chunk text from Textract output."""
    # Implementation depends on Textract response structure
    # For simplicity, returns a list of text chunks
    return ["Sample chunk 1", "Sample chunk 2"]
