"""Website signal module — Prospector v2 Phase 4.

Lightweight fetch of a prospect's homepage (and, opportunistically, a
contact page) using httpx, looking for absence of booking/live-chat
widgets and phone-dependent contact pages. This is a heuristic signal, not
a perfect one — see the detection-approach notes below each check, so Andy
can tune the signature/keyword lists in scoring_config.py as he sees false
positives/negatives in real prospect sites.

Fetch politeness: httpx.Client with a timeout, up to 2 retries with
backoff (same shape as prospector/http.py's retry helper, reimplemented
here for httpx since http.py is `requests`-based and this module is the
one place in the codebase using httpx, per the standing "no new
dependencies beyond httpx" rule), a rotating User-Agent, and a fixed
delay between requests to avoid hammering prospect sites.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from prospector.db import update_business_fields
from prospector.scoring_config import (
    BOOKING_LANGUAGE_KEYWORDS,
    BOOKING_WIDGET_SIGNATURES,
    CALLBACK_PROMISE_KEYWORDS,
    CHAT_LANGUAGE_KEYWORDS,
    CHAT_WIDGET_SIGNATURES,
    CONTACT_PAGE_PATHS,
    OPPORTUNITY_SCORE_CAP,
    PHONE_PATTERN,
    SITE_FLAG_POINTS,
)

# Rotated per-request so repeated fetches to a small set of prospect sites
# don't all look identical in their server logs.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

_TIMEOUT_SECONDS = 10.0
_MAX_RETRIES = 2
_BACKOFF_BASE_SECONDS = 1.5
_POLITE_DELAY_SECONDS = 1.5  # fixed delay between requests to the same/different sites


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch(client: httpx.Client, url: str) -> httpx.Response | None:
    """GET url with retry/backoff, rotating UA. Returns None (not raises)
    on final failure — a single unreachable site shouldn't kill a batch
    fetch, same "fail one, keep going" philosophy as pipeline.py."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            headers = {"User-Agent": random.choice(_USER_AGENTS)}
            resp = client.get(url, headers=headers, timeout=_TIMEOUT_SECONDS, follow_redirects=True)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            return resp
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_BASE_SECONDS * (2 ** attempt))
            continue
    return None


def _has_any(haystack: str, needles: list[str]) -> bool:
    lowered = haystack.lower()
    return any(n.lower() in lowered for n in needles)


@dataclass
class SiteSignalResult:
    business_id: int
    name: str
    fetched: bool
    no_booking: bool
    no_chat: bool
    phone_dependent: bool
    opportunity_score: int
    email: str | None = None


def _detect_booking(html: str) -> bool:
    """no_booking = True when neither a known booking-widget signature nor
    generic booking language is found. Heuristic: iframe/script src
    signatures (Calendly, Acuity, Fresha, etc. — scoring_config.py) plus a
    weaker fallback keyword scan, since some sites roll their own booking
    form without a detectable third-party widget."""
    return not (_has_any(html, BOOKING_WIDGET_SIGNATURES) or _has_any(html, BOOKING_LANGUAGE_KEYWORDS))


def _detect_chat(html: str) -> bool:
    """no_chat = True when neither a known chat-widget signature nor
    generic chat language is found."""
    return not (_has_any(html, CHAT_WIDGET_SIGNATURES) or _has_any(html, CHAT_LANGUAGE_KEYWORDS))


def _detect_phone_dependent(html: str) -> bool:
    """phone_dependent = True when the page shows a phone number (loose UK
    pattern) and/or a <form>, but no callback-promise wording. This is
    intentionally permissive: a page with a phone number and no callback
    language is flagged even without a <form>, since 'call us' with no
    other contact route is itself phone-dependent."""
    has_phone = bool(re.search(PHONE_PATTERN, html))
    has_form = "<form" in html.lower()
    has_callback_promise = _has_any(html, CALLBACK_PROMISE_KEYWORDS)
    return (has_phone or has_form) and not has_callback_promise


def _extract_email(html: str) -> str | None:
    """Opportunistic mailto: scrape — not part of Andy's Phase 4 spec, but
    cheap to grab while we already have the HTML in hand, and Phase 5's
    export needs an `email` column. Best-effort only; leaves email blank
    if nothing found rather than guessing."""
    m = re.search(r'mailto:([^"\'?\s]+)', html, re.IGNORECASE)
    return m.group(1) if m else None


def fetch_and_score_site(client: httpx.Client, business: dict) -> SiteSignalResult:
    website = business.get("website")
    if not website:
        return SiteSignalResult(
            business_id=business["id"], name=business["name"], fetched=False,
            no_booking=True, no_chat=True, phone_dependent=True, opportunity_score=0,
        )

    url = website if website.startswith("http") else f"https://{website}"
    resp = _fetch(client, url)
    if resp is None or resp.status_code >= 400:
        return SiteSignalResult(
            business_id=business["id"], name=business["name"], fetched=False,
            no_booking=True, no_chat=True, phone_dependent=True, opportunity_score=0,
        )

    home_html = resp.text
    combined_html = home_html
    email = _extract_email(home_html)

    # Try a contact page too — phone_dependent specifically concerns the
    # contact page per spec; the homepage is used as a fallback if none of
    # the common contact paths resolve.
    base = str(resp.url).rstrip("/")
    for path in CONTACT_PAGE_PATHS:
        time.sleep(_POLITE_DELAY_SECONDS)
        contact_resp = _fetch(client, base + path)
        if contact_resp is not None and contact_resp.status_code < 400:
            combined_html += "\n" + contact_resp.text
            if not email:
                email = _extract_email(contact_resp.text)
            break

    no_booking = _detect_booking(combined_html)
    no_chat = _detect_chat(combined_html)
    phone_dependent = _detect_phone_dependent(combined_html)

    score = 0
    if no_booking:
        score += SITE_FLAG_POINTS
    if no_chat:
        score += SITE_FLAG_POINTS
    if phone_dependent:
        score += SITE_FLAG_POINTS
    score = min(score, OPPORTUNITY_SCORE_CAP)

    return SiteSignalResult(
        business_id=business["id"], name=business["name"], fetched=True,
        no_booking=no_booking, no_chat=no_chat, phone_dependent=phone_dependent,
        opportunity_score=score, email=email,
    )


def fetch_all(conn, run_id: int | None = None, business_id: int | None = None, refresh: bool = False, limit: int | None = None) -> list[SiteSignalResult]:
    """Fetch + score site signals for businesses with a website that
    haven't been checked yet (or all matching, if refresh=True)."""
    query = "SELECT * FROM businesses WHERE website IS NOT NULL"
    params: list = []
    if run_id is not None:
        query += " AND run_id = ?"
        params.append(run_id)
    if business_id is not None:
        query += " AND id = ?"
        params.append(business_id)
    if not refresh:
        query += " AND site_checked_at IS NULL"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query, params).fetchall()

    results: list[SiteSignalResult] = []
    with httpx.Client() as client:
        for i, row in enumerate(rows):
            if i > 0:
                time.sleep(_POLITE_DELAY_SECONDS)
            biz = dict(row)
            result = fetch_and_score_site(client, biz)
            results.append(result)

            fields = {
                "no_booking": int(result.no_booking),
                "no_chat": int(result.no_chat),
                "phone_dependent": int(result.phone_dependent),
                "opportunity_score": result.opportunity_score,
                "site_checked_at": _utcnow(),
            }
            if result.email:
                fields["email"] = result.email
            update_business_fields(conn, biz["id"], fields)

    return results
