"""API Gateway (HTTP API) Lambda authorizer.

Allows a request only when its x-api-key header matches the key Terraform
generated. Uses the simple-response format: {"isAuthorized": true|false}.
"""

import hmac
import os

API_KEY = os.environ["API_KEY"]


def lambda_handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    provided = headers.get("x-api-key", "")
    # Constant-time comparison so the key can't be guessed from response timing.
    return {"isAuthorized": bool(provided) and hmac.compare_digest(provided, API_KEY)}
