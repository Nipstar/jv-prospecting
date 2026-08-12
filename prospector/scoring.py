"""Priority scoring — plain weighted function, not ML.

Ad-spend signals (Meta/Google ads via Apify) were removed in the "Prospector
v2: UK High-Ticket Firms, Review-Based Targeting" rebuild — the old
targeting model scored businesses by ad spend; the new one targets weak
review profiles instead (see prospector/pain.py for the review pain-flag
keyword matching this scoring still uses). Review-based scoring
(`review_target_score` etc.) is a later phase of that rebuild — this module
is Phase 1's interim scoring, stripped of ads but not yet carrying the new
review-weight model.

Weights:
  pain_flag    = 25   (at least one pain-flagged review)
  independent  = 15   (not group/corporate-owned)
  review_count bonus, up to 10 (scaled, capped)

Priority bands:
  A: pain-flagged review AND independently owned.
  B: pain-flagged review OR independently owned.
  C: everything else that survived the filters.
"""
from __future__ import annotations

REVIEW_COUNT_BONUS_CAP = 10
REVIEW_COUNT_BONUS_DIVISOR = 20  # +1 point per ~20 reviews, capped at 10


def review_count_bonus(review_count: int | None) -> int:
    if not review_count:
        return 0
    return min(REVIEW_COUNT_BONUS_CAP, review_count // REVIEW_COUNT_BONUS_DIVISOR)


def score_business(
    *,
    has_pain_flag: bool,
    is_independent: bool,
    review_count: int | None,
) -> tuple[str, int]:
    """Return (priority, priority_score) for a qualified business."""
    score = 0
    if has_pain_flag:
        score += 25
    if is_independent:
        score += 15
    score += review_count_bonus(review_count)

    if has_pain_flag and is_independent:
        priority = "A"
    elif has_pain_flag or is_independent:
        priority = "B"
    else:
        priority = "C"

    return priority, score
