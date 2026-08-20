"""Website signal module — Prospector v2 Phase 4 (+ fetch-escalation follow-up).

Lightweight fetch of a prospect's homepage (and, opportunistically, a
contact page), looking for absence of booking/live-chat widgets and
phone-dependent contact pages. This is a heuristic signal, not a perfect
one — see the detection-approach notes below each check, so Andy can tune
the signature/keyword lists in scoring_config.py as he sees false
positives/negatives in real prospect sites.

Fetch escalation (three layers, cheapest/free first — see `_fetch_httpx`,
`_fetch_playwright`, `_fetch_apify_browser` below, each independently
testable and returning the same `FetchResult | None` shape):

  1. **httpx** — plain HTTP client, the original Phase 4 mechanism.
     httpx.Client with a timeout, up to 2 retries with backoff (same shape
     as prospector/http.py's retry helper, reimplemented here for httpx
     since http.py is `requests`-based and this module is the one place in
     the codebase using httpx, per the standing "no new dependencies
     beyond httpx" rule), a rotating User-Agent, and a fixed delay between
     requests to avoid hammering prospect sites.
  2. **Playwright** (headless Chromium) — added as a fallback for sites
     that block plain HTTP clients outright. NOT a new dependency: it's
     already installed for prospector/report.py's PDF pipeline (Chromium
     already cached at ~/.cache/ms-playwright), just a new *usage* here.
     Confirmed live against CVS Group's site (cvsvets.com — the site
     behind "Mildmay Veterinary Hospital", which returns HTTP 406 to
     httpx on every retry): a bare `browser.new_page()` still got 406, but
     a browser *context* with a realistic Accept/Accept-Language header
     set, en-GB locale, and the automation-controlled Blink flag disabled
     got a clean 200 with full HTML — see PLAYWRIGHT_* constants in
     scoring_config.py, tuned from that live test.
  3. **Apify** (`apify/rag-web-browser` actor, `scrapingTool:
     browser-playwright` explicitly set) — last-resort fallback if even a
     real local headless browser gets blocked. Narrow, deliberate
     re-introduction of Apify for site-fetch only — Phase 1 of the v2
     rebuild removed Apify entirely from the ad-spend model, and this
     does not reverse that decision; it's a different (and much smaller)
     use of the same APIFY_TOKEN. Costs real, if small, money — see the
     README "Site-fetch escalation" section — so this should rarely
     trigger in practice, since Playwright resolves most of what httpx
     can't. Skipped cleanly (falls through to "no signal detected") if
     APIFY_TOKEN is unset.

If all three fail (or there's no website to try), the flags are set to
"no signal detected" (no_booking=True, no_chat=True, phone_dependent=True)
— a failure to fetch is never scored as if it were a verified absence of
booking/chat, it's recorded as "we don't know." opportunity_score is
still computed from those flags like any other result (fixed — a prior
version hardcoded opportunity_score=0 here despite all three flags being
True, which silently under-scored exactly the businesses that need the
most attention; see db.py migration v12 / Andy: "the last 2 no website,
that should be a signal"). `SiteSignalResult.fetch_method` records which
layer (if any) actually succeeded, and `.no_website` distinguishes "no
website at all" from "had a website, couldn't fetch it" — so Andy can see
from a run's console output (and the `site_fetch_method`/`no_website` DB
columns) how often each case happens.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from prospector.config import APIFY_TOKEN
from prospector.db import update_business_fields
from prospector.scoring_config import (
    APIFY_ACTOR_RUN_ENDPOINT,
    APIFY_INPUT_DEFAULTS,
    APIFY_RAG_BROWSER_ACTOR_ID,
    APIFY_TIMEOUT_SECONDS,
    BOOKING_LANGUAGE_KEYWORDS,
    BOOKING_WIDGET_SIGNATURES,
    CALLBACK_PROMISE_KEYWORDS,
    CHAT_LANGUAGE_KEYWORDS,
    CHAT_WIDGET_SIGNATURES,
    CONTACT_PAGE_PATHS,
    OPPORTUNITY_SCORE_CAP,
    PHONE_PATTERN,
    PLAYWRIGHT_EXTRA_HEADERS,
    PLAYWRIGHT_LAUNCH_ARGS,
    PLAYWRIGHT_LOCALE,
    PLAYWRIGHT_TIMEOUT_MS,
    PLAYWRIGHT_VIEWPORT,
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
# Extra delay before escalating to a heavier fetch layer (Playwright launch,
# or an Apify actor run) — on top of _POLITE_DELAY_SECONDS, not instead of
# it, since a blocked httpx request shouldn't be immediately retried by a
# different mechanism against the same site.
_ESCALATION_DELAY_SECONDS = 1.5


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
class FetchResult:
    """Common return shape for all three fetch layers, so the escalation
    logic in `_fetch_with_escalation` doesn't need to know which layer
    produced the HTML."""
    html: str
    final_url: str
    method: str  # "httpx" | "playwright" | "apify"


def _fetch_httpx(client: httpx.Client, url: str) -> FetchResult | None:
    """Layer 1. Thin wrapper around `_fetch` returning the common
    FetchResult shape. Returns None on any non-2xx-ish/unreachable result
    so the caller can escalate."""
    resp = _fetch(client, url)
    if resp is None or resp.status_code >= 400:
        return None
    return FetchResult(html=resp.text, final_url=str(resp.url), method="httpx")


def _fetch_playwright(url: str) -> FetchResult | None:
    """Layer 2. Headless Chromium fetch via the Playwright install already
    used by prospector/report.py's PDF pipeline — no new dependency, just
    a new usage. Returns None (never raises) on any failure — including
    Playwright not being installed — so a missing/broken browser install
    degrades to "skip this layer," not a crash.

    A bare `browser.new_page()` was NOT sufficient to get past CVS Group's
    bot-detection in live testing (still returned HTTP 406) — a browser
    *context* with realistic Accept/Accept-Language headers, an en-GB
    locale, and the automation-controlled Blink flag disabled was required.
    See PLAYWRIGHT_* constants in scoring_config.py."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=PLAYWRIGHT_LAUNCH_ARGS)
            try:
                context = browser.new_context(
                    user_agent=random.choice(_USER_AGENTS),
                    locale=PLAYWRIGHT_LOCALE,
                    viewport=PLAYWRIGHT_VIEWPORT,
                    extra_http_headers=PLAYWRIGHT_EXTRA_HEADERS,
                )
                page = context.new_page()
                resp = page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
                if resp is None or resp.status >= 400:
                    return None
                html = page.content()
                final_url = page.url
                return FetchResult(html=html, final_url=final_url, method="playwright")
            finally:
                browser.close()
    except Exception:
        # Broad catch deliberately: any Playwright/browser-level failure
        # (navigation timeout, crashed renderer, DNS error inside the
        # browser, etc.) should degrade to "try Apify next," not blow up
        # the batch fetch.
        return None


def _fetch_apify_browser(url: str) -> FetchResult | None:
    """Layer 3, last resort. Calls Apify's `apify/rag-web-browser` actor
    (FREE pricing tier on Apify's Store — no per-actor markup, billed only
    against ordinary Apify platform compute/free-tier credits — see the
    README for the exact cost mechanics) via its run-sync-get-dataset-items
    endpoint, requesting HTML output and forcing `scrapingTool:
    browser-playwright` (the actor's *default* scraping mode is
    `raw-http`, a plain HTTP fetch — leaving that default would just hit
    the same kind of block httpx already hit, defeating the point of this
    fallback).

    Returns None (never raises) if APIFY_TOKEN is unset, the call fails,
    or the actor's dataset item doesn't contain usable HTML — same
    "degrade to no-signal, don't crash the batch" contract as the other
    two layers."""
    if not APIFY_TOKEN:
        return None

    endpoint = APIFY_ACTOR_RUN_ENDPOINT.format(actor_id=APIFY_RAG_BROWSER_ACTOR_ID)
    payload = {"query": url, **APIFY_INPUT_DEFAULTS}

    try:
        resp = httpx.post(
            endpoint,
            params={"token": APIFY_TOKEN},
            json=payload,
            timeout=APIFY_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        items = resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
        return None

    if not items:
        return None

    item = items[0]
    html = item.get("html")
    if not html:
        return None
    final_url = (item.get("metadata") or {}).get("url") or url
    return FetchResult(html=html, final_url=final_url, method="apify")


def _fetch_with_escalation(client: httpx.Client, url: str) -> FetchResult | None:
    """The three-layer escalation chain: httpx -> Playwright -> Apify.
    Returns as soon as one layer succeeds; returns None only if all three
    fail (or aren't available), which the caller treats as "unreachable"
    and scores accordingly (no_booking/no_chat/phone_dependent=True,
    opportunity_score=0 — never faked off unverified data)."""
    result = _fetch_httpx(client, url)
    if result is not None:
        return result

    print(f"  [site] httpx blocked/failed for {url} — escalating to Playwright...")
    time.sleep(_ESCALATION_DELAY_SECONDS)
    result = _fetch_playwright(url)
    if result is not None:
        return result

    print(f"  [site] Playwright also failed for {url} — escalating to Apify (last resort, costs money)...")
    time.sleep(_ESCALATION_DELAY_SECONDS)
    result = _fetch_apify_browser(url)
    if result is not None:
        return result

    print(f"  [site] all three fetch layers failed for {url} — recording as unreachable, no signal.")
    return None


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
    fetch_method: str | None = None  # "httpx" | "playwright" | "apify" | None (unreachable/no website)
    no_website: bool = False  # True only when businesses.website was NULL — see db.py migration v12


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
        # No website at all — Andy: "that should be a signal as both of us
        # do websites, Ayse is the SEO expert". Previously this scored
        # opportunity_score=0 despite all three flags being True (a bug:
        # the early-return skipped the SITE_FLAG_POINTS calc entirely).
        # This is the STRONGEST opportunity signal, not the weakest — max
        # the score to match the flags, and set no_website so reports can
        # call it out as a dual-service (website/SEO + AI answering) lead.
        return SiteSignalResult(
            business_id=business["id"], name=business["name"], fetched=False,
            no_booking=True, no_chat=True, phone_dependent=True,
            opportunity_score=min(SITE_FLAG_POINTS * 3, OPPORTUNITY_SCORE_CAP),
            no_website=True,
        )

    url = website if website.startswith("http") else f"https://{website}"
    home_result = _fetch_with_escalation(client, url)
    if home_result is None:
        # Has a website, just couldn't be fetched (blocked/down/timeout) —
        # same flag values as no-website since we genuinely don't know,
        # but no_website stays False: this business isn't necessarily
        # missing a site, so don't conflate the two signals in reports.
        return SiteSignalResult(
            business_id=business["id"], name=business["name"], fetched=False,
            no_booking=True, no_chat=True, phone_dependent=True,
            opportunity_score=min(SITE_FLAG_POINTS * 3, OPPORTUNITY_SCORE_CAP),
        )

    home_html = home_result.html
    combined_html = home_html
    email = _extract_email(home_html)
    fetch_method = home_result.method

    # Try a contact page too — phone_dependent specifically concerns the
    # contact page per spec; the homepage is used as a fallback if none of
    # the common contact paths resolve. Uses the same three-layer
    # escalation as the homepage fetch (a site that blocks httpx on the
    # homepage will typically block it on /contact too).
    base = home_result.final_url.rstrip("/")
    for path in CONTACT_PAGE_PATHS:
        time.sleep(_POLITE_DELAY_SECONDS)
        contact_result = _fetch_with_escalation(client, base + path)
        if contact_result is not None:
            combined_html += "\n" + contact_result.html
            if not email:
                email = _extract_email(contact_result.html)
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
        opportunity_score=score, email=email, fetch_method=fetch_method,
    )


def fetch_all(conn, run_id: int | None = None, business_id: int | None = None, refresh: bool = False, limit: int | None = None) -> list[SiteSignalResult]:
    """Fetch + score site signals for businesses that haven't been checked
    yet (or all matching, if refresh=True).

    Deliberately NOT filtered to `website IS NOT NULL` — a business with no
    website at all still needs its opportunity_score/no_website/flags set
    (fetch_and_score_site() already handles website=None correctly, scoring
    it as the strongest opportunity signal; this query used to filter those
    rows out before they ever reached that logic, leaving them permanently
    unscored — see db.py migration v12 / Andy: "the last 2 no website,
    that should be a signal")."""
    query = "SELECT * FROM businesses WHERE 1=1"
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
            if result.fetched:
                status = "fetched via " + result.fetch_method
            elif result.no_website:
                status = "NO WEBSITE"
            else:
                status = "UNREACHABLE"
            print(f"  [site] #{result.business_id} {result.name}: {status}")

            fields = {
                "no_booking": int(result.no_booking),
                "no_chat": int(result.no_chat),
                "phone_dependent": int(result.phone_dependent),
                "opportunity_score": result.opportunity_score,
                "site_checked_at": _utcnow(),
                "site_fetch_method": result.fetch_method,
                "no_website": int(result.no_website),
            }
            if result.email:
                fields["email"] = result.email
            update_business_fields(conn, biz["id"], fields)

    return results
