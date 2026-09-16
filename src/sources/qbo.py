"""QBO financials via FANNIT Command (FANNIT realm only).

This service NEVER talks to Intuit. FANNIT Command owns the production QBO
connection and exposes the three scorecard figures over the SAME shared secret
this service already holds (env EOS_SCORECARD_SECRET, secret
`eos-api-shared-secret`). Re-authing Intuit here would invalidate Command's
refresh token, so there is deliberately no Intuit client anywhere in this repo.

Endpoint (see config.COMMAND_FINANCIALS_URL):

    GET .../executive/api/financials?start=YYYY-MM-DD&end=YYYY-MM-DD
    Header: X-EOS-Secret: <EOS_SCORECARD_SECRET>
    -> {"cash_collected": ..., "ar_total": ..., "ar_past_30": ...,
        "cash_on_hand": ..., "start": ..., "end": ...}

Semantics: cash_collected = cash-basis P&L total Income for [start, end];
ar_past_30 = AgedReceivables 31+ overdue as of `end`; cash_on_hand = Balance
Sheet total bank accounts as of `end`. Command returns 400 if `end` is in the
future, so callers must clamp the current week's end to today.

Only FANNIT (config.QBO_AGENCIES). HMC/TMSA/IPA render the trio "unavailable".
"""

from __future__ import annotations

import logging
import os
import time

import requests

from ..config import COMMAND_FINANCIALS_URL, COMMAND_SECRET_ENV

log = logging.getLogger("eos-scorecard.qbo")

HTTP_TIMEOUT = 30
_RETRIES = 3
_CACHE_TTL_SECONDS = 600  # our own cache on top of Command's 15-min window cache
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _secret() -> str:
    return os.environ.get(COMMAND_SECRET_ENV, "")


def financials(start_iso: str, end_iso: str) -> dict | None:
    """Financial figures for [start_iso, end_iso], or None on failure.

    Retries transient Command/QBO failures (502/503, network) with backoff,
    then gives up so the caller renders the KPI "unavailable". 400/401 are not
    retried. Successful responses are cached in-process by (start, end).
    """
    secret = _secret()
    if not secret:
        log.warning("QBO: %s not set; financials unavailable", COMMAND_SECRET_ENV)
        return None

    key = (start_iso, end_iso)
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]

    backoff = 1.0
    for attempt in range(_RETRIES):
        try:
            r = requests.get(
                COMMAND_FINANCIALS_URL,
                headers={"X-EOS-Secret": secret},
                params={"start": start_iso, "end": end_iso},
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            log.warning(
                "QBO command network error (%s..%s) attempt %d/%d: %s",
                start_iso, end_iso, attempt + 1, _RETRIES, exc,
            )
            time.sleep(backoff)
            backoff *= 2
            continue

        if r.status_code == 200:
            try:
                body = r.json()
            except ValueError:
                log.warning("QBO command 200 but non-JSON body")
                return None
            _cache[key] = (now, body)
            return body

        if r.status_code in (502, 503):
            log.warning(
                "QBO command %s (%s..%s) attempt %d/%d",
                r.status_code, start_iso, end_iso, attempt + 1, _RETRIES,
            )
            time.sleep(backoff)
            backoff *= 2
            continue

        # 400 (bad dates), 401 (bad secret): not retryable.
        log.warning(
            "QBO command non-retryable %s (%s..%s): %s",
            r.status_code, start_iso, end_iso, (r.text or "")[:200],
        )
        return None

    return None
