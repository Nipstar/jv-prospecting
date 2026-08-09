"""Pain keyword matching for review text.

Case-insensitive substring match against a fixed keyword list. Used to flag
reviews.pain_flag — a strong outreach signal ("this business drops calls /
ignores customers / is slow to respond", which is exactly what Antek's
automation fixes).
"""
from __future__ import annotations

PAIN_KEYWORDS: list[str] = [
    "no answer",
    "didn't answer",
    "never called back",
    "no call back",
    "no callback",
    "waited weeks",
    "couldn't get through",
    "no response",
    "ignored my",
    "missed my appointment",
    "no reply",
    "chased them",
    "went straight to voicemail",
]


def has_pain_signal(text: str | None) -> bool:
    """Return True if any pain keyword appears as a substring of text."""
    if not text:
        return False
    lowered = text.lower()
    return any(kw in lowered for kw in PAIN_KEYWORDS)
