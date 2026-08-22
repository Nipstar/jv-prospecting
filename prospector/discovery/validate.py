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

import json
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
    # NOTE: the digit can come before "top" too — "8 Top Rated Electricians
    # in Romford for 2026" slipped past the original top-then-digit-only
    # pattern (live-tested miss, East London electrician sweep). Also
    # catch "for {year}" as its own signal — a "best-of" listicle dated
    # to a specific year is never a business's own name.
    r"\b\d+\s+top\b",
    r"\bfor\s+20\d\d\b",
    r"\bbest\s+\d*\s*.*\bin\b",
    r"\bcompanies\s+in\b.*\bfor\b",
    r"\blocal\s+.*\s+companies\s+in\b",
    r"\(\d+\s+found\)",  # "HVAC companies in London (100 found)" — a search-results-page title
    r"\bnear\s+me\b.*\blondon\b",  # "X Near Me London" listicle-style aggregator titles
]
_TITLE_REJECT_RE = re.compile("|".join(_TITLE_REJECT_PATTERNS), re.IGNORECASE)

# "Air conditioning" as a search term is ambiguous between building/HVAC
# aircon (what Andy wants) and vehicle aircon (a different trade entirely)
# — same ambiguity that caused the manually-caught "Alpinair"/"Southwest
# Mobile Autocare" misses in the London sweep. Unlike those two (caught by
# business name alone), the Surrey sweep surfaced misses where the NAME
# reads as a plausible building-aircon business but the DOMAIN gives away
# the real trade: jdkautomotive.co.uk, autotest.co.uk, and
# car-air-conditioning-service-guildford.co.uk (business name "Precision
# Air Conditioning Service", nothing automotive-sounding at all). Domain-
# level check, free/instant, no fetch needed.
_AUTOMOTIVE_DOMAIN_SUBSTRINGS = [
    "automotive", "autotest", "car-air-con", "vehicle-air-con", "autocare",
    "-garage", "garage-", "mot-centre", "motcentre", "tyres", "bodyshop",
    "car-servicing", "carservicing",
]
_AUTOMOTIVE_DOMAIN_RE = re.compile("|".join(_AUTOMOTIVE_DOMAIN_SUBSTRINGS), re.IGNORECASE)

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

_DIRECTORY_URL_PATTERNS = [
    r"/results/",
    r"order-by-relevance",
    r"[?&]page=\d",
    r"/search[/?]",
    r"/search-results",
    r"within-\d+-miles",
    r"/listings?/",
    r"/directory/",
]
_DIRECTORY_URL_RE = re.compile("|".join(_DIRECTORY_URL_PATTERNS), re.IGNORECASE)

# Raw schema-block COUNT is not a reliable directory signal on its own —
# live-tested false positive: aacairconditioning.co.uk (a real business)
# carries 23 separate LocalBusiness JSON-LD blocks (repeated/nested
# structured data for the same company), which a naive "count > 2 ==
# directory" rule wrongly flagged. What actually distinguishes a directory
# page is DISTINCT BUSINESS NAMES across those blocks — a real business's
# repeated schema all names itself once; a directory schema-marks up every
# *different* listed business. industryoversight.co.uk: 24 blocks, 24
# distinct names ("Tri-Counties Heating LTD", "Vitoenergy Ltd", ...) — a
# genuine directory. See _extract_schema_business_names() below.
_MAX_DISTINCT_SCHEMA_NAMES_FOR_SINGLE_BUSINESS = 2

_FETCH_TIMEOUT_SECONDS = 6.0
_MAX_DISTINCT_EXTERNAL_DOMAINS_BEFORE_SUSPECT = 8  # of the NON-infra, NON-cert-body domains counted below

# Domains that show up on totally normal small-business sites and tell us
# nothing about directory/listicle-ness — CDN/font/asset hosts, social
# profile links, map embeds, and (importantly) UK trade-body/certification
# badge links (Gas Safe, NICEIC, TrustMark, CHAS, etc — a real independent
# tradesperson site *should* link to these, it's a trust signal, not a
# directory signal). Found via retroactive validation against 72 existing
# organic-sourced DB rows: without this list the raw distinct-external-
# domain count flagged 8 genuine independent businesses as "directories"
# purely because their footer links to Gas Safe Register + Trustpilot +
# Facebook + Google Fonts — see git log for the specific false positives.
_NON_SIGNAL_DOMAIN_SUBSTRINGS = [
    "fonts.googleapis.com", "fonts.gstatic.com", "pro.fontawesome.com", "use.typekit.net",
    "cdnjs.cloudflare.com", "cloudflare.com", "squarespace.com", "squarespace-cdn.com", "sqspcdn.com",
    "gmpg.org", "cdn.", "static.", "assets.",
    "google.com", "goo.gl", "google.co.uk", "maps.app.goo.gl", "search.google.com",
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "a.me",
    "trustpilot.com", "browsehappy.com",
    # UK trade-body / certification / compliance registers — a trust
    # signal on a real tradesperson's own site, not a directory signal.
    "gassaferegister.co.uk", "niceic.com", "napit.org.uk", "elecsa.co.uk", "trustmark.org.uk",
    "chas.co.uk", "thebesa.com", "safecontractor.com", "checkatrade.com", "ciphe.org.uk",
    "recc.org.uk", "mcscertified.com",
]


# Job board / recruitment domains — a Places/organic/Yell result can be a
# job AD, not a business, e.g. energyjobline.com/job/maintenance-electrician-
# kingston-upon-thames-london-31236809 ("Maintenance Electrician in Kingston
# upon Thames, London") slipped through because its title doesn't match
# _TITLE_REJECT_RE's "jobs in" pattern (it's phrased as a job title, not a
# "jobs in X" listicle) — the DOMAIN is the reliable tell here, same idea as
# _AUTOMOTIVE_DOMAIN_SUBSTRINGS.
_JOB_BOARD_DOMAIN_SUBSTRINGS = [
    "energyjobline.com", "indeed.co.uk", "indeed.com", "totaljobs.com",
    "reed.co.uk", "cv-library.co.uk", "glassdoor.co.uk", "glassdoor.com",
    "monster.co.uk", "jobsite.co.uk", "jobserve.com", "adzuna.co.uk",
    "s1jobs.com", "careerstructure.com", "findajob.dwp.gov.uk",
    "milkround.com", "prospects.ac.uk", "apprenticeships.gov.uk",
    "jooble.org", "ziprecruiter.co.uk", "careerbuilder.co.uk",
    "linkedin.com/jobs",
]
_JOB_BOARD_DOMAIN_RE = re.compile("|".join(re.escape(s) for s in _JOB_BOARD_DOMAIN_SUBSTRINGS), re.IGNORECASE)

# Off-vertical junk that Google Places text search ("electricians in
# Kingston") loosely text-matches in on trade/keyword overlap alone, none of
# which are ever a real target for any of prospector's verticals — a
# tattooist, vape shop, and phone/gadget repair shop all surfaced in one
# Kingston run (id 1067/1069/1072/1071) purely because their listing text
# mentions "electric"/"repair"/wiring-adjacent words. Name-based, no
# network call, safe to run against every discovery source (Places/Yell/
# Checkatrade/organic), not just organic search results.
_OFF_VERTICAL_NAME_SUBSTRINGS = [
    "tattoo", "vape", "vaping", "e-cigarette", "e cigarette", "e-liquid",
    "phone repair", "mobile phone repair", "iphone repair", "ipad repair",
    "laptop repair", "computer repair", "screen repair", "repair zone",
    "cell phone repair", "gadget repair", "phone shop",
]
_OFF_VERTICAL_NAME_RE = re.compile("|".join(re.escape(s) for s in _OFF_VERTICAL_NAME_SUBSTRINGS), re.IGNORECASE)

# Educational institution names ("Kingston College") also slip through
# Places text search the same way — but NOT a universal reject: "London
# College of Oral Implantology (LCOI)" is a real dental-implant training
# clinic that legitimately trades under "College" in the dental vertical.
# Scoped to verticals where a further-education college is never a
# plausible real target (every trade-services vertical); dental/vets are
# excluded since specialist teaching-clinic naming is plausible there.
_EDUCATION_NAME_SUBSTRINGS = ["college", "university", "sixth form", "further education"]
_EDUCATION_NAME_RE = re.compile("|".join(re.escape(s) for s in _EDUCATION_NAME_SUBSTRINGS), re.IGNORECASE)
_VERTICALS_WHERE_COLLEGE_IS_PLAUSIBLE = {"dental_cosmetic_aesthetic", "vets"}
# Belt-and-braces text match on top of the slug check above — live-tested
# false positive: run 2's vertical is stored as the legacy trade_sectors.py
# label "Cosmetic dentistry / implant clinics" (pre-v2 pipeline), which
# slug_for_label() can't resolve (returns None, same "legacy label" gap
# documented in chain_signals.match_known_chain_name), so the slug-only
# check alone wrongly rejected "London College of Oral Implantology
# (LCOI)". Checking the raw label text too catches this regardless of
# which pipeline/label vocabulary wrote it.
_PLAUSIBLE_COLLEGE_LABEL_SUBSTRINGS = ["dental", "dentist", "cosmetic", "implant", "aesthetic", "vet"]


def looks_like_job_board_domain(url: str | None) -> bool:
    """Fast, free, no-network check. True if the URL is a job-board/
    recruitment-site domain (a job ad, not a business) — see
    _JOB_BOARD_DOMAIN_SUBSTRINGS docstring for the live-tested miss this
    catches."""
    if not url:
        return False
    return bool(_JOB_BOARD_DOMAIN_RE.search(url))


def looks_like_off_vertical_shop(name: str | None) -> bool:
    """Fast, free, no-network check. True if the business name reads as a
    tattooist/vape shop/phone-gadget-repair shop — off-vertical junk for
    every prospector vertical, regardless of which trade keyword the
    Places/Yell text search matched on."""
    if not name:
        return False
    return bool(_OFF_VERTICAL_NAME_RE.search(name))


def looks_like_education_institution(
    name: str | None, vertical_slug: str | None = None, vertical_label: str | None = None
) -> bool:
    """Fast, free, no-network check. True if the name reads as a
    college/university (further-education institution, not a trading
    business) — scoped OFF for verticals where a teaching clinic is a
    plausible real business (see _VERTICALS_WHERE_COLLEGE_IS_PLAUSIBLE
    docstring: "London College of Oral Implantology" is a real dental
    business, "Kingston College" surfacing under an electricians search is
    not). Checks both the resolved slug AND the raw label text (legacy
    pipeline labels don't always resolve to a slug — see
    _PLAUSIBLE_COLLEGE_LABEL_SUBSTRINGS docstring)."""
    if not name:
        return False
    if vertical_slug in _VERTICALS_WHERE_COLLEGE_IS_PLAUSIBLE:
        return False
    label_lower = (vertical_label or "").lower()
    if any(sub in label_lower for sub in _PLAUSIBLE_COLLEGE_LABEL_SUBSTRINGS):
        return False
    return bool(_EDUCATION_NAME_RE.search(name))


def validate_discovered_business(
    name: str | None,
    website: str | None,
    vertical_slug: str | None = None,
    vertical_label: str | None = None,
) -> tuple[bool, str]:
    """Cheap, no-network validation applied to EVERY discovery source
    (Places, Yell, Checkatrade, organic) at insert time — see
    discovery/places.py's discover_run() loop. Unlike
    validate_organic_result() below, this deliberately does NOT fetch the
    page (Places/Yell results are frequent enough that a per-result httpx
    GET would be slow/rate-limit-risky across a whole run) — name/domain
    heuristics only. Returns (is_valid, reason); is_valid=False means
    "skip this, don't insert into the DB"."""
    if is_junk_title(name):
        return False, f"title matches junk/listicle pattern: {name!r}"
    if looks_like_job_board_domain(website):
        return False, f"domain is a job board/recruitment site, not a business: {website!r}"
    if looks_like_off_vertical_shop(name):
        return False, f"name reads as an off-vertical shop (tattoo/vape/phone-repair), not a trade business: {name!r}"
    if looks_like_education_institution(name, vertical_slug, vertical_label):
        return False, f"name reads as a college/university, not a trading business: {name!r}"
    return True, "no junk signals found"


def is_junk_title(title: str | None) -> bool:
    """Fast, free, no-network check. True if the title itself reads like
    a listicle/aggregator/job/course page rather than a business name."""
    if not title:
        return False
    return bool(_TITLE_REJECT_RE.search(title))


def looks_like_automotive_domain(url: str | None) -> bool:
    """Fast, free, no-network check. True if the domain/URL gives away a
    vehicle-aircon/garage/tyre business rather than building HVAC — see
    _AUTOMOTIVE_DOMAIN_SUBSTRINGS docstring for the live-tested misses
    this catches (the business NAME alone doesn't always give it away)."""
    if not url:
        return False
    return bool(_AUTOMOTIVE_DOMAIN_RE.search(url))


def looks_like_directory_url(url: str) -> bool:
    """Fast, free, no-network check. True if the URL path itself reads
    like a directory search-results page (dentons.net's
    /results/.../order-by-relevance?page=2 pattern)."""
    return bool(_DIRECTORY_URL_RE.search(url))


_LD_JSON_BLOCK_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _extract_schema_business_names(html: str) -> list[str]:
    """Parse every JSON-LD block on the page (proper json.loads, not
    regex-counting) and collect the `name` of every LocalBusiness/
    Organization entity found. A page can legitimately have several
    blocks — what matters is how many *distinct* business names they
    carry (see _MAX_DISTINCT_SCHEMA_NAMES_FOR_SINGLE_BUSINESS docstring)."""
    names: list[str] = []
    for raw in _LD_JSON_BLOCK_RE.findall(html):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            # @graph is a common wrapper for multiple entities in one block.
            candidates = item.get("@graph", [item]) if isinstance(item.get("@graph"), list) else [item]
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                cand_type = cand.get("@type")
                type_list = cand_type if isinstance(cand_type, list) else [cand_type]
                if any(
                    t in ("LocalBusiness", "Organization", "HVACBusiness",
                          "HomeAndConstructionBusiness", "ProfessionalService")
                    for t in type_list
                ) and cand.get("name"):
                    names.append(str(cand["name"]).strip().lower())
    return names


def looks_like_directory_or_blog(url: str, html: str | None = None) -> tuple[bool, str]:
    """One httpx GET (if html not already supplied), fails open on any
    fetch problem. Returns (is_suspect, reason)."""
    if looks_like_directory_url(url):
        return True, "URL path matches directory/search-results pattern"

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

    schema_names = _extract_schema_business_names(html)
    distinct_schema_names = len(set(schema_names))
    if 1 <= distinct_schema_names <= _MAX_DISTINCT_SCHEMA_NAMES_FOR_SINGLE_BUSINESS:
        return False, "has LocalBusiness/Organization schema — confirmed real business"
    if distinct_schema_names > _MAX_DISTINCT_SCHEMA_NAMES_FOR_SINGLE_BUSINESS:
        return True, (
            f"{distinct_schema_names} distinct business names in page schema "
            "— directory/listing-aggregator signal, not a single business"
        )
    # No JSON-LD names parsed (either no JSON-LD at all, or it's non-name
    # microdata) — fall back to itemtype microdata presence as a weaker
    # positive signal, same as the original check.
    if _LOCAL_BUSINESS_SCHEMA_RE.search(html):
        return False, "has LocalBusiness/Organization microdata — confirmed real business"

    if _BLOG_SIGNAL_RE.search(html):
        return True, "blog/article signals found (posted/published/byline/read-more)"

    own_domain = urlparse(url).netloc.lower().lstrip("www.")
    hrefs = re.findall(r'href=["\']https?://([^/"\'#]+)', html, re.IGNORECASE)
    external_domains = Counter(
        d.lower().lstrip("www.") for d in hrefs
        if d.lower().lstrip("www.") != own_domain
        and not any(sig in d.lower() for sig in _NON_SIGNAL_DOMAIN_SUBSTRINGS)
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
    if looks_like_automotive_domain(url):
        return False, f"domain gives away vehicle-aircon/garage business, not building HVAC: {url!r}"
    if not url:
        return True, "no URL to content-check, letting it through"
    is_suspect, reason = looks_like_directory_or_blog(url)
    if is_suspect:
        return False, reason
    return True, reason
