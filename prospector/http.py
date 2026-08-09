"""Shared HTTP-with-retry helper for all external API calls.

Every external call in prospector goes through here so retry/backoff
behaviour (max 2 retries, exponential backoff) is consistent and defined in
one place, per the error-handling requirement in the spec.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from prospector.config import BACKOFF_BASE_SECONDS, MAX_RETRIES


class ApiError(Exception):
    """Raised when an external API call fails after all retries."""


def request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int = MAX_RETRIES,
    backoff_base: float = BACKOFF_BASE_SECONDS,
    **kwargs: Any,
) -> requests.Response:
    """requests.request wrapped with retry + exponential backoff.

    Retries on network errors, timeouts, and 5xx/429 responses. Raises
    ApiError with the last error after max_retries is exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise ApiError(f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:300]}")
            return resp
        except (requests.RequestException, ApiError) as exc:
            last_exc = exc
            if attempt < max_retries:
                sleep_for = backoff_base * (2 ** attempt)
                time.sleep(sleep_for)
            continue
    raise ApiError(f"{method} {url} failed after {max_retries + 1} attempts: {last_exc}") from last_exc


def get(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("POST", url, **kwargs)
