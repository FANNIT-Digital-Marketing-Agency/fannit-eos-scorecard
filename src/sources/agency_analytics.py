"""Agency Analytics — GA4 sessions fallback (white-label reporting.fannit.com).

Used ONLY when the GA4 pull errors (a legitimate zero-sessions week is not an
error and never triggers this). Cloud Run reads the API key from Secret
Manager (`agency-analytics-api-key`); until that secret exists this module is
inert and returns None, leaving GA4 as the sole Traffic source.

NOTE: the exact Agency Analytics reporting endpoint + response shape for
"sessions by day for a client over a date range" must be confirmed against the
account's API once the key is provisioned. The call below is written
defensively: any deviation yields None (KPI falls through to "unavailable"),
so a wrong guess here can never surface bad numbers. Wire the verified request
in `_fetch_daily_sessions` when the key lands.
"""

from __future__ import annotations

import logging

from ..config import AGENCY_ANALYTICS_CLIENT_ID, AGENCY_ANALYTICS_SECRET_NAME
from .secrets import get_secret

log = logging.getLogger("eos-scorecard.agency_analytics")

HTTP_TIMEOUT = 30


def _api_key() -> str | None:
    """The AA API key from Secret Manager, or None if not provisioned yet."""
    try:
        return get_secret(AGENCY_ANALYTICS_SECRET_NAME)
    except Exception as exc:  # noqa: BLE001  -- absent secret = inert fallback
        log.info("Agency Analytics key unavailable (%s); fallback inert", exc)
        return None


def daily_sessions(agency: str, start_iso: str, end_iso: str) -> dict[str, int] | None:
    """Sessions per day 'YYYY-MM-DD' for the agency's AA client over the range,
    or None if the fallback is not available / not yet wired.
    """
    key = _api_key()
    client_id = AGENCY_ANALYTICS_CLIENT_ID.get(agency)
    if not key or not client_id:
        return None
    return _fetch_daily_sessions(key, client_id, start_iso, end_iso)


def _fetch_daily_sessions(
    api_key: str, client_id: str, start_iso: str, end_iso: str
) -> dict[str, int] | None:
    """Placeholder for the verified Agency Analytics request.

    Returns None until the real endpoint is confirmed against the account.
    Keeping this a no-op (rather than a guess) guarantees the fallback can
    never surface incorrect Traffic numbers; GA4 stays authoritative and this
    activates only after the key + endpoint are verified.
    """
    log.info(
        "Agency Analytics fallback not yet wired (client %s, %s..%s)",
        client_id, start_iso, end_iso,
    )
    return None
