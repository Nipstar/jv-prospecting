"""Centralised, tunable thresholds/keyword lists for Prospector v2's two
enrichment scores — `review_target_score` (enrichers/reviews.py, Phase 3)
and `opportunity_score` (enrichers/site.py, Phase 4). Kept in one place per
the standing project rule ("all thresholds configurable, not hardcoded
magic numbers scattered in code") so Andy can tune without hunting through
module internals.
"""
from __future__ import annotations

# --- review_target_score (0-100, capped) ------------------------------------
#
# Andy's spec: "review_count < 20: +40. 20-50: +20. Over 100: 0." — leaving
# an explicit gap for 50-100. We interpolate a third, lower tier to avoid a
# cliff-edge at 50 (a 51-review business scoring the same as a 400-review
# one would be a worse signal than a smooth taper): 50-100 gets +10. This is
# the one place in the spec Andy flagged as ambiguous ("use sensible
# interpolation/tiering, document the exact bands you chose") — bands below.
REVIEW_COUNT_BANDS = [
    # (min_inclusive, max_inclusive_or_None, points)
    (0, 19, 40),
    (20, 50, 20),
    (51, 100, 10),
    (101, None, 0),
]

# avg_rating: "between 3.0 and 4.2: +30. Below 3.0: +15." — 4.2+ (a strong,
# not-a-target rating) scores 0, implicit from the spec (not called out, but
# the only sensible reading — a 4.6-rated firm isn't a weak-review target).
RATING_BAND_MID_LOW = 3.0
RATING_BAND_MID_HIGH = 4.2
RATING_BAND_MID_POINTS = 30
RATING_BAND_LOW_POINTS = 15  # below RATING_BAND_MID_LOW

HAS_NEGATIVE_RECENT_POINTS = 20  # any review <=2 stars in the returned set
NEGATIVE_RECENT_STAR_THRESHOLD = 2

WEAK_GBP_POINTS = 10  # no Google listing found, or unclaimed-looking (no website AND no hours)

MISSED_CALL_EVIDENCE_POINTS = 15  # negative review text matches a missed-call keyword
REVIEW_TARGET_SCORE_CAP = 100

# Simple case-insensitive substring match against negative (<=2 star)
# review snippets returned by Places Details. Places returns at most 5
# reviews per fetch — see the module docstring in enrichers/reviews.py for
# the coverage caveat Andy flagged.
MISSED_CALL_KEYWORDS: list[str] = [
    "phone",
    "call",
    "calls",
    "calling",
    "called",
    "answer",
    "answering",
    "answered",
    "unanswered",
    "response",
    "respond",
    "responded",
    "never got back",
    "did not get back",
    "didn't get back",
    "no reply",
    "voicemail",
]

# --- opportunity_score (Phase 4, enrichers/site.py) --------------------------
#
# Each detected flag (no_booking, no_chat, phone_dependent) adds this many
# points, capped separately from review_target_score (kept as a distinct
# score per Andy's spec, not folded into review_target_score).
SITE_FLAG_POINTS = 5
OPPORTUNITY_SCORE_CAP = 100

# Common booking-widget script/iframe/domain signatures — substring match
# against fetched HTML. Not exhaustive; documented here so Andy can extend
# it as he finds more platforms among prospects.
BOOKING_WIDGET_SIGNATURES: list[str] = [
    "calendly.com",
    "acuityscheduling.com",
    "squareup.com/appointments",
    "setmore.com",
    "simplybook.me",
    "bookwhen.com",
    "bookings.jamespot",
    "10to8.com",
    "fresha.com",
    "treatwell",
    "booksy.com",
    "cliniko.com",
    "zenoti.com",
    "widget.mindbodyonline.com",
]

# Common live-chat widget script/domain signatures.
CHAT_WIDGET_SIGNATURES: list[str] = [
    "intercom.io",
    "widget.intercom.io",
    "js.driftt.com",
    "drift.com",
    "embed.tawk.to",
    "tawk.to",
    "client.crisp.chat",
    "crisp.chat",
    "static.zdassets.com",
    "zendesk",
    "livechatinc.com",
    "hubspot.com/livechat",
    "js.hs-scripts.com",
    "widget.freshchat.com",
    "freshchat",
]

# Booking/chat language keywords — used as a secondary, weaker signal
# alongside the widget-signature scan (e.g. a site might use a booking
# system without a detectable third-party widget signature, but still
# advertise "book online" in its own copy).
BOOKING_LANGUAGE_KEYWORDS: list[str] = ["book online", "book now", "book an appointment", "online booking"]
CHAT_LANGUAGE_KEYWORDS: list[str] = ["live chat", "chat with us", "chat now"]

# Callback-promise wording — presence of any of these on a contact page
# means we do NOT flag phone_dependent even if the page is otherwise just a
# phone number + form.
CALLBACK_PROMISE_KEYWORDS: list[str] = [
    "call you back",
    "call back",
    "callback",
    "we'll call you",
    "request a call",
    "arrange a call",
]

# Common contact-page URL suffixes tried in order, in addition to the
# homepage itself, when looking for phone_dependent signal.
CONTACT_PAGE_PATHS: list[str] = ["/contact", "/contact-us", "/contactus", "/get-in-touch"]

# Simple UK-ish phone number pattern for detecting "shows a phone number"
# on the contact page — loose on purpose (landlines, mobiles, +44, 0800 etc).
PHONE_PATTERN = r"(\+44\s?\d[\d\s]{8,12}|0\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4})"

# --- Site-fetch escalation (Phase 4 follow-up) -------------------------------
#
# httpx (plain HTTP client) gets outright blocked by anti-bot protection on
# some corporate sites — confirmed live against CVS Group's site
# (cvsvets.com), which returns HTTP 406 to every httpx retry regardless of
# User-Agent. Andy asked for two escalating fallback layers instead of
# giving up after httpx: a headless-browser fetch (Playwright — already a
# project dependency, reused from prospector/report.py's PDF pipeline, not
# a new install), and, only if that also fails, a paid last-resort browser
# fetch via Apify.
#
# Cost note for Andy: Playwright is free/local compute (just slower — a
# real Chromium launch+render vs. httpx's plain GET), so it costs nothing
# beyond CPU time and is expected to resolve most of what httpx can't.
# Apify is the one paid step in this chain. It should rarely trigger.

# Playwright fallback (layer 2). A bare `browser.new_page()` was NOT enough
# to get past CVS Group's block in live testing — it still returned 406.
# A browser *context* with a realistic Accept/Accept-Language header set,
# an en-GB locale, and the automation-controlled Blink flag disabled was
# required and got a clean 200 with full HTML in testing. Kept here (not
# hardcoded in site.py) so Andy can tune if another site needs a different
# header set.
PLAYWRIGHT_TIMEOUT_MS = 20000
PLAYWRIGHT_LOCALE = "en-GB"
PLAYWRIGHT_VIEWPORT = {"width": 1366, "height": 768}
PLAYWRIGHT_EXTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
PLAYWRIGHT_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]

# Apify fallback (layer 3, last resort). Uses the "RAG Web Browser" actor
# (apify/rag-web-browser) — chosen because Apify's own pricing page lists
# it under "FREE" pricing tier (no per-actor markup on top of platform
# compute; billed only against Apify's plan/free-tier compute credits,
# same as any other actor run — see README "Site-fetch escalation" section
# for the exact cost mechanics Andy should know about before this runs at
# volume). `scrapingTool: browser-playwright` is required explicitly —
# the actor's *default* scraping mode is `raw-http` (a plain HTTP fetch,
# same class of request as httpx, which would just hit the same 406 block
# again), so leaving it on the default silently defeats the point of this
# fallback layer.
APIFY_RAG_BROWSER_ACTOR_ID = "apify~rag-web-browser"
APIFY_ACTOR_RUN_ENDPOINT = (
    "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
)
APIFY_TIMEOUT_SECONDS = 60.0
APIFY_INPUT_DEFAULTS = {
    "outputFormats": ["html"],
    "scrapingTool": "browser-playwright",
    "requestTimeoutSecs": 40,
}

# --- Yell.com discovery source (prospector/discovery/yell.py) ---------------
#
# Confirmed real and functional via Apify's actor search (Andy said one
# existed; verified rather than assumed — see README "Yell.com discovery
# source"): jungle_synthesizer/yell-uk-business-directory-scraper. Free
# pricing tier, last modified 2026-07-31 (actively maintained), input is
# {keywords, location, maxItems}. Reuses the exact same run-sync-get-
# dataset-items REST pattern as the Apify site-fetch fallback above
# (APIFY_ACTOR_RUN_ENDPOINT), just a different actor ID/input/timeout.
#
# Live-tested behaviour worth knowing: the actor solves Yell's Cloudflare
# challenge itself (18-45s typical, up to ~85s across 3 internal retry
# attempts), so a generous client-side timeout is needed. It also 404s
# cleanly (raises inside the actor, run status FAILED) for location
# strings Yell's own site doesn't recognise as a location page — "South
# London" failed consistently in live testing, "London" and "Croydon"
# succeeded — the same class of "colloquial sub-region vs. formal
# place-name" issue as the Places regionCode fix, just surfacing as a
# hard failure instead of silent wrong-country leakage. yell.py fails
# soft (returns [], logs a warning) rather than raising into discover_run,
# so one bad location string never blocks the Places-sourced pass in the
# same combined run.
APIFY_YELL_ACTOR_ID = "jungle_synthesizer~yell-uk-business-directory-scraper"
APIFY_YELL_TIMEOUT_SECONDS = 240.0
APIFY_YELL_MAX_ITEMS_CAP = 200  # sanity ceiling passed as the actor's own maxItems input

# --- Checkatrade.com discovery source (prospector/discovery/checkatrade.py) -
#
# Added to replace/supplement Yell.com as a UK trade-directory source,
# since the Yell actor above is confirmed broken for multi-word keywords
# (see README "Known limitations — multi-source discovery"). Selected via
# Apify's actor-search API, the same verification process the Yell actor
# originally got — not assumed. Live-tested (2026-08-14) with real calls
# before being wired in:
#   - trev0n/checkatrade-scraper: 46 total users/12 monthly (modest but
#     real, and the input schema — trade+location OR arbitrary searchUrls,
#     requirePhone/minRating/minReviews filters, extractReviews toggle —
#     is by far the most prospecting-shaped of the ~9 Checkatrade actors
#     in the Store). Live-tested with {trade:"Air-Conditioning-Installation",
#     location:"South London", maxItems:3}: SUCCEEDED, 3 real structured
#     records (name, phone, website, city, areaServed, rating /10,
#     reviewsCount, accreditations) — genuinely more structured than Yell
#     or organic (has a working phone + rating on every populated record).
#     Also tested an out-of-taxonomy multi-word slug via searchUrls
#     (deliberately invalid, to probe multi-word-keyword robustness the
#     way the Yell bug was found) — actor run still SUCCEEDED with 0 items
#     rather than erroring/404ing the whole run, i.e. it fails soft on an
#     unmatched trade slug instead of hard-failing like the Yell actor
#     does on multi-word keywords. Modified 2026-08-07 (actively
#     maintained, not an abandoned actor).
#   - vulnv/checkatrade (270 total users) has more raw usage but its
#     `category` input is an opaque numeric-ID enum (~600 magic numbers,
#     no human-readable trade name) with no documented ID->trade mapping
#     and no `searchUrls` escape hatch — unusable for prospector's
#     freeform vertical terms without reverse-engineering the ID list
#     first, so it was passed over despite the higher user count.
#   - No better/fixed Yell.com actor turned up in the same search —
#     jungle_synthesizer's is still the only real Yell.com actor in the
#     Store; it remains registered as its own source (still low-value for
#     multi-word keywords, see README) rather than being replaced.
#
# Checkatrade has no official slug for prospector's freeform vertical
# terms ("heating plumbing and electrical contractors" etc. — see
# prospector/verticals.py), and Checkatrade itself only covers trade/home-
# services categories, not prospector's non-trade verticals (solicitors,
# accountants, dental, vets, funeral directors) — those will just
# harmlessly return 0 Checkatrade results, same "additional pass, not
# guaranteed to fire" contract as Yell/organic. discovery/checkatrade.py
# builds a best-effort trade slug (title-case, hyphenate the sector term)
# and feeds it via `searchUrls` (bypassing the actor's `trade` enum
# restriction — confirmed via its README that arbitrary slugs are
# accepted this way), rather than trying to hardcode a sector-term ->
# official-slug mapping for ~1600 categories.
APIFY_CHECKATRADE_ACTOR_ID = "trev0n~checkatrade-scraper"
APIFY_CHECKATRADE_TIMEOUT_SECONDS = 180.0
APIFY_CHECKATRADE_MAX_ITEMS_CAP = 100
# Checkatrade ratings are 0-10 ("bestRating" in its own data), not Google's
# 0-5 scale that scoring.py's avg_rating thresholds assume (see
# scoring_config.py's avg_rating comment). Divide by this to normalise.
CHECKATRADE_RATING_SCALE_DIVISOR = 2.0
