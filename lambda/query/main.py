import json
import random
import time
from datetime import datetime, timezone
import boto3
import os

bedrock = boto3.client('bedrock-agent-runtime')
knowledge_base_id = os.environ['KNOWLEDGE_BASE_ID']
model_id = os.environ['MODEL_ID']
guardrail_id = os.environ.get('GUARDRAIL_ID', '')
guardrail_version = os.environ.get('GUARDRAIL_VERSION', '')
# Online evaluation: a random share of answered questions is saved for the nightly LLM judge.
sample_bucket = os.environ.get('EVAL_SAMPLE_BUCKET', '')
sample_rate = float(os.environ.get('EVAL_SAMPLE_RATE', '0') or 0)
s3 = boto3.client('s3') if sample_bucket else None

def lambda_handler(event, context):
    started = time.perf_counter()
    request_id = getattr(context, 'aws_request_id', None)
    try:
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        user_query = body.get('query', '')
        
        if not user_query:
            return respond(400, {'error': 'query parameter required'})
        
        # Call Bedrock RetrieveAndGenerate API
        kb_config = {
            'knowledgeBaseId': knowledge_base_id,
            'modelArn': model_arn()
        }
        if guardrail_id:
            kb_config['generationConfiguration'] = {
                'guardrailConfiguration': {
                    'guardrailId': guardrail_id,
                    'guardrailVersion': guardrail_version
                }
            }

        response = bedrock.retrieve_and_generate(
            input={'text': user_query},
            retrieveAndGenerateConfiguration={
                'type': 'KNOWLEDGE_BASE',
                'knowledgeBaseConfiguration': kb_config
            }
        )
        
        citations = response.get('citations', [])
        log_event('query_succeeded', request_id, started,
                  query_chars=len(user_query),
                  answer_chars=len(response['output']['text']),
                  citations=len(citations),
                  guardrail_action=response.get('guardrailAction', 'NONE'),
                  references=sum(len(c.get('retrievedReferences', [])) for c in citations))

        if sample_bucket and random.random() < sample_rate:
            save_eval_sample(request_id, user_query, response['output']['text'], citations,
                             response.get('guardrailAction', 'NONE'), started)

        return respond(200, {
            'answer': response['output']['text'],
            'citations': citations
        })
    
    except json.JSONDecodeError:
        return respond(400, {'error': 'request body must be valid JSON'})
    except Exception as e:
        # Log the details to CloudWatch, but don't expose internals to API callers.
        log_event('query_failed', request_id, started, error_type=type(e).__name__, error=repr(e))
        return respond(500, {'error': 'query failed'})

def save_eval_sample(request_id, question, answer, citations, guardrail_action, started):
    """Store one question/answer/passages record for the nightly LLM judge.

    Saved to an encrypted bucket with a short expiry. Failures are logged and
    swallowed so sampling can never break a user's request.
    """
    try:
        now = datetime.now(timezone.utc)
        passages = []
        for citation in citations:
            for ref in citation.get('retrievedReferences', []):
                text = ref.get('content', {}).get('text')
                if text and text not in passages:
                    passages.append(text)
        record = {
            'request_id': request_id,
            'timestamp': now.isoformat(),
            'model': model_id,
            'guardrail_action': guardrail_action,
            'latency_ms': round((time.perf_counter() - started) * 1000),
            'question': question,
            'answer': answer,
            'passages': passages,
        }
        s3.put_object(
            Bucket=sample_bucket,
            Key=f"samples/{now:%Y/%m/%d}/{request_id or now.timestamp()}.json",
            Body=json.dumps(record).encode('utf-8'),
            ContentType='application/json',
        )
    except Exception as e:
        print(json.dumps({'event': 'eval_sample_failed', 'request_id': request_id, 'error': repr(e)}))

def log_event(event_name, request_id, started, **fields):
    """Emit one JSON log line, queryable with CloudWatch Logs Insights.

    The question text itself is not logged, only its size, to avoid storing user data.
    """
    print(json.dumps({
        'event': event_name,
        'request_id': request_id,
        'model': model_id,
        'latency_ms': round((time.perf_counter() - started) * 1000),
        **fields,
    }))

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
