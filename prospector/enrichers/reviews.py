"""Review profile module — Prospector v2 Phase 3.

For each prospect with a google_place_id, fetches rating, review count, and
review snippets via places_client.get_place_details() (Place Details
endpoint, extended in this phase — see places_client.py), then computes
review_target_score (0-100).

Coverage caveat (Andy's note, not a change request — documented here per
his instruction): "Places API returns 5 reviews per fetch, so
missed_call_evidence will only catch complaints that happen to surface in
that set. When it does hit, it's gold for the opener ('saw a review
mentioning calls going unanswered'), so it's worth the flag despite patchy
coverage." Built as specified.

Scoring bands are centralised in prospector/scoring_config.py — see that
module for the documented interpretation of the 50-100 review-count gap in
Andy's spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from prospector import places_client
from prospector.db import businesses_needing_review_fetch, insert_review, update_business_fields
from prospector.http import ApiError
from prospector.pain import has_pain_signal
from prospector.scoring_config import (
    HAS_NEGATIVE_RECENT_POINTS,
    MISSED_CALL_EVIDENCE_POINTS,
    MISSED_CALL_KEYWORDS,
    NEGATIVE_RECENT_STAR_THRESHOLD,
    RATING_BAND_LOW_POINTS,
    RATING_BAND_MID_HIGH,
    RATING_BAND_MID_LOW,
    RATING_BAND_MID_POINTS,
    REVIEW_COUNT_BANDS,
    REVIEW_TARGET_SCORE_CAP,
    WEAK_GBP_POINTS,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_count_points(review_count: int | None) -> int:
    count = review_count or 0
    for lo, hi, points in REVIEW_COUNT_BANDS:
        if hi is None:
            if count >= lo:
                return points
        elif lo <= count <= hi:
            return points
    return 0


def _rating_points(avg_rating: float | None) -> int:
    if avg_rating is None:
        return 0
    if RATING_BAND_MID_LOW <= avg_rating <= RATING_BAND_MID_HIGH:
        return RATING_BAND_MID_POINTS
    if avg_rating < RATING_BAND_MID_LOW:
        return RATING_BAND_LOW_POINTS
    return 0


def matches_missed_call_keyword(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(kw in lowered for kw in MISSED_CALL_KEYWORDS)


@dataclass
class ReviewScoreResult:
    business_id: int
    name: str
    found_listing: bool
    avg_rating: float | None
    review_count: int | None
    worst_recent_rating: int | None
    has_negative_recent: bool
    weak_gbp: bool
    missed_call_evidence: bool
    review_target_score: int


def score_business_reviews(business: dict, details: dict | None) -> ReviewScoreResult:
    """Pure scoring function — given a business row (dict-like) and a
    places_client.get_place_details() result (or None if the listing fetch
    failed / no listing found), compute the review_target_score fields.
    Split out from fetch_and_score() below so it's independently testable.
    """
    if details is None:
        # No Google listing found — weak_gbp per spec, no other signal
        # available to score.
        return ReviewScoreResult(
            business_id=business["id"],
            name=business["name"],
            found_listing=False,
            avg_rating=None,
            review_count=None,
            worst_recent_rating=None,
            has_negative_recent=False,
            weak_gbp=True,
            missed_call_evidence=False,
            review_target_score=min(WEAK_GBP_POINTS, REVIEW_TARGET_SCORE_CAP),
        )

    avg_rating = details.get("rating")
    review_count = details.get("user_ratings_total")
    reviews = details.get("reviews") or []

    ratings = [r["rating"] for r in reviews if r.get("rating") is not None]
    worst_recent_rating = min(ratings) if ratings else None
    has_negative_recent = any(r <= NEGATIVE_RECENT_STAR_THRESHOLD for r in ratings)

    negative_texts = [r.get("text") for r in reviews if (r.get("rating") or 99) <= NEGATIVE_RECENT_STAR_THRESHOLD]
    missed_call_evidence = any(matches_missed_call_keyword(t) for t in negative_texts)

    # "unclaimed-looking profile (no website, no hours)" — weak_gbp also
    # applies here, distinct from the no-listing-found case above.
    weak_gbp = not details.get("website") and not details.get("has_hours")

    score = 0
    score += _review_count_points(review_count)
    score += _rating_points(avg_rating)
    if has_negative_recent:
        score += HAS_NEGATIVE_RECENT_POINTS
    if weak_gbp:
        score += WEAK_GBP_POINTS
    if missed_call_evidence:
        score += MISSED_CALL_EVIDENCE_POINTS
    score = min(score, REVIEW_TARGET_SCORE_CAP)

    return ReviewScoreResult(
        business_id=business["id"],
        name=business["name"],
        found_listing=True,
        avg_rating=avg_rating,
        review_count=review_count,
        worst_recent_rating=worst_recent_rating,
        has_negative_recent=has_negative_recent,
        weak_gbp=weak_gbp,
        missed_call_evidence=missed_call_evidence,
        review_target_score=score,
    )


def fetch_and_score(conn, run_id: int | None = None, business_id: int | None = None, refresh: bool = False) -> list[ReviewScoreResult]:
    """Fetch review profiles + score for businesses with a google_place_id
    that haven't been fetched yet (or all matching, if refresh=True),
    writing results back to the businesses/reviews tables."""
    rows = businesses_needing_review_fetch(conn, run_id=run_id, business_id=business_id, refresh=refresh)
    results: list[ReviewScoreResult] = []

    for row in rows:
        biz = dict(row)
        if biz.get("google_place_id"):
            try:
                details = places_client.get_place_details(biz["google_place_id"])
            except ApiError:
                details = None
        else:
            # Yell/organic-sourced business — no Google Places ID to look
            # up. Treated the same as "no listing found" rather than
            # skipped, so it still gets a review_target_score (weak_gbp)
            # and can surface in `targets list`/`export` — see
            # db.businesses_needing_review_fetch's docstring.
            details = None

        result = score_business_reviews(biz, details)
        results.append(result)

        update_business_fields(conn, biz["id"], {
            "rating": result.avg_rating,
            "review_count": result.review_count,
            "worst_recent_rating": result.worst_recent_rating,
            "has_negative_recent": int(result.has_negative_recent),
            "weak_gbp": int(result.weak_gbp),
            "missed_call_evidence": int(result.missed_call_evidence),
            "review_target_score": result.review_target_score,
            "reviews_fetched_at": _utcnow(),
        })

        if details:
            for r in details.get("reviews") or []:
                # pain_flag keeps its existing, pipeline-wide meaning (a
                # match against pain.PAIN_KEYWORDS, the original "no
                # answer / never called back" list) rather than being
                # redefined here — review_keyword_match is the new,
                # separate missed-call-evidence keyword match
                # (scoring_config.MISSED_CALL_KEYWORDS) this phase adds.
                insert_review(conn, biz["id"], {
                    "rating": r.get("rating"),
                    "text": r.get("text"),
                    "review_date": r.get("publish_time"),
                    "pain_flag": has_pain_signal(r.get("text")),
                    "review_keyword_match": matches_missed_call_keyword(r.get("text")),
                })

    return results
