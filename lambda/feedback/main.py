"""Feedback Lambda: records a thumbs-up / thumbs-down rating for an answer.

POST /feedback  {"request_id": "<from the /query response>", "rating": "up" | "down", "comment": "optional"}

Each rating is written as one log line in CloudWatch Embedded Metric Format,
so CloudWatch turns it into FeedbackPositive / FeedbackNegative metrics
automatically (no extra API calls), and the same line can be joined with the
query Lambda's logs on request_id in Logs Insights.
"""

import json
import os
import re
import time

NAMESPACE = os.environ.get("METRIC_NAMESPACE", "RAG")
PROJECT = os.environ.get("PROJECT_NAME", "rag")
MAX_COMMENT_CHARS = 500
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return respond(400, {"error": "request body must be valid JSON"})
    if not isinstance(body, dict):
        return respond(400, {"error": "request body must be a JSON object"})

    request_id = str(body.get("request_id", ""))
    rating = str(body.get("rating", "")).lower()
    comment = body.get("comment") or ""

    if not REQUEST_ID_PATTERN.match(request_id):
        return respond(400, {"error": "request_id from the /query response is required"})
    if rating not in ("up", "down"):
        return respond(400, {"error": "rating must be 'up' or 'down'"})
    if not isinstance(comment, str):
        return respond(400, {"error": "comment must be a string"})

    print(json.dumps(emf_record(request_id, rating, comment[:MAX_COMMENT_CHARS])))
    return respond(202, {"status": "recorded"})


def emf_record(request_id, rating, comment):
    return {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [["Project"]],
                "Metrics": [
                    {"Name": "FeedbackPositive", "Unit": "Count"},
                    {"Name": "FeedbackNegative", "Unit": "Count"},
                ],
            }],
        },
        "Project": PROJECT,
        "FeedbackPositive": 1 if rating == "up" else 0,
        "FeedbackNegative": 1 if rating == "down" else 0,
        "event": "feedback",
        "request_id": request_id,
        "rating": rating,
        "comment": comment,
    }


def respond(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body),
    }
