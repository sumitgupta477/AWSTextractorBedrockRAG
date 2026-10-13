import json
import boto3
import os

bedrock = boto3.client('bedrock-agent-runtime')
knowledge_base_id = os.environ['KNOWLEDGE_BASE_ID']
model_id = os.environ['MODEL_ID']

def lambda_handler(event, context):
    try:
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        user_query = body.get('query', '')
        
        if not user_query:
            return respond(400, {'error': 'query parameter required'})
        
        # Call Bedrock RetrieveAndGenerate API
        response = bedrock.retrieve_and_generate(
            input={'text': user_query},
            retrieveAndGenerateConfiguration={
                'type': 'KNOWLEDGE_BASE',
                'knowledgeBaseConfiguration': {
                    'knowledgeBaseId': knowledge_base_id,
                    'modelArn': model_arn()
                }
            }
        )
        
        return respond(200, {
            'answer': response['output']['text'],
            'citations': response.get('citations', [])
        })
    
    except json.JSONDecodeError:
        return respond(400, {'error': 'request body must be valid JSON'})
    except Exception as e:
        # Log the details to CloudWatch, but don't expose internals to API callers.
        print(f"Query failed: {e!r}")
        return respond(500, {'error': 'query failed'})

def model_arn():
    """Accept either a full model / inference-profile ARN or a plain foundation model ID."""
    if model_id.startswith('arn:'):
        return model_id
    region = os.environ.get('AWS_REGION', 'us-east-1')
    return f'arn:aws:bedrock:{region}::foundation-model/{model_id}'

def respond(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body)
    }
