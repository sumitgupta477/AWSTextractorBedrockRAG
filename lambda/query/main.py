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
                    'modelArn': f'arn:aws:bedrock:{os.environ.get("AWS_REGION", "us-east-1")}::foundation-model/{model_id}'
                }
            }
        )
        
        return respond(200, {
            'answer': response['output']['text'],
            'citations': response.get('citations', [])
        })
    
    except Exception as e:
        return respond(500, {'error': str(e)})

def respond(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body)
    }
