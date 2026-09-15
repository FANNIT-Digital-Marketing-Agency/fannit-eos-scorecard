"""Shared-secret auth for the scorecard API.

Two accepted credentials, both derived from one Secret Manager secret
(`eos-api-shared-secret`, exposed to the container as env var
EOS_SCORECARD_SECRET via cloudbuild.yaml):

1. `X-EOS-Secret` header carrying the raw secret. Used by server-side
   callers: Cloud Scheduler (weekly snapshot) and FANNIT Command's
   exec-dashboard collector (utils/exec_eos.py, env EOS_SCORECARD_SECRET).

2. A signed short-lived token (`<exp-epoch>.<hmac-sha256-hex>`) passed as
   the `token` query param. FANNIT Command mints it server-side and embeds
   it in the dashboard iframe URL; the static frontend forwards it on every
   /api call. Read-only endpoints accept it; /internal/snapshot does NOT.

Fails closed: if EOS_SCORECARD_SECRET is missing at runtime, every guarded
endpoint returns 503 rather than serving unauthenticated.
"""

import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request

TOKEN_QUERY_PARAM = "token"
SECRET_HEADER = "X-EOS-Secret"


def _secret() -> str:
    return os.environ.get("EOS_SCORECARD_SECRET", "")


def _header_ok(request: Request, secret: str) -> bool:
    provided = request.headers.get(SECRET_HEADER, "")
    return bool(provided) and hmac.compare_digest(provided, secret)


def _token_ok(token: str, secret: str) -> bool:
    """Validate `<exp-epoch>.<hex hmac_sha256(secret, exp-epoch)>`."""
    exp_str, _, sig = token.partition(".")
    if not sig or not exp_str.isdigit():
        return False
    if time.time() > int(exp_str):
        return False
    expected = hmac.new(secret.encode(), exp_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _require_configured() -> str:
    secret = _secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="auth_not_configured: EOS_SCORECARD_SECRET is not set",
        )
    return secret


def require_read_auth(request: Request) -> None:
    """Dependency for GET /api/* — header secret OR signed iframe token."""
    secret = _require_configured()
    if _header_ok(request, secret):
        return
    token = request.query_params.get(TOKEN_QUERY_PARAM, "")
    if token and _token_ok(token, secret):
        return
    raise HTTPException(status_code=401, detail="unauthorized")


def require_secret_header(request: Request) -> None:
    """Dependency for POST /internal/snapshot — header secret only."""
    secret = _require_configured()
    if not _header_ok(request, secret):
        raise HTTPException(status_code=401, detail="unauthorized")
