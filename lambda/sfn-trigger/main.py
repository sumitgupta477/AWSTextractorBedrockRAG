import json
import boto3
import os

sfn = boto3.client('stepfunctions')
state_machine_arn = os.environ['STATE_MACHINE_ARN']

def lambda_handler(event, context):
    for record in event['Records']:
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        
        # Start Step Function execution
        response = sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=f"{context.aws_request_id}",
            input=json.dumps({
                'bucket': bucket,
                'key': key,
                'timestamp': context.aws_request_id
            })
        )
        print(f"Started execution {response['executionArn']} for {bucket}/{key}")
    
    return {'statusCode': 200, 'body': json.dumps('OK')}
