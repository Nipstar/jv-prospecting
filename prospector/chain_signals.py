"""Chain / franchise / corporate-group detection config — standing exclusion
rule added after Phase 2 testing surfaced "Mildmay Veterinary Hospital"
(a CVS Group plc sibling practice, incorporated 1985, `established_flag=1`)
sliding through as an apparently-independent target.

Why not rely on Companies House alone
--------------------------------------
The old (pre-Phase-1) ad-spend model had an `is_group_owned` concept
(`companies_house_client.check_ownership()` — still present in the
codebase, PSC/officer based) built for a similar purpose: flag
corporate-entity ownership so outreach targets are genuinely independent
owner-operators. It's real signal and worth reusing (see
`discovery/places.py`'s `enrich_companies_house()`, extended to also
return `is_group_owned`/`director_name` using the *same* matched company,
rather than a second name search).

But it's not sufficient alone. Live-tested against the exact business that
surfaced this gap: `companies_house_client.search_company("Mildmay
Veterinary Hospital")` matches **"MILDMAY HOSPITAL LTD"** — a London
hospice charity with no relation to the vet practice or CVS Group — a
false-positive name match with zero PSC records. Companies House
name-search is best-effort (see README "Notes / limitations"); for a
national chain like CVS Group, subsidiary practices are frequently *not*
separately incorporated under a name that text-matches the trading name at
all, so PSC lookups miss them outright. That same business's `website`
column, however, is unambiguous: `https://www.cvsvets.com/...` — CVS
Group's own booking/practice-finder domain. Chains reliably leak through
domain and brand-name patterns even when Companies House data doesn't
resolve cleanly, so keyword/domain matching is the *primary*, most
reliable signal here — Companies House ownership is a secondary,
supporting signal, not the sole gate.

Multi-location signal
----------------------
If two discovered businesses (any run, any vertical) share the same
`domain` or the same `companies_house_number`, that's strong evidence of a
multi-site chain rather than a coincidence — a genuinely independent local
firm doesn't usually share a domain or a Companies House registration with
another discovered listing. This is computed against data already in the
DB (see `db.count_businesses_sharing_domain` /
`count_businesses_sharing_company_number`, extending the existing
place_id/phone/domain dedupe from Phase 2 rather than duplicating it) —
no extra API calls required.

Editing these lists
--------------------
Purely data — no code changes needed to add a brand. Matching is
case-insensitive substring matching against the business name (or its
domain, for `CHAIN_DOMAIN_PATTERNS`). Keep entries lower-case.
"""
from __future__ import annotations

from prospector.verticals import slug_for_label

# Brand/name substrings that are always treated as a corporate chain
# regardless of vertical (holding groups whose practices/branches often
# keep the trading name but occasionally surface the parent brand in the
# Google Places listing name itself, e.g. "... - part of Rentokil").
GENERIC_CHAIN_BRAND_NAMES: list[str] = [
    "rentokil",
    "anglian home improvements",
    "everest",
    "safestyle",
    "homeserve",
    "dyno-rod",
    "dynorod",
    "drain doctor",
    "metro rod",
    "co-op funeralcare",
    "co-op funeral",
    "dignity funerals",
    "dignity plc",
]

# Vertical-specific known UK chain/franchise/corporate-group brand names.
# Keyed by prospector.verticals.VERTICALS slugs. Judgement call on
# realistic large operators per vertical — extend as Andy spots more in
# real discovery runs.
CHAIN_BRAND_NAMES_BY_VERTICAL: dict[str, list[str]] = {
    "estate_agents_lettings": [
        "purplebricks",
        "foxtons",
        "countrywide",
        "connells",
        "belvoir",
        "william h brown",
        "your move",
        "reeds rains",
        "haart",
        "romans",
        "martin & co",
        "martin and co",
        "hunters",
        "spicerhaart",
        "lsl property services",
        "kinleigh folkard",
        "winkworth",
        "ekb estate",
    ],
    "solicitors_conveyancers": [
        "irwin mitchell",
        "slater and gordon",
        "slater & gordon",
        "dwf law",
        "knights plc",
        "my home move",
        "premier property lawyers",
        "simplify conveyancing",
        "simplify group",
        "enact conveyancing",
        "convey law",
        "national conveyancing",
        "co-op legal services",
    ],
    "accountants": [
        "pwc",
        "pricewaterhousecoopers",
        "deloitte",
        "kpmg",
        "ernst & young",
        "ernst and young",
        "grant thornton",
        "bdo llp",
        "rsm uk",
        "mazars",
        "azets",
        "menzies llp",
        "xeinadin",
        "taxassist accountants",
        "cheylesmore",
        # NB: deliberately no bare "ey" entry for Ernst & Young — too short
        # to substring-match safely (collides with ordinary words like
        # "Finchley", "surgery", "money"). "ernst & young" above covers it.
    ],
    "heating_plumbing_electrical": [
        "british gas",
        "pimlico plumbers",
        "homeserve",
        "dyno-rod",
        "dynorod",
        "drain doctor",
        "metro rod",
        "corgi homeplan",
        "warmzilla",
        "boilerhut",
    ],
    "roofing_damp_driveways": [
        "anglian home improvements",
        "everest",
        "safestyle",
        "peter cox",
        "rentokil property care",
        "permagard",
        "ultraframe",
    ],
    "dental_cosmetic_aesthetic": [
        "mydentist",
        "my dentist",
        "idh group",
        "integrated dental holdings",
        "bupa dental care",
        "portman dental care",
        "rodericks dental",
        "dentex",
        "oasis dental care",
        "dentalcorp",
        "church street dental",
        "sk:n clinic",
        "skn clinic",
        "court house clinics",
        "regen health",
        "the dental partnership",
    ],
    "vets": [
        "cvs group",
        "cvs (uk)",
        "cvs uk",
        "ivc evidensia",
        "linnaeus",
        "medivet",
        "vets4pets",
        "vets 4 pets",
        "pets at home vet",
        "goddard veterinary group",
        "vetpartners",
        "companion care vets",
        "white cross vets",
        "willows veterinary",
        "vets now",
    ],
    "funeral_directors": [
        "dignity plc",
        "dignity funerals",
        "co-op funeralcare",
        "co-op funeral",
        "funeral partners",
        "laurel funerals",
        "westerleigh group",
    ],
    "garage_window_conservatory": [
        "anglian home improvements",
        "everest",
        "safestyle",
        "zenith windows",
        "safeglaze",
    ],
}

# Domain substrings for known chain/franchise/corporate-group web
# properties — matched against businesses.domain (case-insensitive
# substring). These catch the "practice-finder" pattern (CVS Group routes
# every sibling practice's website through its own domain, e.g.
# cvsvets.com/south-east/mildmay-veterinary-hospital-winchester) that a
# name-only match misses, since the *business name* on Google often stays
# the pre-acquisition local trading name.
CHAIN_DOMAIN_PATTERNS: list[str] = [
    "cvsvets.com",
    "cvsgroup.co.uk",
    "ivcevidensia.co.uk",
    "ivcevidensia.com",
    "linnaeusgroup.co.uk",
    "medivet.co.uk",
    "vets4pets.com",
    "petsathome.com",
    "goddardvetgroup.co.uk",
    "vetpartners.co.uk",
    "companioncare.co.uk",
    "whitecrossvets.co.uk",
    "mydentist.co.uk",
    "idh-dental.co.uk",
    "bupa.co.uk",
    "portmandentalcare.com",
    "rodericksdental.co.uk",
    "oasisdentalcare.co.uk",
    "purplebricks.co.uk",
    "foxtons.co.uk",
    "connells.co.uk",
    "yourmove.co.uk",
    "reedsrains.co.uk",
    "haart.co.uk",
    "hunters.com",
    "irwinmitchell.com",
    "myhomemove.com",
    "premierpropertylawyers.com",
    "co-opfuneralcare.co.uk",
    "dignityfunerals.co.uk",
    "anglianhome.co.uk",
    "everest.co.uk",
    "safestyle-windows.co.uk",
    "rentokil.co.uk",
]


def _lower(value: str | None) -> str:
    return (value or "").lower()


def match_known_chain_name(name: str | None, vertical_slug: str | None = None) -> str | None:
    """Return the matched brand substring if `name` looks like a known
    chain/franchise brand for this vertical, else None.

    Only checks GENERIC_CHAIN_BRAND_NAMES (cross-vertical corporate
    groups, deliberately kept to long/unambiguous brand strings so they're
    safe to apply regardless of vertical) plus this vertical's own list.
    Deliberately does NOT fall back to checking *every* vertical's brand
    list when vertical_slug is None/unrecognised (e.g. a legacy
    trade_sectors.py vertical label from the pre-v2 `prospector run`
    pipeline, which uses different label text than verticals.py) — some
    vertical-specific brand strings are short/generic enough (e.g. "hunters"
    for the estate-agents chain Hunters) that matching them against an
    unrelated vertical produces false positives (caught in testing: "Hunters
    Brook" kitchen fitters wrongly matched the estate-agents-only "hunters"
    entry before this fix).
    """
    lowered = _lower(name)
    if not lowered:
        return None
    candidates = list(GENERIC_CHAIN_BRAND_NAMES)
    if vertical_slug:
        candidates += CHAIN_BRAND_NAMES_BY_VERTICAL.get(vertical_slug, [])
    for brand in candidates:
        if brand.strip() and brand.strip() in lowered:
            return brand.strip()
    return None


def match_known_chain_domain(domain: str | None) -> str | None:
    """Return the matched domain pattern if `domain` looks like a known
    chain/franchise/corporate-group web property, else None."""
    lowered = _lower(domain)
    if not lowered:
        return None
    for pattern in CHAIN_DOMAIN_PATTERNS:
        if pattern in lowered:
            return pattern
    return None


def detect_chain(conn, biz: dict, exclude_id: int | None = None) -> tuple[bool, str | None]:
    """Combine every chain signal into one is_chain / chain_reason
    decision, for a business dict carrying at least `name`, `domain`,
    `vertical` (the VERTICALS *label*, not slug — that's what
    businesses.vertical stores), `is_group_owned`, and
    `companies_house_number`.

    Signals, most to least reliable per the module docstring:
    1. known chain/franchise brand name or domain match (config-driven,
       primary signal — catches cases like Mildmay Veterinary Hospital
       where Companies House name-search misses entirely).
    2. Companies House PSC shows corporate-entity ownership
       (`is_group_owned`, reused from the pre-Phase-1 ad-spend model's
       ownership-filter logic) — secondary/supporting signal only, since
       CH name-matching can both false-negative (as above) and, in
       principle, false-positive against an unrelated same-named company.
    3. multi-location: the same domain or Companies House company number
       already appears on another discovered business (any run, any
       vertical) — extends the existing place_id/phone/domain dedupe
       rather than duplicating it (db.count_businesses_sharing_*).

    Returns (is_chain, chain_reason) — chain_reason is a semicolon-joined,
    human-readable audit trail (None if is_chain is False) so excluded
    businesses can be inspected via `--include-chains`, not silently lost.
    """
    from prospector import db  # local import: db.py doesn't import this module, but keeps chain_signals importable standalone (e.g. from tests) without a DB connection

    reasons: list[str] = []

    name = biz.get("name")
    vertical_label = biz.get("vertical")
    vertical_slug = slug_for_label(vertical_label) if vertical_label else None

    name_match = match_known_chain_name(name, vertical_slug)
    if name_match:
        reasons.append(f"business name matches known chain/franchise brand '{name_match}'")

    domain_match = match_known_chain_domain(biz.get("domain"))
    if domain_match:
        reasons.append(f"domain matches known chain/franchise web property '{domain_match}'")

    if biz.get("is_group_owned"):
        company_number = biz.get("companies_house_number") or "unknown"
        reasons.append(f"Companies House PSC shows corporate-entity ownership (company {company_number})")

    domain_count = db.count_businesses_sharing_domain(conn, biz.get("domain"), exclude_id=exclude_id)
    if domain_count:
        reasons.append(f"same domain seen at {domain_count + 1} discovered locations")

    ch_count = db.count_businesses_sharing_company_number(conn, biz.get("companies_house_number"), exclude_id=exclude_id)
    if ch_count:
        reasons.append(f"same Companies House company number seen at {ch_count + 1} discovered locations")

    return (bool(reasons), "; ".join(reasons) if reasons else None)


def rescan_existing(conn) -> int:
    """Recompute is_chain/chain_reason for every business already in the
    DB, using only data already stored (name, domain, is_group_owned,
    companies_house_number — no new Companies House/Places API calls). For
    businesses discovered before this feature existed (all 295 pre-Phase-6
    rows — the 8 real runs plus run #9's 4 test vets), `is_group_owned` is
    whatever it already was (NULL/0 for anything written before this
    feature, since the v2 discovery module didn't call the ownership check
    until this change) — the keyword/domain and multi-location signals
    still apply retroactively, which is exactly how "Mildmay Veterinary
    Hospital" (run #9, cvsvets.com domain) gets caught by this backfill.

    `prospector chain rescan` (cli.py) is the CLI entrypoint. Returns the
    count of businesses now flagged is_chain=1.
    """
    rows = conn.execute("SELECT * FROM businesses").fetchall()
    flagged = 0
    for row in rows:
        biz = dict(row)
        is_chain, reason = detect_chain(conn, biz, exclude_id=row["id"])
        conn.execute(
            "UPDATE businesses SET is_chain = ?, chain_reason = ? WHERE id = ?",
            (int(is_chain), reason, row["id"]),
        )
        if is_chain:
            flagged += 1
    return flagged
