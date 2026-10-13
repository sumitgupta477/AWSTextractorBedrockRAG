import json
import os
import re
import urllib.parse

import boto3

sfn = boto3.client("stepfunctions")
state_machine_arn = os.environ["STATE_MACHINE_ARN"]


def lambda_handler(event, context):
    started = []
    for index, record in enumerate(event.get("Records", [])):
        bucket = record["s3"]["bucket"]["name"]
        # S3 event keys are URL-encoded (spaces arrive as "+"), so decode before using them.
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])

        # Execution names must be unique, at most 80 chars, and use a limited character set.
        name = re.sub(r"[^A-Za-z0-9_-]", "-", f"{context.aws_request_id}-{index}")[:80]

        response = sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=name,
            input=json.dumps({"bucket": bucket, "key": key}),
        )
        print(f"Started execution {response['executionArn']} for s3://{bucket}/{key}")
        started.append(response["executionArn"])

    return {"statusCode": 200, "body": json.dumps({"executions": started})}
