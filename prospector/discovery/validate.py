"""Content-based validation for organic-search discovery results.

`discovery/organic.py`'s EXCLUDED_DOMAINS list only catches known
aggregator/directory *domains* (yell.com, checkatrade.com, etc.) — it
can't catch a job board on a domain we've never seen before
(simplyhired.co.uk), a college's course-listing page on an .ac.uk domain,
or a "Top 10 Air Conditioning Companies in X" blog/listicle post on an
otherwise-legitimate-looking domain. Live testing on the London
prospecting sweep found exactly these: two organic results ("air
conditioning engineer jobs in north london, greater london" and
"Refrigeration & Air Conditioning Courses in London") were job/course
listing pages, not businesses, and had to be manually caught and removed
after the fact.

This module adds two cheap, fast checks *before* an organic result is
inserted into the DB at all:

1. `is_junk_title()` — instant, free, no network call. Regex-matches the
   SERP result title against known listicle/aggregator/job/course
   phrasing ("jobs in", "courses in", "top N", "best X in Y", "companies
   in X for", etc). Catches most junk without ever fetching the page.

2. `looks_like_directory_or_blog()` — one httpx GET (no Playwright
   escalation — this is a filtering heuristic, not core enrichment, so it
   fails OPEN: if the fetch is blocked/errors/times out, treat the result
   as valid rather than losing a real business to a network hiccup).
   Checks for:
   - schema.org LocalBusiness/Organization JSON-LD or microdata — a
     strong positive signal this IS a real business page.
   - External link density — a directory/listicle page links out to many
     *different* domains (other businesses, other articles); a real
     business's own site mostly links to itself. High distinct-external-
     domain count is a directory/blog signal.
   - Blog/article signals — "posted on", "published", byline patterns
     ("by [Name]"), an <article> tag with a datetime, "read more".

Both checks are heuristics, not perfect classifiers — deliberately tuned
to fail open (let a result through) rather than fail closed (silently
drop a real business), since a false negative here just means Andy sees
one extra junk row to manually exclude (as before), while a false
positive would silently lose real prospecting data with no visibility.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

import httpx

_TITLE_REJECT_PATTERNS = [
    r"\bjobs?\s+in\b",
    r"\bjob\s+listings?\b",
    r"\bcourses?\s+in\b",
    r"\btraining\s+courses?\b",
    r"\btop\s+\d+\b",
    r"\bbest\s+\d*\s*.*\bin\b",
    r"\bcompanies\s+in\b.*\bfor\b",
    r"\blocal\s+.*\s+companies\s+in\b",
    r"\(\d+\s+found\)",  # "HVAC companies in London (100 found)" — a search-results-page title
    r"\bnear\s+me\b.*\blondon\b",  # "X Near Me London" listicle-style aggregator titles
]
_TITLE_REJECT_RE = re.compile("|".join(_TITLE_REJECT_PATTERNS), re.IGNORECASE)

_BLOG_SIGNAL_PATTERNS = [
    r"\bposted\s+(on|by)\b",
    r"\bpublished\s+(on|by)\b",
    r"\bby\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b.*\b(min(ute)?\s+read|comments?)\b",
    r"<article[^>]*\bdatetime\b",
    # NOTE: a bare "read more" was tried and dropped — live-tested false
    # positive on iClimate Solutions (a real business), whose Elementor
    # template uses "Read More" as a generic homepage CTA button, not a
    # blog affordance. Only count it as a blog signal when paired with
    # an actual comment-count/read-time marker nearby (rare on landing
    # pages, common on article templates).
    r"\bread\s+more\b.{0,80}\b(comments?|min(ute)?\s+read)\b",
]
_BLOG_SIGNAL_RE = re.compile("|".join(_BLOG_SIGNAL_PATTERNS), re.IGNORECASE)

_LOCAL_BUSINESS_SCHEMA_RE = re.compile(
    r'"@type"\s*:\s*"(LocalBusiness|Organization|HVACBusiness|HomeAndConstructionBusiness|ProfessionalService)"'
    r"|itemtype=[\"'][^\"']*schema\.org/(LocalBusiness|Organization)",
    re.IGNORECASE,
)

_FETCH_TIMEOUT_SECONDS = 6.0
_MAX_DISTINCT_EXTERNAL_DOMAINS_BEFORE_SUSPECT = 8  # a real business site rarely links to 8+ different other domains from its landing page


def is_junk_title(title: str | None) -> bool:
    """Fast, free, no-network check. True if the title itself reads like
    a listicle/aggregator/job/course page rather than a business name."""
    if not title:
        return False
    return bool(_TITLE_REJECT_RE.search(title))


def looks_like_directory_or_blog(url: str, html: str | None = None) -> tuple[bool, str]:
    """One httpx GET (if html not already supplied), fails open on any
    fetch problem. Returns (is_suspect, reason)."""
    if html is None:
        try:
            with httpx.Client(
                timeout=_FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            ) as client:
                resp = client.get(url)
                if resp.status_code >= 400:
                    return False, "fetch failed — fail open, not flagged"
                html = resp.text
        except Exception:
            return False, "fetch error — fail open, not flagged"

    if _LOCAL_BUSINESS_SCHEMA_RE.search(html):
        return False, "has LocalBusiness/Organization schema — confirmed real business"

    if _BLOG_SIGNAL_RE.search(html):
        return True, "blog/article signals found (posted/published/byline/read-more)"

    own_domain = urlparse(url).netloc.lower().lstrip("www.")
    hrefs = re.findall(r'href=["\']https?://([^/"\'#]+)', html, re.IGNORECASE)
    external_domains = Counter(
        d.lower().lstrip("www.") for d in hrefs if d.lower().lstrip("www.") != own_domain
    )
    distinct_external = len(external_domains)
    if distinct_external >= _MAX_DISTINCT_EXTERNAL_DOMAINS_BEFORE_SUSPECT:
        return True, f"high external-domain link count ({distinct_external}) — directory/listicle signal"

    return False, "no directory/blog signals found"


def validate_organic_result(name: str | None, url: str | None) -> tuple[bool, str]:
    """Combined check for one organic search result. Returns (is_valid,
    reason). is_valid=False means "skip this, don't insert into the DB"."""
    if is_junk_title(name):
        return False, f"title matches junk pattern: {name!r}"
    if not url:
        return True, "no URL to content-check, letting it through"
    is_suspect, reason = looks_like_directory_or_blog(url)
    if is_suspect:
        return False, reason
    return True, reason
