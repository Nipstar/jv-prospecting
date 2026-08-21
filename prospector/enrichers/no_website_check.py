"""Confirmation search for businesses flagged `no_website=1`.

`no_website=1` is set purely from Google Places' `website` field being
empty (enrichers/site.py) — that's not a verified fact, it's "Places
didn't have a website on file for this listing." Places data can be
missing or stale even when the business genuinely has a site (never
filled in on their GBP, or the business is easier to find under organic
search than Places). Andy: "Do we do a brand name search to confirm
there is no website" — answer was no, so this module adds one.

Design: "no guessing" (Andy's explicit instruction) — a targeted SerpAPI
organic search for `"{business name}" {town}`, then a STRICT normalized-
name-to-domain match against the results. If nothing meets the strict bar,
the check is inconclusive-negative (genuinely couldn't find a site), not a
confident "confirmed no website" — both outcomes are recorded distinctly
(see `no_website_confirmed` column, migration v13) so a low-confidence
non-match is never silently treated as proof.

Match rule (deliberately conservative — a false "found a site" would
wrongly downgrade a real no-website business and lose the strongest
signal we have; a false negative just leaves it as "no website", which
was already the default): normalize the business name by lowercasing,
stripping common suffixes (ltd/limited/llp/plc/&), and splitting into
significant (len >= 3) words. A candidate domain counts as a confident
match ONLY if its slug (domain minus TLD, hyphens removed) contains the
first two significant words of the name concatenated together, or the
full concatenated name if it's only one significant word. This is
stricter than discovery/organic.py's dedupe logic on purpose — that
module is finding NEW businesses (a loose match is fine, worst case is
an extra row Andy can exclude); this module is DOWNGRADING an existing
strong signal (a loose match here would be actively harmful).

On a confident match: sets `website`/`domain` to the found candidate,
`no_website=0`, `no_website_confirmed=0` (0 = "confirmed a website exists
after all"), and re-runs `enrichers/site.py`'s scoring for that business
so opportunity_score reflects reality — this business was miscategorized,
not just "flag it and move on."

On no confident match: sets `no_website_confirmed=1` (1 = "checked,
genuinely couldn't find one") and leaves everything else untouched — the
no_website=1 / max opportunity_score signal stands, now with actual
verification behind it instead of just an empty Places field.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from prospector.config import GL, GOOGLE_DOMAIN, HL, SERPAPI_BASE_URL, SERPAPI_KEY
from prospector.db import update_business_fields
from prospector.discovery.organic import _is_excluded
from prospector.http import ApiError, get
from prospector.places_client import _domain_from_url


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

_RESULTS_TO_CHECK = 10  # one page of organic results is enough for a targeted "{name} {town}" query
_MIN_SIGNIFICANT_WORD_LEN = 3
_NAME_SUFFIX_WORDS = {"ltd", "limited", "llp", "plc", "co", "and", "the", "services", "service"}
_POLITE_DELAY_SECONDS = 0.4


def _significant_words(name: str) -> list[str]:
    lowered = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    words = [w for w in lowered.split() if len(w) >= _MIN_SIGNIFICANT_WORD_LEN and w not in _NAME_SUFFIX_WORDS]
    return words


def _confident_domain_match(business_name: str, domain: str | None) -> bool:
    """Strict match — see module docstring. False on anything ambiguous."""
    if not domain:
        return False
    words = _significant_words(business_name)
    if not words:
        return False
    needle = "".join(words[:2])  # first two significant words concatenated, or just one if that's all there is
    slug = domain.split(".")[0].replace("-", "").lower()
    return len(needle) >= 5 and needle in slug  # len>=5 guard: avoid trivially short/coincidental substrings


def confirm_no_website(business: dict) -> dict[str, Any]:
    """One SerpAPI organic search for `business`. Returns a dict of
    fields to write back to the DB (never raises on a bad/empty result —
    fails to "checked, no confident match" rather than crashing a batch,
    same fail-soft posture as the rest of the enrichers)."""
    name = business.get("name")
    town = business.get("town") or ""
    result: dict[str, Any] = {"no_website_checked_at": _utcnow()}

    if not name or not SERPAPI_KEY:
        result["no_website_confirmed"] = 1
        return result

    query = f'"{name}" {town}'.strip()
    try:
        resp = get(SERPAPI_BASE_URL, params={
            "engine": "google",
            "q": query,
            "google_domain": GOOGLE_DOMAIN,
            "hl": HL,
            "gl": GL,
            "num": _RESULTS_TO_CHECK,
            "api_key": SERPAPI_KEY,
        })
        if resp.status_code != 200:
            raise ApiError(f"SerpAPI no-website check failed: HTTP {resp.status_code}")
        organic_results = (resp.json().get("organic_results") or [])[:_RESULTS_TO_CHECK]
    except (ApiError, httpx.RequestError, ValueError) as exc:
        print(f"  [no-website-check] search failed for {name!r}: {exc} — leaving as unconfirmed, not guessing.")
        result["no_website_confirmed"] = 1
        return result

    for item in organic_results:
        link = item.get("link")
        domain = _domain_from_url(link)
        if not domain or _is_excluded(domain):
            continue  # aggregator/directory domain (organic.py's EXCLUDED_DOMAINS) — never a candidate "their site"
        if _confident_domain_match(name, domain):
            print(f"  [no-website-check] {name!r}: confident match found — {link}")
            result.update({
                "website": link,
                "domain": domain,
                "no_website": 0,
                "no_website_confirmed": 0,
            })
            return result

    result["no_website_confirmed"] = 1
    return result


def confirm_all(conn, run_id: int | None = None, refresh: bool = False, limit: int | None = None) -> list[dict]:
    """Batch-run confirm_no_website() over businesses flagged
    no_website=1 that haven't been checked yet (or all matching, if
    refresh=True). Re-scores via enrichers/site.py for any confident
    match found, so opportunity_score reflects the corrected data."""
    from prospector.enrichers.site import fetch_and_score_site  # local import: avoids a site.py <-> here import cycle

    query = "SELECT * FROM businesses WHERE no_website = 1"
    params: list = []
    if run_id is not None:
        query += " AND run_id = ?"
        params.append(run_id)
    if not refresh:
        query += " AND no_website_checked_at IS NULL"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query, params).fetchall()

    results = []
    with httpx.Client() as client:
        for row in rows:
            biz = dict(row)
            outcome = confirm_no_website(biz)
            update_business_fields(conn, biz["id"], outcome)
            print(
                f"  [no-website-check] #{biz['id']} {biz['name']}: "
                + ("website found, rescoring" if outcome.get("no_website") == 0 else "no confident match, staying flagged")
            )
            if outcome.get("no_website") == 0:
                # Miscategorized — re-fetch/re-score against the newly-found site so
                # opportunity_score/no_booking/no_chat/phone_dependent reflect reality.
                biz.update(outcome)
                site_result = fetch_and_score_site(client, biz)
                update_business_fields(conn, biz["id"], {
                    "no_booking": int(site_result.no_booking),
                    "no_chat": int(site_result.no_chat),
                    "phone_dependent": int(site_result.phone_dependent),
                    "opportunity_score": site_result.opportunity_score,
                    "site_checked_at": _utcnow(),
                    "site_fetch_method": site_result.fetch_method,
                })
            results.append({"business_id": biz["id"], "name": biz["name"], **outcome})
            time.sleep(_POLITE_DELAY_SECONDS)
    return results
