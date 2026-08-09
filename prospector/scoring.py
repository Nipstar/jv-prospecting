"""Priority scoring — plain weighted function, not ML.

Weights (per spec):
  ads_both     = 40   (ads active on both Meta and Google)
  ads_one      = 20   (ads active on exactly one channel)
  pain_flag    = 25   (at least one pain-flagged review)
  independent  = 15   (not group/corporate-owned)
  review_count bonus, up to 10 (scaled, capped)

Priority bands:
  A: ads on both channels AND at least one pain-flagged review AND
     independently owned.
  B: ads on one channel AND (pain-flagged review OR independently owned).
  C: everything else that survived the filters.

NULL handling for fb_ads_active / google_ads_active
-----------------------------------------------------
These fields are tri-state: True (confirmed active), False/0 (confirmed
checked, not active), or None/NULL (unknown — the Apify check failed,
timed out, or came back empty; see prospector/apify_client.py). NULL is
*not* the same as False and must not be scored as if the channel were
confirmed inactive in a way that penalizes it, nor scored as active. The
policy here: only a channel confirmed True (via `is True`/`== 1`, not
merely truthy) counts toward `_ads_channel_count`. An unknown channel
contributes 0 to the score — identical to a confirmed-inactive channel,
because the scoring function only ever *rewards* active channels and never
penalizes inactive/unknown ones, so "don't reward, don't penalize" and
"contribute 0" are the same outcome here. The NULL vs 0 distinction still
matters upstream (storage, export, and business-quality signal to Andy —
"unknown, worth re-checking later via `prospector collect`" vs "checked,
genuinely no ads") even though it doesn't change the score itself.
"""
from __future__ import annotations

REVIEW_COUNT_BONUS_CAP = 10
REVIEW_COUNT_BONUS_DIVISOR = 20  # +1 point per ~20 reviews, capped at 10


def _is_active(value: bool | int | None) -> bool:
    """True only for a confirmed-active channel. Handles both native bools
    (pre-DB, in-pipeline) and ints (post-DB round-trip, where sqlite stores
    True as 1) without ever treating None as active or as a penalty."""
    return value is True or value == 1


def _ads_channel_count(fb_active: bool | int | None, google_active: bool | int | None) -> int:
    return int(_is_active(fb_active)) + int(_is_active(google_active))


def review_count_bonus(review_count: int | None) -> int:
    if not review_count:
        return 0
    return min(REVIEW_COUNT_BONUS_CAP, review_count // REVIEW_COUNT_BONUS_DIVISOR)


def score_business(
    *,
    fb_ads_active: bool | int | None,
    google_ads_active: bool | int | None,
    has_pain_flag: bool,
    is_independent: bool,
    review_count: int | None,
) -> tuple[str, int]:
    """Return (priority, priority_score) for a qualified business."""
    channels = _ads_channel_count(fb_ads_active, google_ads_active)

    score = 0
    if channels == 2:
        score += 40
    elif channels == 1:
        score += 20

    if has_pain_flag:
        score += 25
    if is_independent:
        score += 15
    score += review_count_bonus(review_count)

    if channels == 2 and has_pain_flag and is_independent:
        priority = "A"
    elif channels >= 1 and (has_pain_flag or is_independent):
        priority = "B"
    else:
        priority = "C"

    return priority, score
