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
