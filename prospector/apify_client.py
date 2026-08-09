"""Apify REST client — Meta ad spend and Google ad spend signals.

Called directly via the Apify REST API (POST runs, poll status, pull the
dataset) — not the Apify SDK, per the spec.

Sync/async polling policy
--------------------------
Apify's synchronous "run-and-wait" endpoints cap out at ~45s, which isn't
long enough to trust for every actor run (some ad-library scrapes take
longer). Instead we always start runs asynchronously
(``POST /v2/acts/{actor}/runs``) and poll
``GET /v2/acts/{actor}/runs/{runId}`` (the `actor-runs/{runId}` alias)
every ``SYNC_POLL_INTERVAL_SECONDS`` (5s) for up to
``SYNC_POLL_TIMEOUT_SECONDS`` (60s) total. If the run is still going after
that window we stop blocking: the caller gets back an ``ActorRunOutcome``
with ``status="pending"`` and the Apify run id, and the pipeline stashes
that run id on the current DB run row (``runs.pending_apify_runs``, a JSON
column) so ``prospector collect --run-id N`` can pick up the result later
instead of blocking the whole batch or timing out and failing it.

NULL vs 0 policy
-----------------
``fb_ads_active`` / ``google_ads_active`` are nullable ints in the schema.
NULL means "unknown / couldn't check"; 0 means "checked, confirmed not
found". Concretely:

  - actor run fails (status FAILED/ABORTED/TIMED-OUT) -> None, warning
    logged. The pipeline continues rather than aborting the whole run over
    one actor failure.
  - actor run is still running after the poll window -> None for now, run
    id stashed for later collection (see above).
  - actor run succeeds but comes back with 0 dataset items for a *single*
    business (the Facebook check, one call per business) -> None, not 0.
    These actors are known to be flaky, and an empty result for a single
    lookup is indistinguishable from "got blocked/rate limited/malformed
    query" — there's no second signal to sanity-check against, so we never
    infer a confident 0 from it.
  - actor run succeeds with a *non-empty* batch result (the Google check,
    one call covering many domains) -> any domain reported in the dataset
    is True; any domain in the request that's absent from a non-empty
    result set IS treated as a confirmed 0, because the actor demonstrably
    ran successfully and had the opportunity to report on every domain in
    the batch. Only a totally empty batch result falls back to None for
    every domain in it (same "can't tell success-with-nothing from
    silent failure" problem as the single-business case).

Actors:
  - curious_coder/facebook-ads-library-scraper (Meta ad spend)
  - scrapesage/google-ads-transparency-scraper (Google ad spend, takes a
    `domains` array directly)

Do NOT use any Checkatrade or Trustpilot actor — both were tested and are
unreliable/broken as of Aug 2026.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from prospector.config import APIFY_BASE_URL, APIFY_TOKEN
from prospector.http import ApiError, get, post

logger = logging.getLogger("prospector.apify_client")

SYNC_POLL_INTERVAL_SECONDS = 5
SYNC_POLL_TIMEOUT_SECONDS = 60  # spec: poll every 5s, up to 60s total, then fall back to async
RUNNING_STATUSES = ("READY", "RUNNING")


def _actor_path(actor: str) -> str:
    # Apify API convention: actor id in a URL path uses ~ instead of /.
    return actor.replace("/", "~")


@dataclass
class ActorRunOutcome:
    status: str  # "succeeded" | "failed" | "pending"
    items: list[dict[str, Any]]
    run_id: str | None
    apify_status: str | None = None  # raw Apify status string, for logging/debugging


def start_actor_run(actor: str, run_input: dict[str, Any]) -> dict[str, Any]:
    """Start an actor run asynchronously. Returns the raw Apify run `data` dict."""
    if not APIFY_TOKEN:
        raise ApiError("APIFY_TOKEN is not set — check .env")
    start_url = f"{APIFY_BASE_URL}/acts/{_actor_path(actor)}/runs"
    resp = post(start_url, params={"token": APIFY_TOKEN}, json=run_input, timeout=60)
    if resp.status_code not in (200, 201):
        raise ApiError(f"Apify run start failed for {actor}: HTTP {resp.status_code}: {resp.text[:300]}")
    run = resp.json().get("data") or {}
    if not run.get("id"):
        raise ApiError(f"Apify run start for {actor} returned no run id: {resp.text[:300]}")
    return run


def get_run_status(run_id: str) -> dict[str, Any]:
    """GET /v2/actor-runs/{runId} — the run-id-keyed alias for
    GET /v2/acts/{actorId}/runs/{runId}; equivalent status payload."""
    resp = get(f"{APIFY_BASE_URL}/actor-runs/{run_id}", params={"token": APIFY_TOKEN})
    if resp.status_code != 200:
        raise ApiError(f"Apify run status check failed for run {run_id}: HTTP {resp.status_code}")
    return resp.json().get("data") or {}


def get_dataset_items(dataset_id: str) -> list[dict[str, Any]]:
    resp = get(
        f"{APIFY_BASE_URL}/datasets/{dataset_id}/items",
        params={"token": APIFY_TOKEN, "format": "json", "clean": "true"},
    )
    if resp.status_code != 200:
        raise ApiError(f"Apify dataset fetch failed for dataset {dataset_id}: HTTP {resp.status_code}")
    return resp.json() or []


def run_actor(actor: str, run_input: dict[str, Any]) -> ActorRunOutcome:
    """Start an actor run and wait up to SYNC_POLL_TIMEOUT_SECONDS, polling
    every SYNC_POLL_INTERVAL_SECONDS. Never blocks past the timeout.
    """
    if not APIFY_TOKEN:
        raise ApiError("APIFY_TOKEN is not set — check .env")

    run = start_actor_run(actor, run_input)
    run_id = run.get("id")
    status = run.get("status")
    dataset_id = run.get("defaultDatasetId")

    waited = 0
    while status in RUNNING_STATUSES and waited < SYNC_POLL_TIMEOUT_SECONDS:
        time.sleep(SYNC_POLL_INTERVAL_SECONDS)
        waited += SYNC_POLL_INTERVAL_SECONDS
        run = get_run_status(run_id)
        status = run.get("status")
        dataset_id = run.get("defaultDatasetId") or dataset_id

    if status in RUNNING_STATUSES:
        logger.warning(
            "Apify run %s for %s still running after %ss sync-poll window — "
            "falling back to async: run id stashed for later collection.",
            run_id, actor, SYNC_POLL_TIMEOUT_SECONDS,
        )
        return ActorRunOutcome(status="pending", items=[], run_id=run_id, apify_status=status)

    if status != "SUCCEEDED":
        logger.warning(
            "Apify run %s for %s ended with status=%s — treating as failed/unknown, continuing pipeline.",
            run_id, actor, status,
        )
        return ActorRunOutcome(status="failed", items=[], run_id=run_id, apify_status=status)

    if not dataset_id:
        return ActorRunOutcome(status="succeeded", items=[], run_id=run_id, apify_status=status)

    items = get_dataset_items(dataset_id)
    return ActorRunOutcome(status="succeeded", items=items, run_id=run_id, apify_status=status)


def facebook_ad_library_url(business_name: str, country: str = "GB") -> str:
    q = quote(business_name)
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status=all&ad_type=all&country={country}&q={q}&search_type=keyword_unordered"
    )


def check_facebook_ads(business_name: str, country: str = "GB") -> dict[str, Any]:
    """Meta ad spend signal via curious_coder/facebook-ads-library-scraper.

    Returns dict: fb_ads_active (True / None — see module NULL-vs-0 policy;
    this single-business check never returns a confirmed 0),
    fb_ads_creative_count, fb_ads_earliest_seen, plus internal
    `_apify_status` / `_apify_run_id` for the pipeline's pending-run
    bookkeeping.
    """
    from prospector.config import APIFY_ACTOR_FB_ADS

    url = facebook_ad_library_url(business_name, country)
    run_input = {"urls": [{"url": url}], "count": 50}
    outcome = run_actor(APIFY_ACTOR_FB_ADS, run_input)

    if outcome.status == "failed":
        logger.warning("Facebook ads check failed for %r (run %s) — fb_ads_active = NULL (unknown).",
                        business_name, outcome.run_id)
        return {"fb_ads_active": None, "fb_ads_creative_count": None, "fb_ads_earliest_seen": None,
                "_apify_status": "failed", "_apify_run_id": outcome.run_id}

    if outcome.status == "pending":
        return {"fb_ads_active": None, "fb_ads_creative_count": None, "fb_ads_earliest_seen": None,
                "_apify_status": "pending", "_apify_run_id": outcome.run_id}

    items = outcome.items
    creative_count = len(items)
    if creative_count == 0:
        logger.warning(
            "Facebook ads check for %r returned 0 items (run %s) — fb_ads_active = NULL (unknown), "
            "not treated as a confirmed 0 for a single-business lookup.",
            business_name, outcome.run_id,
        )
        return {"fb_ads_active": None, "fb_ads_creative_count": None, "fb_ads_earliest_seen": None,
                "_apify_status": "empty", "_apify_run_id": outcome.run_id}

    earliest_seen = None
    for item in items:
        started = (
            item.get("startDateFormatted")
            or item.get("start_date")
            or item.get("adDeliveryStartTime")
        )
        if started and (earliest_seen is None or started < earliest_seen):
            earliest_seen = started

    return {
        "fb_ads_active": True,
        "fb_ads_creative_count": creative_count,
        "fb_ads_earliest_seen": earliest_seen,
        "_apify_status": "succeeded",
        "_apify_run_id": outcome.run_id,
    }


def _normalize_domain(raw: str) -> str:
    d = (raw or "").lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def summarize_google_items(items: list[dict[str, Any]], domains: list[str]) -> dict[str, dict[str, Any]]:
    """Fold raw scrapesage/google-ads-transparency-scraper dataset items into
    a per-domain summary. Shared by check_google_ads_batch (live sync/async
    path) and the pipeline's collect-pending-runs path (same dataset shape,
    fetched later)."""
    by_domain: dict[str, dict[str, Any]] = {d: {
        "google_ads_active": False,
        "google_ads_creative_count": 0,
        "google_ads_days_active": None,
        "google_ads_advertiser_name": None,
    } for d in domains}

    for item in items:
        domain = _normalize_domain(item.get("domain") or item.get("advertiserDomain") or "")
        if domain not in by_domain:
            match = next((d for d in domains if d in domain or domain in d), None)
            if not match:
                continue
            domain = match
        entry = by_domain[domain]
        entry["google_ads_active"] = True
        entry["google_ads_creative_count"] += 1
        days_active = item.get("daysActive") or item.get("days_active")
        if days_active and (entry["google_ads_days_active"] is None or days_active > entry["google_ads_days_active"]):
            entry["google_ads_days_active"] = days_active
        advertiser_name = item.get("advertiserName") or item.get("advertiser_name")
        if advertiser_name and not entry["google_ads_advertiser_name"]:
            entry["google_ads_advertiser_name"] = advertiser_name

    return by_domain


def check_google_ads_batch(domains: list[str], region: str = "GB") -> dict[str, dict[str, Any]]:
    """Google ad spend signal for a batch of domains via
    scrapesage/google-ads-transparency-scraper.

    Returns {domain: {google_ads_active, google_ads_creative_count,
    google_ads_days_active, google_ads_advertiser_name, _apify_status,
    _apify_run_id}}. See module docstring for the NULL-vs-0 policy — a
    domain absent from a *non-empty* successful batch result is a confirmed
    0; a wholly failed/pending/empty batch sets every domain to None.
    """
    from prospector.config import APIFY_ACTOR_GOOGLE_ADS

    domains = [d for d in domains if d]
    if not domains:
        return {}

    run_input = {"domains": domains, "region": region}
    outcome = run_actor(APIFY_ACTOR_GOOGLE_ADS, run_input)

    def _unknown(status: str) -> dict[str, dict[str, Any]]:
        return {d: {
            "google_ads_active": None,
            "google_ads_creative_count": None,
            "google_ads_days_active": None,
            "google_ads_advertiser_name": None,
            "_apify_status": status,
            "_apify_run_id": outcome.run_id,
        } for d in domains}

    if outcome.status == "failed":
        logger.warning("Google ads batch check failed (run %s) — %d domain(s) set to NULL (unknown).",
                        outcome.run_id, len(domains))
        return _unknown("failed")
    if outcome.status == "pending":
        return _unknown("pending")

    items = outcome.items
    if not items:
        logger.warning(
            "Google ads batch check returned 0 items for %d domain(s) (run %s) — set to NULL "
            "(unknown), not a confirmed 0 for the whole batch.",
            len(domains), outcome.run_id,
        )
        return _unknown("empty")

    by_domain = summarize_google_items(items, domains)
    for entry in by_domain.values():
        entry["_apify_status"] = "succeeded"
        entry["_apify_run_id"] = outcome.run_id
    return by_domain
