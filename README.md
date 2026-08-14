# prospector

Local business prospecting pipeline for **Antek Automation**. Runs an
interactive wizard, pulls data from Google Places (primary business
discovery) with SerpAPI as an automatic fallback (also used for reviews),
Companies House (ownership/PSC check), scores each business, and stores
everything in SQLite with CSV export.

**Prospector v2 note:** the original ad-spend targeting model (score
businesses by whether they run Meta/Google ads, via two Apify actors) was
removed in Phase 1 of the "Prospector v2: UK High-Ticket Firms,
Review-Based Targeting" rebuild. Phases 2-5 then built the replacement:
**discovery** (`prospector discover`, vertical x location Google Places
search + Companies House enrichment), **review profile** (`prospector
reviews`, Google Places review-snippet scoring), **website signal**
(`prospector site`, booking/chat-widget + phone-dependency heuristics),
and **combined targeting/export** (`prospector targets`). All four are new
command groups that sit alongside the original `prospector run` /
`export` / `report` commands (kept as-is — the 8 existing runs used that
pipeline and it still works). See "Prospector v2: discovery, reviews,
site signals, targets" below for the new workflow.

**Phase 6 note:** a standing exclusion rule was added after Phase 2
testing surfaced a gap — "Mildmay Veterinary Hospital" (a CVS Group plc
sibling practice) was discovered and scored as if independent, because
Companies House name-search false-positive-matched it to an unrelated
company with no PSC records. `prospector` now flags corporate/franchise/
chain businesses (`is_chain`) via a combination of known-brand
keyword/domain matching, Companies House ownership data, and
multi-location detection, and excludes them from `prospector targets
list`/`export` by default (never deletes data — `--include-chains`
overrides). See "Chain / franchise / corporate exclusion" below.

This is a standalone tool that lives alongside Andy's existing
`geo-prospecting` project and **reuses its API keys** (Google Places,
SerpAPI, Companies House) rather than requiring new ones — see
"Credentials" below. It does not modify or depend on `geo-prospecting`'s
code, though `places_client.py` follows the same Google Places API (New)
conventions already established there (see
`geo-prospecting/src/ingest/places.py`).

## What it does

1. **Wizard** (`prospector run`) asks for area, radius, trade sector(s),
   minimum review count/rating, max businesses per sector, an ownership
   filter, and whether to dry-run.
2. **Discover** — Google Places API (New) Text Search per sector/area is
   tried first (`places_client.py`); if it raises (missing/invalid key, HTTP
   error, etc.) or returns 0 results, prospector logs a warning and falls
   back to SerpAPI's Google Maps search (`serpapi_client.py`) automatically.
   Both sources return the identical business dict shape, so this is
   transparent to every step downstream — filtering, scoring, and storage
   don't know or care which source found a given business. See "Discovery
   source & fallback" below for the full rationale.
3. **Filter** — drop businesses below the rating/review thresholds or with
   no website.
4. **Reviews** — pull the latest ~20 Google reviews per surviving business
   and flag any that match a "pain" keyword list (missed calls, no
   response, etc.) — a strong signal that automation would help them.
   Reviews always come from SerpAPI regardless of which source discovered
   the business — see "Discovery source & fallback" below for why.
5. **Ownership** — Companies House PSC/officer lookup, to filter out
   group/corporate-owned businesses (optional, on by default).
6. **Score** — a plain weighted function (not ML) assigns Priority
   A/B/C and a numeric score. See `prospector/scoring.py`.
7. **Store** — everything lands in `prospector.db` (SQLite).
8. **Export** (`prospector export --run-id N --format csv`) writes a sorted
   CSV to `./exports/`.

A **dry run** stops after step 3 (discovery/filtering only) — no Companies
House calls, no DB writes — so you can see roughly how many businesses
would qualify before committing to a real run.

## Project layout

This matches Andy's specified module layout exactly (no `clients/`
subpackage — every API client is a flat top-level module); a couple of
small internal-only support modules (`http.py`, `config.py`, `pain.py`,
`export.py`) exist alongside it for things Andy's list didn't call out by
name but the tool still needs:

```
prospector/
  pyproject.toml          # deps + `prospector` console script
  requirements.txt        # same deps, plain pip form
  .env -> ../geo-prospecting/.env   # symlink, shares Andy's existing keys
  .env.example             # documents the keys this tool needs
  prospector/
    __init__.py
    wizard.py                    # the interactive "series of asks"
    places_client.py              # Google Places API (New) discovery — PRIMARY
    serpapi_client.py             # Google Maps discovery (FALLBACK) + reviews (always)
    companies_house_client.py      # PSC/officer ownership lookup
    scoring.py                       # priority scoring function
    db.py                             # SQLite schema + queries
    pipeline.py                        # orchestrates: discover -> filter ->
                                         # reviews -> ownership -> score -> store
    trade_sectors.py                    # TRADE_SECTORS constant
    cli.py                                # `prospector run` / `export` / `list-runs` / `report`
    # --- internal support modules, not part of Andy's named list ---
    http.py                               # shared retry/backoff wrapper
    config.py                              # env vars, paths
    pain.py                                 # pain keyword list + matcher
    export.py                                # CSV export
    report.py                                 # branded PDF report (ported from geo-slab)
    # --- Prospector v2 (Phases 2-5): discovery, review/site scoring, targeting ---
    verticals.py                                # VERTICALS — Andy's 9 v2 target verticals
    locations.py                                 # starter UK town/city list
    scoring_config.py                             # centralised thresholds/keywords for
                                                    # review_target_score + opportunity_score
    targets.py                                     # combined sort/list/export (Phase 5)
    discovery/
      __init__.py
      places.py                                     # vertical x location discovery (Phase 2)
    enrichers/
      __init__.py
      reviews.py                                     # review profile + review_target_score (Phase 3)
      site.py                                         # booking/chat/phone signals + opportunity_score (Phase 4;
                                                        # 3-layer fetch escalation httpx->Playwright->Apify)
exports/                    # CSV exports land here
reports/                     # HTML + PDF reports land here
prospector.db                # created on first run
```

**Why a `discovery/`/`enrichers/` subpackage split instead of flat
modules:** Andy's Phase 2-4 spec named the files `discovery/places.py`,
`enrichers/reviews.py`, `enrichers/site.py` explicitly (vs. the flat
`prospector/` layout Phase 1 and earlier used) — these are genuinely new
subsystems layered on top of the existing flat client modules
(`places_client.py`, `companies_house_client.py`, kept as-is and
reused/extended rather than duplicated), so the subpackage split doesn't
fight the existing layout, it sits on top of it.

**Note:** `apify_client.py` (Meta + Google ad spend actors) was removed in
Phase 1 of the Prospector v2 rebuild, along with the `collect` CLI command
and the ad-spend scoring/columns. See git history for the removal commit.

## Setup

Requires Python 3.11+. This machine doesn't have a working system `pip`, so
use [`uv`](https://github.com/astral-sh/uv) (already installed at
`/data/.local/bin/uv`) — plain `venv`/`pip` works fine on a normal machine
too.

```bash
cd /data/workspaces/worker/prospector

# with uv (recommended on this box)
export PATH="/data/.local/bin:$PATH"
uv venv .venv
uv pip install -p .venv/bin/python -r requirements.txt
source .venv/bin/activate
python -m prospector.cli --help

# or, on a machine with a normal pip:
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
prospector --help
```

## Credentials

`prospector/.env` is a **symlink** to
`/data/workspaces/worker/geo-prospecting/.env`, so it automatically picks up
the same `GOOGLE_PLACES_API_KEY`, `SERPAPI_KEY`, and
`COMPANIES_HOUSE_API_KEY` that geo-prospecting already uses — nothing to
configure out of the box. That shared `.env` may still contain
`APIFY_TOKEN` (used by geo-prospecting), but prospector no longer reads or
requires it since the ad-spend module was removed in Phase 1 of the
Prospector v2 rebuild.

`GOOGLE_PLACES_API_KEY` is required for the primary (Places) discovery
path — Andy already pays for it, so it's the default. It's not *strictly*
required for the tool to run at all, since discovery automatically falls
back to SerpAPI if it's missing (see "Discovery source & fallback" below),
but without it every run pays for SerpAPI instead of the already-paid-for
Places API. `SERPAPI_KEY` remains required regardless, since it's both the
fallback discovery source and the only reviews source.

If you ever want prospector to use its own separate keys, delete the
symlink and create a real `prospector/.env` file (see `.env.example` for
the variables it reads). Keys are always read from `.env` at runtime; none
are hardcoded anywhere in the codebase.

## Discovery source & fallback

Google Places API (New) (`places_client.py`, Text Search endpoint,
`places.googleapis.com/v1/places:searchText`) is the **default** discovery
source — Andy already pays for `GOOGLE_PLACES_API_KEY`, so this is now
wired in as primary rather than 100% SerpAPI. It follows the same
conventions as the existing Google Places integration in the sibling
`geo-prospecting` project (`src/ingest/places.py`): the newer v1 API (not
the legacy `maps.googleapis.com/maps/api/place` one), `X-Goog-Api-Key` +
`X-Goog-FieldMask` headers, and `nextPageToken` pagination.

**Fallback:** if Google Places raises an error (missing/invalid key, HTTP
failure, etc.) or returns 0 results for a sector/area, `pipeline.py` logs a
warning and automatically retries the same query against SerpAPI's
`google_maps` engine (the tool's original 100%-SerpAPI behaviour). Both
clients' `discover_businesses()` return the identical dict shape (`name`,
`address`, `phone`, `website`, `domain`, `rating`, `review_count`,
`google_place_id`), so nothing downstream — filtering, ad checks, scoring,
storage — needs to change based on which source actually ran.

**Reviews stay on SerpAPI, always** — regardless of which source
discovered a business. This is a deliberate choice, not an oversight:
Google's own Places Details endpoint caps reviews at 5 per place, while
SerpAPI's `google_maps_reviews` engine returns up to ~20. Since
`pipeline.py`'s pain-flag detection (`pain.has_pain_signal`) scans review
text for signals like missed calls/no response, and that detection is only
as good as the review sample it sees, thinning the sample from ~20 to ~5
would materially weaken pain-flag accuracy — a worse trade than the cost of
keeping SerpAPI in the loop just for this step. `google_place_id` is a
standard Google place identifier valid across both APIs, so this works
regardless of which API discovered the business. See
`places_client.fetch_reviews`'s docstring for the same reasoning in code.

**Country restriction (`regionCode: GB`):** the Places Text Search request
body now sends `"regionCode": "GB"`. Without it, ambiguous/colloquial area
names leaked wrong-country results into two separate live runs — a
Canadian "Roy Inch & Sons" (`enercare.ca`) and a Kentucky, USA "Smith
Heating & Cooling" both matched a "South London" query. Confirmed fixed by
re-running the exact same query (`air conditioning companies` in `South
London`) after the fix: 59 results, all genuinely UK addresses/phone
numbers, zero non-UK leakage (the previous US/Canada matches simply don't
come back any more). See `places_client.py`'s `discover_businesses()` for
the field-level comment.

## Usage

```bash
# Interactive wizard -> pipeline run
prospector run
# (or: python -m prospector.cli run, if not installed as a script)

# List past runs
prospector list-runs

# Export a run to CSV (sorted priority A->C, then score descending)
prospector export --run-id 1 --format csv
# writes ./exports/run_1_<timestamp>.csv

# Generate a branded PDF report (see "PDF reports" below)
prospector report --run-id 1
prospector report --business-id 7
```

### Wizard flow

1. Area (free text — town/city or postcode district)
2. Radius (5 / 10 / 20 miles / county-wide)
3. Trade sectors (multi-select checklist, grouped by category, + custom
   free-text sectors)
4. Minimum review count (default 10)
5. Minimum rating (default 4.0)
6. Max businesses per sector (default 25 — controls SerpAPI spend)
7. Ownership filter — exclude group/corporate-owned via Companies House
   (default Y)
8. Dry run? (default Y — discovery only, no Companies House calls)

After the wizard it prints a cost estimate (SerpAPI + Companies House are
free-tier/free) and asks for a final confirmation before spending anything.

## Trade sectors

The full seed list (legal/medical, property, home improvement, trade
services, other independents — 28 sectors) lives in
`prospector/trade_sectors.py` as `TRADE_SECTORS`, a dict of
`{name, category, ticket_size_estimate, google_search_term}`. Add new
sectors there; the wizard picks them up automatically.

## Scoring

`prospector/scoring.py` — plain weighted function:

- pain-flagged review present: +25
- independently owned (not group/corporate): +15
- review count bonus: +1 per ~20 reviews, capped at +10

Priority bands:
- **A** — a pain-flagged review AND independent
- **B** — a pain-flagged review OR independent
- **C** — everything else that survived the filters

**Note (Prospector v2 Phase 1):** the old ad-spend scoring factors (ads on
both/one channel via Meta + Google Apify checks, worth +40/+20) were
removed along with the ad-spend module — see git history. This is Phase
1's interim scoring, stripped of ads but not yet carrying the new
review-weight model that later phases of the Prospector v2 rebuild will
add (targeting weak review profiles rather than ad spend).

## PDF reports

`prospector report` generates a branded, client/rep-ready PDF from a run's
data (or a single business). It reuses the rendering pipeline built for
[geo-slab](https://github.com/Nipstar) — the neo-brutalist, Outfit / DM
Sans / JetBrains Mono, coral-on-cream-and-charcoal brand system Andy already
uses for GEO scan reports and his podcast cover branding — rather than
inventing a new rendering approach. See `prospector/report.py` for the
full port; the mechanism (self-contained HTML/CSS template, printed to PDF
via Playwright headless Chromium) is copied close to verbatim from
`geo-slab/scripts/generate_prospect_report.py`, with a new prospector-shaped
layout since geo-slab's template is built around 0-100 GEO audit scores that
don't apply here.

```bash
# One PDF per run: target-count stats, then a card per targetable business
# (name/vertical/location, review_target_score + opportunity_score, which
# signals fired for each score, director/company details, top pain-flagged
# review quote, contact info) — sorted review_target_score desc,
# opportunity_score desc tiebreak, same order as `targets export`.
# Excludes is_chain=1 by default (same as `targets list`/`export`).
prospector report --run-id 11
# optional: --limit N (default 25) caps how many businesses are shown
# optional: --include-chains includes is_chain=1 businesses (flagged CHAIN/FRANCHISE)

# One-page sales-readiness snapshot for a single business (works for any
# business id, including chain-flagged ones — shown with a chain-reason
# note in the contact block rather than being excluded, since you asked
# for it by id).
prospector report --business-id 7
```

Both write an `.html` and a `.pdf` to `./reports/` (created automatically),
named `PROSPECTOR-RUN-<id>-<area>.pdf` / `PROSPECTOR-BIZ-<id>-<name>.pdf`.
The HTML is kept alongside the PDF for a quick eyeball in a browser without
regenerating.

**v2 note:** this report was originally built around the pre-v2 `priority`
(A/B/C) + `priority_score` model from the legacy `prospector run` wizard.
Businesses discovered via `prospector discover run` (Phase 2 onwards) never
populate those columns, so against v2 data the old report silently rendered
every card as "PRIORITY C / SCORE 0" with a 0/0/0/0 stat grid — no error,
just meaningless output (confirmed live against run #11 before this was
fixed). `prospector/report.py` now reads `review_target_score` /
`opportunity_score` / the weak_gbp/has_negative_recent/missed_call_evidence/
no_booking/no_chat/phone_dependent flags / `director_name` / `is_chain`
directly, matching `targets.py`'s sort and chain-exclusion behaviour — see
the module docstring for the full history.

**Setup**: `prospector report` needs Playwright's Chromium browser, which
is a separate download from the `playwright` pip package:

```bash
playwright install chromium
```

## Live reports (Cloudflare Pages)

Every run's report can also go live as a branded HTML page — same design
as the PDF, viewable in a browser, with same-origin download buttons for
that run's PDF and CSV — plus a master index of every run ever generated.
Both are pushed to Cloudflare Pages by `prospector/deploy.py`, reusing the
credentials/pattern already set up for geo-prospecting's `antek-claim`
Pages site (`CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` in `.env`,
`npx wrangler pages deploy`, no global install needed).

```bash
# Generate the PDF/CSV for a run AND deploy it (+ the whole site, index
# included) in one step — the "standard output at the end of a run":
prospector report --run-id 11 --deploy

# Or deploy without regenerating the PDF (fast — reuses whatever's already
# in reports/runs/ + exports/runs/ for this run and every other run):
prospector deploy --run-id 11
# --force re-renders this run's PDF/HTML even if one already exists
```

Live site: **https://jv-prospecting-reports.pages.dev**
(`/` = master index of every run; `/runs/<id>/` = that run's live report,
with `PROSPECTOR-RUN-<id>-<area>.pdf` and `run_<id>_<timestamp>.csv`
co-located alongside it for the download buttons).

**How it works** (`prospector/deploy.py`):
- Rebuilds the *entire* site into `.pages_dist/` on every deploy — Cloudflare
  Pages has no incremental deploy, so each run's page must be reconstructed
  from files already tracked in `reports/runs/` + `exports/runs/`, not just
  the run you're currently deploying, or older runs would vanish from the
  live index.
- `discover_deployable_run_ids()` scans `reports/runs/PROSPECTOR-RUN-*.pdf`
  for every run_id that already has a report, intersected with the `runs`
  table.
- Per run, it tries a live re-render from the DB (`fetch_run_data` +
  `render_run_report`, freshest data, exact same template as the PDF). If a
  run's businesses no longer carry `review_target_score` in the current DB
  (some early runs predate a rescoring pass and can't be regenerated without
  re-running discovery/scoring, which is out of scope for a deploy step),
  it falls back to patching the *existing* static HTML in `reports/runs/`
  with the same download bar instead of dropping the run from the site.
- The master index (`/index.html`) is regenerated from the `runs` table
  every deploy — run id, area, vertical, date, business count, link to the
  live page, and direct PDF/CSV download links — so it's always current,
  not a one-off snapshot.
- Creates the Cloudflare Pages project (`jv-prospecting-reports`) on first
  deploy if it doesn't exist yet (`wrangler pages project create`), same
  convention as `antek-claim`.

**GitHub-side index**: `reports/INDEX.md` lists every run with links to its
GitHub-hosted PDF/CSV (`reports/runs/...`, `exports/runs/...`) *and* its
live Cloudflare Pages URL. It's a hand-maintained snapshot, not
auto-regenerated on every deploy (the live `/index.html` above is the
always-current one) — update it manually when you commit a new run's
`reports/runs/`/`exports/runs/` artifacts, mirroring the row format already
there.

## Prospector v2: discovery, reviews, site signals, targets

This is the new workflow built in Phases 2-5 of the "Prospector v2: UK
High-Ticket Firms, Review-Based Targeting" rebuild. It writes to the same
`businesses`/`runs`/`reviews` tables as the legacy `prospector run`
pipeline above (dedupe is shared across both), but is driven by four new
command groups rather than the interactive wizard, and doesn't do
ad-spend or pain-keyword scoring — it targets **weak Google review
profiles** instead.

```bash
# 1. Discover — vertical x location search across THREE discovery
#    sources (Google Places, Yell.com, SerpAPI organic search — see
#    "Multi-source discovery" below) + Companies House enrichment
#    (company number, incorporation date, status, established_flag for
#    3+ year old firms). Dedupes on google_place_id, then normalised
#    phone/domain fallback, against everything already in the DB
#    (including legacy-pipeline businesses) AND across sources within
#    the same discover_run call.
prospector discover run --vertical vets --location Winchester --max-results 20
prospector discover run --vertical "solicitors and conveyancers" --location Reading

# --source controls which discovery source(s) run (default: all three)
prospector discover run --vertical "air conditioning" --location Croydon --source places
prospector discover run --vertical "air conditioning" --location Croydon --source yell
prospector discover run --vertical "air conditioning" --location Croydon --source places,yell
prospector discover run --vertical "air conditioning" --location Croydon --source all   # same as omitting --source

# Bulk, from a CSV of vertical,location pairs (optional max_results column)
prospector discover import my_targets.csv --source all

# 2. Reviews — Google Places Details fetch (rating, review count, up to 5
#    review snippets) + review_target_score (0-100).
prospector reviews fetch --run-id 9
prospector reviews list --min-score 50

# 3. Site signals — lightweight homepage/contact-page fetch for
#    booking-widget/live-chat-widget absence and phone-dependency +
#    opportunity_score (0-100, separate score from review_target_score).
#    Fetch escalates through 3 layers if a site blocks the previous one —
#    httpx -> Playwright headless Chromium -> Apify browser actor (last
#    resort, costs a little money) — see "Site-fetch escalation" below.
prospector site fetch --run-id 9

# 4. Targets — combined sort (review_target_score desc, opportunity_score
#    desc tiebreak) + CSV export. Excludes is_chain=1 businesses by default
#    (see "Chain / franchise / corporate exclusion" below) — pass
#    --include-chains to see/export them anyway.
prospector targets list --min-score 50
prospector targets export exports/targets.csv
prospector targets list --min-score 50 --include-chains
```

`targets export` CSV columns (in order): `firm_name`, `vertical`, `location`,
`postcode`, `phone`, `email`, `website`, `review_count`, `avg_rating`,
`review_target_score`, `opportunity_score`, `flags`, `company_number`,
`director_name`, `years_trading`, `tps_checked`, `is_chain`, `chain_reason`.
`director_name` is the first natural-person officer name from the
Companies House lookup done during discovery (`businesses.director_name`,
same source `prospector report`'s contact block uses) — placed next to
`company_number`/`years_trading` since it's the other Companies-House-derived
field. `targets list`'s console output includes the same `director_name`
value (when present) alongside the score columns.

### Multi-source discovery (Places + Yell.com + SerpAPI organic search + Checkatrade.com)

`discover run`/`discover import` now query up to four discovery sources
per vertical/location, additively (not as a replacement for Google
Places) — Andy wanted extra coverage for businesses that don't surface
well on Places/Maps. Every source returns the identical dict shape
(`name`, `address`, `phone`, `website`, `domain`, `rating`, `review_count`,
`google_place_id`), so Companies House enrichment, chain detection,
reviews fetch, and site-signal fetch all run identically regardless of
which source found a given business — verified live, not assumed (see
each subsection below).

- **`places`** (default/primary, unchanged behaviour) — `places_client.py`,
  Google Places API (New) Text Search. Now sends `regionCode: GB` — see
  "Discovery source & fallback" above.
- **`yell`** (new) — `prospector/discovery/yell.py`. Yell.com, the UK's
  largest business directory, via the Apify actor
  `jungle_synthesizer/yell-uk-business-directory-scraper` (confirmed real
  and functional via Apify's actor-search API before use, not assumed —
  free pricing tier, actively maintained). Direct httpx/scraping against
  Yell was ruled out: `yell.com` returns HTTP 403 to a plain fetch and
  sits behind a Cloudflare challenge, so the Apify-actor route (which
  solves that challenge itself, same class of problem `enrichers/site.py`
  already handles for prospect sites) is the only realistic option here,
  not just the convenient one. Uses the same `run-sync-get-dataset-items`
  REST pattern as `enrichers/site.py`'s Apify fallback layer — same
  `APIFY_TOKEN`, different actor/input/timeout.
  - `google_place_id` is always `None` for Yell-sourced businesses (Yell
    has no Google Places ID); `businesses.yell_listing_id` carries Yell's
    own profile URL instead (migration v9, additive).
  - **Live-tested caveat:** Yell's site only recognises formal town/city
    names as a location — colloquial sub-regions fail outright. `"South
    London"` 404s (confirmed via the actor's run log — it solves the
    Cloudflare challenge fine, then gets a hard 404 on the resulting
    search URL); `"London"` and `"Croydon"` both work. This mirrors the
    Places `regionCode` issue in spirit (informal UK region names causing
    trouble) but surfaces as a clean failure rather than silent
    wrong-country leakage. `discover_businesses()` fails **soft** for
    this case — catches the error, logs it, returns `[]` — so a bad
    location string for Yell specifically never blocks the Places-sourced
    (or organic-sourced) results in the same `discover run` call. Prefer
    real town/borough names over colloquial regions when you want Yell
    coverage.
- **`organic`** (new) — `prospector/discovery/organic.py`. SerpAPI
  `engine=google` (regular organic web search, distinct from the
  `engine=google_maps` local-pack engine `serpapi_client.py` already
  used as the Places fallback — that fallback is unchanged), 2-3 pages
  deep (`start=0,10,20`, ~20-30 organic results per query). Filters out
  big directories/aggregators (Yell, Checkatrade, Trustpilot, Yelp,
  Facebook/Instagram/LinkedIn, Bark, MyBuilder, RatedPeople, etc. — see
  `EXCLUDED_DOMAINS` in `discovery/organic.py`) so this source surfaces
  independent business websites that rank organically, not directory
  listing pages (including Yell's own — we already query Yell directly as
  its own source, so re-finding Yell listing pages here would just be
  noise/near-duplication rather than new coverage).
  - Organic results carry a title/link/snippet, not a structured business
    record — there's no phone/address/rating field the way the other two
    sources provide one. `address`/`rating`/`review_count` are always
    `None`; `phone` is extracted from the snippet text on a best-effort
    basis (same UK-phone regex `enrichers/site.py` uses for
    phone-dependency detection) and is often `None` too. A genuinely
    weaker record than Places/Yell, by design — still useful as a lead
    (the domain/website is real), just don't expect a phone number to
    always be there without a site-fetch pass filling in more later.
- **`checkatrade`** (new) — `prospector/discovery/checkatrade.py`.
  Checkatrade.com, UK's other major trade directory (arguably more
  relevant than Yell for prospector's trade-heavy verticals — HVAC,
  plumbing, electrical, roofing, driveways, garage/window/conservatory
  installers), via the Apify actor `trev0n/checkatrade-scraper`. Added
  specifically because the Yell actor above is confirmed broken for
  multi-word keywords (see "Known limitations" below) — Andy asked for a
  better alternative, so the Apify Store was searched again (same
  actor-search-API verification process, not assumed) for both a fixed
  Yell actor and a Checkatrade one:
  - No better/fixed Yell.com actor exists in the Store —
    `jungle_synthesizer/yell-uk-business-directory-scraper` is still the
    only real one; it stays registered as `yell` (see caveat above),
    unchanged.
  - Of ~9 Checkatrade actors found, `trev0n/checkatrade-scraper` was
    selected after live-testing: real structured records (name, phone,
    website, city/areaServed, rating **out of 10** — normalised to a 0-5
    scale to match Places/SerpAPI, see `CHECKATRADE_RATING_SCALE_DIVISOR`
    in `scoring_config.py`, `reviewsCount`, accreditations), modest but
    real usage (46 users), modified 2026-08-07 (actively maintained). A
    higher-usage alternative (`vulnv/checkatrade`, 270 users) was passed
    over because its `category` input is an opaque numeric-ID enum with
    no documented mapping and no freeform escape hatch — unusable for
    prospector's freeform vertical terms.
  - Live-tested against the exact class of bug that sank the Yell actor:
    a deliberately invalid multi-word trade slug via the actor's
    `searchUrls` input (bypassing its restrictive `trade` enum) still
    completed with 0 items rather than erroring/404ing the whole run —
    i.e. it fails soft on an unmatched slug, unlike Yell's hard failure.
    A real slug (`Air-Conditioning-Installation`) in `South London`
    returned 3 real businesses with working phones and 0-10 ratings.
  - Checkatrade only covers trade/home-services categories — prospector's
    non-trade verticals (solicitors, accountants, dental, vets, funeral
    directors) will just harmlessly return 0 Checkatrade results, same
    "additional pass, not guaranteed to fire for every vertical" contract
    as Yell/organic.
  - No official slug mapping exists for prospector's freeform vertical
    terms; `checkatrade.py` builds a naive best-effort slug (title-case,
    hyphen-join the sector term) rather than hardcoding a mapping for
    ~1600 categories — works well for terms that happen to align with a
    real Checkatrade trade name, returns 0 (not an error) otherwise.
  - `google_place_id` is always `None` for Checkatrade-sourced businesses;
    `businesses.checkatrade_listing_id` carries Checkatrade's own profile
    URL instead (migration v10, additive).

**Cross-source dedupe + provenance.** Sources run in order (`places`,
`yell`, `organic`, `checkatrade`) against the same DB connection within
one `discover run` call, so a later source's phone/domain dedupe check
sees rows an earlier source in the *same* call already inserted —
cross-source dedupe falls out of the existing place_id/phone/domain
dedupe logic (`db.find_business_by_place_id`/`find_business_by_phone_or_domain`)
for free, no new matching logic needed. When a later source re-finds a
business an earlier source (or an earlier run entirely) already inserted,
that corroboration isn't silently dropped: `businesses.discovery_source`
(migration v9, additive) records every source that found a business,
joined with `+` — e.g. `places`, `yell`, or `places+yell` if both Places
and Yell independently surfaced the same firm. Pre-existing businesses
(all Places-era) were backfilled to `discovery_source='places'` by the
migration itself.

**Companies House / chain / reviews / site-fetch all verified
source-agnostic:**
- Companies House enrichment (`enrich_companies_house()`) keys off the
  business *name* only — unaffected by source.
- Chain/franchise detection (`chain_signals.detect_chain`) keys off
  name/domain/company-number — unaffected by source.
- Reviews fetch (`enrichers/reviews.py`) previously *required*
  `google_place_id IS NOT NULL` to even be considered — which would have
  silently excluded every Yell/organic-sourced business from ever getting
  a `review_target_score`, and therefore from ever appearing in `targets
  list`/`export` (both filter on `review_target_score IS NOT NULL`). Fixed
  as part of this work: `db.businesses_needing_review_fetch` no longer
  restricts on `google_place_id`, and `fetch_and_score()` now treats a
  missing `google_place_id` the same as "Places lookup found no listing"
  (`found_listing=False`, `weak_gbp=True`) rather than skipping the
  business outright — so Yell/organic-sourced businesses still get scored
  and can still surface as targets.
- Site-fetch (`enrichers/site.py`) keys off `website` only — unaffected by
  source (and Yell/organic-sourced businesses generally *do* have a
  website, since that's most of what those sources return).

### Organic-search cross-check / validation

`prospector crosscheck organic` (`prospector/enrichers/crosscheck.py`) —
answers "how much of SerpAPI organic search's output is a genuinely
findable/real local business, vs. unvalidated noise?" for businesses with
`discovery_source LIKE '%organic%'`. Two checks, both reusing existing,
already-working clients (per the standing "prefer a real API over new
scraping" rule — Playwright is only used, via `enrichers/site.py`'s
existing `_fetch_with_escalation`, when there's genuinely no API path,
i.e. independently reading a business's own homepage):

1. **GBP cross-check** — `places_client.find_place_by_name(name,
   location)` (new; a single-result, name+location-targeted Places Text
   Search, distinct from `discover_businesses()`'s paginated
   vertical-wide sweep). If a real GBP listing turns up, it's strong
   validation the organic result is a real, findable business, and its
   phone/postcode/rating/review_count/google_place_id are backfilled onto
   the row (only into fields that were still `NULL`) —
   `gbp_crosscheck_status='validated_gbp'`. If no match is found even
   with this more targeted query, that's recorded, not discarded:
   `gbp_crosscheck_status='no_gbp_found'`, plus a free-text
   `gbp_crosscheck_note` disambiguating (best-effort) between "looks like
   a real business with a weak/absent GBP presence" (the organic result's
   own homepage independently shows a phone/contact signal, or a prior
   `site fetch` pass already found one — reused, not re-fetched) and
   "the organic 'business name' matches a listicle/aggregator-title
   pattern, likely not a real business record at all" (same failure mode
   `discovery/organic.py`'s own docstring already calls out, e.g. "Local
   Air Conditioning Companies in London for AC...").
2. **Companies House cross-check** — already runs for every discovered
   business regardless of source: `discovery/places.py`'s `discover_run()`
   calls `enrich_companies_house()` unconditionally inside the per-business
   loop, not gated on which source found the business, so organic-sourced
   businesses already get the same CH name-search as Places/Yell/
   Checkatrade-sourced ones (verified by reading the code — this was
   already source-agnostic, nothing needed fixing). `crosscheck.py` just
   *reads* the result: a live (non-dissolved) `companies_house_number` on
   the row counts toward `organic_validated` without a second lookup.

`businesses.organic_validated` (migration v11, additive) = 1 if either
check corroborates the business; both a `validated_gbp` GBP match and a
live CH match set it (an `error` GBP lookup — e.g. a transient Places API
failure — doesn't count either way, and is left retriable via `--refresh`).

**Real test: run #16 (South London air conditioning), 14 organic-only
businesses (`discovery_source='organic'`, ids 403-416).** Results:

| Outcome | Count |
|---|---|
| GBP match found (`validated_gbp`) | 13 / 14 |
| No GBP match, but homepage independently looks real (weak_gbp candidate) | 1 / 14 |
| Live Companies House match | 0 / 14 |

At face value that reads as "13/14 validated" — but re-checking the 13
matches' `google_place_id`s surfaced a real caveat, so this **should not
be read as 13 genuinely distinct, confirmed net-new businesses**:

- The 13 `validated_gbp` matches collapse onto only **8 distinct real GBP
  listings**. Two clusters of near-identical-generic organic names
  ("Air Conditioning London" / "Air Conditioning Units Installation In
  South London" / "Aircon Installation In South London" / "London Air
  Conditioning Specialists" — 5 records; "Air Conditioning Installation &
  Maintenance" / "Air Conditioning Companies in South London" — 2 records)
  all matched the *same* generic top-ranked GBP listing for their shared,
  non-specific name-derived query. That's a false-positive risk inherent
  to name-targeted search when the organic-derived "name" is generic
  (exactly the title-cleaning weakness `discovery/organic.py` already
  documents) — it confirms *a* real air-conditioning business exists for
  that query, not that *this specific website* is that business.
- 2 of the 8 distinct GBP listings the crosscheck found
  (`ChIJbRk60iwNdkgRXzrkgJOpKG8` "Cooling Services Ltd",
  `ChIJd_cwXoUBdkgRtLAo6mG2xjY` "Associated Cooling Services") turned out
  to be `google_place_id`-identical to businesses **already in the same
  run from the `places`/`places+organic` sources** (business ids #389,
  #391) — i.e. these 2 organic "net-new" records were actually
  rediscoveries of already-known businesses that the original
  phone/domain dedupe missed (organic records had no phone/domain overlap
  with the Places-sourced row at insert time, since organic's own record
  had `phone=NULL`), not genuinely new leads.
- That leaves **4 organic records with a solid, specific, plausibly
  net-new GBP validation**: "Cool Electrics..." → Cool Guys Air
  Conditioning Ltd, "Air Conditioning Wandsworth London" → DG Air
  Conditioning Ltd, "Local Air Conditioning Companies in London for AC..."
  → The Air Conditioning Company, "South Eastern Air Conditioning (London)
  Ltd." → SOUTH EAST AIR-CONDITIONING & HVAC LTD — an interesting case
  since the last one *looks* like the most "real" business name of the 14
  and validated cleanly.
- The 1 `no_gbp_found` record ("Stanley Cool: HVAC Services London") has
  an independently-verified phone number on its own homepage — a genuine
  `weak_gbp` candidate (no discoverable Google Business Profile at all,
  exactly prospector's target pain-point), not a listicle false-positive.

**Before/after example** (id 405, "Cool Electrics: Air Conditioning
Installation in South West..."): before crosscheck —
`phone=NULL, postcode=NULL, rating=NULL, review_count=NULL,
google_place_id=NULL`; after — `phone='020 3130 4033',
postcode='SW11 2PR', rating=5.0, review_count=2,
google_place_id='ChIJRdn-hHcFdkgRKPz5qaZXPmE'` (matched GBP name: "Cool
Guys Air Conditioning Ltd" — a plausible near-name match, not identical,
worth a manual glance before outreach). Id 416 ("Stanley Cool"): no
backfill (no GBP match), `gbp_crosscheck_status='no_gbp_found'`,
`gbp_crosscheck_note` explains the homepage-based weak_gbp reasoning.

**Honest quality assessment:** organic search is worth keeping as a
discovery source, but its raw output should not be trusted at face value
— only the ~4/14 (29%) genuinely distinct, specifically-matched
validations and the 1/14 (7%) independently-verified weak_gbp candidate
are solid outreach-ready leads from this batch; the 2/14 (14%) that
collided with already-known businesses should be treated as duplicates,
not new leads; and the 5/14 (36%) whose "match" collapsed onto a shared
generic listing should be manually spot-checked rather than treated as
confirmed (the `gbp_crosscheck_status='validated_gbp'` flag alone isn't
sufficient evidence when the matched `google_place_id` is shared across
multiple organic records in the same run — a quick `GROUP BY
google_place_id HAVING COUNT(*) > 1` on `discovery_source LIKE
'%organic%'` rows is the cheapest way to spot that pattern). Recommended
policy: treat `organic_validated=1` **and** a `google_place_id` not
shared with another row in the same run as the bar for "trust this
organic result without a manual check"; discard/deprioritize the rest
until `site fetch` + a human glance confirms it.

### Chain / franchise / corporate exclusion

**Standing rule, added post-rebuild:** Andy's pitch (AI call answering +
review generation) targets owner-operators who feel a missed call
personally — not corporate entities with dedicated marketing/reception
teams. `prospector` now flags **corporate, franchise, or chain businesses**
(`businesses.is_chain`) and excludes them from `prospector targets
list`/`export`'s default view. Nothing is deleted — flagged businesses stay
in the DB with a `chain_reason` audit trail; pass `--include-chains` to
`targets list`/`targets export` to see/export them anyway (e.g. to sanity
check why something got flagged).

This was prompted by a live discovery test that caught "Mildmay Veterinary
Hospital" — a CVS Group plc sibling practice — sliding through as an
apparently-independent target: it scored `established_flag=1` (incorporated
1985) and had no `is_group_owned` signal, because Companies House
name-search false-positive-matched it to an unrelated London hospice
charity ("MILDMAY HOSPITAL LTD", zero PSC records) rather than the vet
practice's real (and not name-matchable) corporate structure. Verified live
against the real Companies House API while building this fix — see
`prospector/chain_signals.py`'s module docstring for the full trace.

Three signals feed `is_chain`, most to least reliable (`chain_reason`
records exactly which fired):

1. **Known chain/franchise brand name or domain match** (primary signal) —
   `prospector/chain_signals.py`, config-only (no code changes needed to
   add a brand): `CHAIN_BRAND_NAMES_BY_VERTICAL` (per-vertical UK chain
   lists — e.g. vets: CVS Group, IVC Evidensia, Linnaeus, Medivet,
   Vets4Pets; dental: mydentist, Bupa Dental Care, Portman Dental Care,
   Rodericks Dental; estate agents: Purplebricks, Foxtons, Connells,
   haart, Hunters; similar lists for solicitors/conveyancers, accountants,
   heating/plumbing/electrical, roofing/damp/driveways, funeral directors,
   garage/window/conservatory installers — see the file for full lists),
   `GENERIC_CHAIN_BRAND_NAMES` (cross-vertical corporate groups, e.g.
   Rentokil, Anglian Home Improvements, HomeServe), and
   `CHAIN_DOMAIN_PATTERNS` (chain-owned web properties, e.g.
   `cvsvets.com` — this is what actually catches Mildmay, since CVS Group
   routes every sibling practice's site through its own domain even though
   the Google-listed business name stays the pre-acquisition local trading
   name). Matching is case-insensitive substring, scoped to the
   business's own vertical plus the generic list — deliberately **not**
   every vertical's list combined, since some brand strings (e.g.
   "hunters") are short enough to false-positive against an unrelated
   vertical (caught in testing: "Hunters Brook" kitchen fitters wrongly
   matched the estate-agents-only "hunters" entry before this was scoped).
2. **Companies House ownership** — reuses the pre-Phase-1 ad-spend model's
   `is_group_owned` concept (`companies_house_client.py`'s PSC/officer
   check, still in the codebase; `discovery/places.py`'s
   `enrich_companies_house()` now also runs it, using the same matched
   company_number as the incorporation-date lookup rather than a second
   name search). Flags when a Companies House PSC is a corporate entity
   rather than an individual. Secondary/supporting signal only — as the
   Mildmay case shows, CH name-search can miss real chain ownership
   entirely; it can in principle also mismatch to an unrelated
   same-named company.
3. **Multi-location** — the same `domain` or `companies_house_number`
   already appears on another discovered business (any run, any
   vertical). Extends the existing place_id/phone/domain dedupe
   (`db.py`, Phase 2) rather than duplicating it
   (`count_businesses_sharing_domain` / `count_businesses_sharing_company_number`);
   free, no extra API calls. When a new discovery reveals an
   earlier-discovered business is also part of the same chain, both ends
   get retroactively flagged (`mark_chain_by_domain` /
   `mark_chain_by_company_number`).

`prospector chain rescan` recomputes `is_chain`/`chain_reason` for every
business already in the DB using only already-stored data (no new API
calls) — run once after upgrading to backfill businesses discovered before
this feature existed.

### Verticals config

`prospector/verticals.py` — `VERTICALS`, Andy's 9 target verticals for the
v2 rebuild: estate agents and lettings, solicitors and conveyancers,
accountants, heating/plumbing/electrical (larger firms, not sole
traders), roofing/damp proofing/driveways, private dental/cosmetic/
aesthetic clinics, vets, funeral directors, garage door/window/
conservatory installers. `--vertical` accepts a `VERTICALS` slug (e.g.
`vets`), its display name, or arbitrary freeform text (falls through to
using your text directly as the search term, so you're never blocked on
the seed list). This is a **separate config from `prospector/trade_sectors.py`**,
not a replacement — `trade_sectors.py`'s `ticket_size_estimate`-oriented
28-sector list still backs the legacy `prospector run` wizard used by the
8 pre-v2 runs, and removing/repurposing it would break that command.

`prospector/locations.py` — `LOCATIONS`, a starter UK town/city list;
`--location` also accepts any freeform UK town/city/postcode district.

### Scoring

Both scores are centralised in `prospector/scoring_config.py` — thresholds
and keyword lists are not hardcoded inline, so they're easy to tune.

**`review_target_score`** (0-100, capped) — `enrichers/reviews.py`:

| Signal | Points |
|---|---|
| review_count < 20 | +40 |
| review_count 20-50 | +20 |
| review_count 51-100 *(interpolated tier — see note)* | +10 |
| review_count > 100 | +0 |
| avg_rating 3.0-4.2 | +30 |
| avg_rating < 3.0 | +15 |
| avg_rating > 4.2 *(implicit — not a weak-review target)* | +0 |
| has_negative_recent (any review <=2★ in the returned set) | +20 |
| weak_gbp (no listing found, or no website AND no hours) | +10 |
| missed_call_evidence (negative review text matches a missed-call keyword) | +15 |

Andy's spec left a gap between the 50 and 100 review-count bands ("20-50:
+20. Over 100: 0."). We interpolated a third tier, 51-100 -> +10, rather
than a hard cliff at 50, documented in `scoring_config.py` alongside the
`MISSED_CALL_KEYWORDS` list.

**Coverage caveat (Andy's note, not a change request):** Google Places
Details returns at most 5 reviews per fetch, so `missed_call_evidence`
only catches complaints that happen to surface in that 5-review set. When
it does hit, it's a strong opener ("saw a review mentioning calls going
unanswered") — worth the flag despite patchy coverage. Built as
specified.

**`opportunity_score`** (0-100, capped, kept **separate** from
`review_target_score`) — `enrichers/site.py`:

| Signal | Points |
|---|---|
| no_booking (no booking-widget signature or "book online" language) | +5 |
| no_chat (no chat-widget signature or chat language) | +5 |
| phone_dependent (contact page: phone number/form, no callback-promise wording) | +5 |

Detection is heuristic HTML/text scanning — booking-widget signatures
(Calendly, Acuity, Fresha, Cliniko, etc.), chat-widget signatures
(Intercom, Drift, Tawk, Crisp, Zendesk, etc.), and keyword scanning, all
listed in `scoring_config.py` for Andy to tune as he sees false
positives/negatives on real prospect sites. Not designed to be perfect —
"it's a lightweight signal" per spec.

### Site-fetch escalation (httpx -> Playwright -> Apify)

`enrichers/site.py` fetches each prospect's homepage through **three
escalating layers**, cheapest/free first, stopping as soon as one
succeeds:

1. **httpx** (original Phase 4 mechanism) — plain HTTP client, up to 2
   retries with backoff, rotating User-Agent. Free (no external service),
   but some corporate sites' anti-bot protection blocks it outright.
   Confirmed live: CVS Group's site (`cvsvets.com`) returns **HTTP 406**
   to httpx on every retry, regardless of User-Agent.
2. **Playwright headless Chromium** — tried when httpx fails. **Not a new
   dependency** — it's the same Playwright install already used by
   `report.py`'s HTML→PDF pipeline (Chromium already cached at
   `~/.cache/ms-playwright`), just a new usage. **Cost: free** (local
   CPU/compute only), just slower than httpx (a real browser launch +
   render vs. a plain GET). In live testing against the same CVS Group
   URL, a bare `browser.new_page()` still got HTTP 406 — bypassing the
   block required a browser *context* with realistic `Accept`/
   `Accept-Language` headers, an `en-GB` locale, and Chromium's
   automation-controlled flag disabled (`--disable-blink-features=
   AutomationControlled`). With that context, the same URL returned a
   clean 200 with the full page HTML. These settings are in
   `scoring_config.py` (`PLAYWRIGHT_*`) so Andy can tune them further if
   another site needs something different.
3. **Apify** (`apify/rag-web-browser` actor) — last resort, tried only if
   Playwright also fails. **This is the one layer that costs real money**
   (small, but not zero — see below), so it's expected to rarely trigger:
   Playwright already gets past most anti-bot blocks that stop httpx. The
   actor is called with `scrapingTool: browser-playwright` explicitly set
   (its *default* mode, `raw-http`, is itself just a plain HTTP fetch —
   leaving the default would hit the same kind of block httpx already
   hit). Requires `APIFY_TOKEN` in `.env`; if unset, this layer is simply
   skipped (not an error) and the fetch falls through to "unreachable."

**Cost honesty, for Andy:** the `apify/rag-web-browser` actor itself is
listed under Apify's **FREE pricing tier** (no per-actor markup on top of
platform usage), but every Apify Actor run still consumes ordinary Apify
platform compute, billed against your account's plan/free-tier compute
credits — it is not literally $0 forever at any volume, just cheap and
covered by Apify's generous free-tier credits at the volumes this tool
should hit it (a rare last-resort path, not a routine third fetch
attempt). Playwright, by contrast, really is free beyond your own CPU
time — it never leaves your machine.

If all three layers fail, behaviour is **unchanged from the original
Phase 4 spec**: the business is marked "no signal detected"
(`no_booking=True`, `no_chat=True`, `phone_dependent=True`,
`opportunity_score=0`) — a fetch failure is never scored as if it were a
verified absence of booking/chat.

Which layer succeeded (or that all three failed) is logged to the console
during `prospector site fetch` (per-business line + an end-of-run tally
of `httpx`/`playwright`/`apify`/`unreachable` counts) and stored per
business in the `site_fetch_method` column (`schema_migrations` v8 — see
"Database" below), so Andy can see from the DB alone how often each
fallback layer was needed across a run, e.g.:

```sql
SELECT site_fetch_method, COUNT(*) FROM businesses
WHERE site_checked_at IS NOT NULL GROUP BY site_fetch_method;
```

### Compliance — PECR / TPS screening

Outreach against these targets is **phone-first** against corporate
numbers. Under PECR (Privacy and Electronic Communications Regulations),
that requires screening numbers against the Telephone Preference Service
(TPS) / Corporate TPS (CTPS) **before calling** — this tool does not do
that screening. `prospector targets export` includes a `tps_checked`
column, defaulting to `false` for every row, as a compliance placeholder
and reminder: **treat every exported number as unscreened until you've
manually checked it against TPS/CTPS**, and update the column (or your
own CRM record) once you have. There is no TPS-checking API integration
in this tool — that's a deliberate scope decision per Andy's spec, not an
oversight.

## Database

SQLite, `prospector.db`, three tables: `runs`, `businesses`, `reviews`.
`db.init_db()` runs automatically on every CLI invocation and is idempotent
(`CREATE TABLE IF NOT EXISTS` + a `schema_migrations` table tracking
applied migrations for any schema changes since the base schema).

The Prospector v2 rebuild's Phase 1 migration (`schema_migrations` version
2) dropped the ad-spend columns (`fb_ads_active`, `fb_ads_creative_count`,
`fb_ads_earliest_seen`, `google_ads_active`, `google_ads_creative_count`,
`google_ads_days_active`, `google_ads_advertiser_name`) from `businesses`
and the `pending_apify_runs` JSON column from `runs` (both were part of the
now-removed ad-spend module), via native `ALTER TABLE ... DROP COLUMN`
(SQLite 3.35+; this environment runs 3.40.1). Existing rows and all other
columns are preserved — this was a migration, not a destructive rewrite.
`prospector.db` is backed up (`prospector.db.bak-<timestamp>`) before any
migration that drops columns.

Migrations v3-v8 (Prospector v2 Phases 2-6 plus the site-fetch-escalation
follow-up) are all additive (`ALTER TABLE ... ADD COLUMN`,
nullable/defaulted) — no columns dropped, no rows touched:
- **v3** (Phase 2): `businesses.incorporation_date`, `company_status`,
  `established_flag`, `email`.
- **v4** (Phase 3): `businesses.worst_recent_rating`,
  `has_negative_recent`, `review_target_score`, `weak_gbp`,
  `missed_call_evidence`, `reviews_fetched_at`; `reviews.review_keyword_match`.
- **v5** (Phase 4): `businesses.no_booking`, `no_chat`, `phone_dependent`,
  `opportunity_score`, `site_checked_at`.
- **v6** (Phase 5): `businesses.tps_checked` (compliance placeholder, see
  "Compliance — PECR / TPS screening" above).
- **v7** (Phase 6): `businesses.is_chain`, `chain_reason` (chain/franchise/
  corporate exclusion, see "Chain / franchise / corporate exclusion"
  above).
- **v8** (site-fetch escalation): `businesses.site_fetch_method` — which
  of httpx/playwright/apify fetched the site (or NULL if unreachable/no
  website), see "Site-fetch escalation" above.
- **v9** (multi-source discovery): `businesses.yell_listing_id` (Yell's
  own profile URL, since Yell-sourced businesses have no
  `google_place_id`) and `businesses.discovery_source` (which source(s)
  found this business — `places`, `yell`, `organic`, or a `+`-joined
  combination, see "Multi-source discovery" above). Unlike v3-v8, this one
  isn't a bare `ADD COLUMN` — it also backfills every pre-existing row to
  `discovery_source='places'` (all of them were, in fact, Places-sourced,
  since Places was the only source that existed before this migration),
  so every row has a source, not just newly-discovered ones.
- **v10** (Checkatrade discovery source): `businesses.checkatrade_listing_id`
  (Checkatrade's own profile URL, same idea as `yell_listing_id`, since
  Checkatrade-sourced businesses have no `google_place_id` either — see
  "Multi-source discovery" above).
- **v11** (organic-search cross-check): `businesses.gbp_crosscheck_status`
  (`'validated_gbp'` / `'no_gbp_found'` / `NULL`), `gbp_crosscheck_at`,
  `gbp_crosscheck_note` (free-text reasoning, see "Organic-search
  cross-check / validation" above), and `organic_validated` (1 if a
  GBP match or a live Companies House match corroborates an
  organic-sourced business). Bare `ADD COLUMN`s, all nullable/0-default —
  no backfill needed since these only ever apply to businesses that have
  gone through `prospector crosscheck organic`.

`prospector.db` was backed up (`prospector.db.bak-<timestamp>-phase2`)
before v3-v6 were first applied, again (`prospector.db.bak-<timestamp>-phase6`)
before v7, again (`prospector.db.bak-<timestamp>-yell-organic-sources`)
before v9, and again (`prospector.db.bak-<timestamp>-checkatrade-crosscheck`)
before v10/v11. All 8 pre-existing runs / 291 businesses / 2302 reviews
(grown to 356 businesses / 13 runs by the time v9 ran, and to 404
businesses / 16 runs / 2580 reviews by the time v10/v11 ran) were verified
intact before and after every migration.

## Error handling

Every external call (SerpAPI, Companies House, Google Places) goes
through `prospector/http.py`, which retries up to 2 times with
exponential backoff on network errors, timeouts, 429s, and 5xx responses.
A failure on one business/sector is logged and skipped rather than
aborting the whole run. `enrichers/site.py` (Phase 4) reimplements the
same retry/backoff shape for `httpx` (since `http.py` itself is
`requests`-based and site.py is the one module using `httpx`), plus a
rotating User-Agent and a fixed delay between requests — and, since the
site-fetch-escalation follow-up, two further fallback layers
(Playwright, then Apify) if httpx itself is blocked; see "Site-fetch
escalation" above for the full chain and its cost implications.

## Notes / limitations

- SerpAPI's `google_maps` engine has no native radius parameter; radius is
  encoded into the search query text rather than geo-filtered precisely.
  Good enough for prospecting, not exact.
- Companies House name-matching is best-effort (`search/companies?q=name`,
  top result). It fails open — if no confident match is found, the business
  is not excluded by the ownership filter.
- The ad-spend module (Meta Facebook Ads Library + Google Ads Transparency
  checks via Apify) was removed in Phase 1 of the "Prospector v2: UK
  High-Ticket Firms, Review-Based Targeting" rebuild. The old targeting
  model scored businesses by ad spend; the new model (Phases 2-5, see
  "Prospector v2: discovery, reviews, site signals, targets" above)
  targets UK high-ticket firms with weak review profiles instead.
- Discovery's domain-fallback dedupe (Phase 2) is intentionally
  aggressive: a chain/group business with multiple branches on a shared
  corporate domain (e.g. a CVS Group vet practice) will dedupe against
  its first-discovered sibling branch even though they're different
  physical practices. This is the documented behaviour of "dedupe on
  phone/domain as fallback" per spec, not a bug — if Andy wants
  per-branch discovery for chains, place_id-only dedupe (`--` no CLI flag
  for this yet) would need to be added.
- `enrichers/site.py`'s heuristics will occasionally get blocked outright
  by anti-bot protection on larger/corporate sites (observed: a CVS Group
  site returning HTTP 406 even to a full browser User-Agent string over
  plain httpx) — as of the site-fetch-escalation follow-up this now
  retries via Playwright headless Chromium, then Apify, before giving up
  (see "Site-fetch escalation" above). Live-tested against the CVS Group
  site that originally exposed this gap: Playwright alone got past the
  block (200, full HTML) once given a realistic browser context, so Apify
  wasn't even needed for that case — Apify remains as a last resort for
  sites that block a real headless browser too. When all three layers
  fail, the result is still handled as "unreachable" (conservative flags,
  `opportunity_score` left at 0 rather than scored off unverified data)
  rather than crashing, so opportunity_score coverage will still be
  thinner for the rare site that defeats all three layers than for small
  independent ones.

## Known limitations — multi-source discovery (Yell, organic search, Checkatrade)

- **Yell.com actor bug**: `jungle_synthesizer/yell-uk-business-directory-scraper`
  works correctly for single-word keywords (e.g. "plumber") but its own
  URL-building logic 404s against real Yell.com for multi-word keywords
  (confirmed via direct Apify API testing — "air conditioning" alone,
  no location complexity, reproduces the failure). Since most of
  prospector's verticals are multi-word phrases ("heating plumbing and
  electrical contractors", "cosmetic dentistry / implant clinics", etc.),
  this actor is currently low-value for most real prospecting queries.
  `discover run` handles the failure gracefully (0 Yell results, logged,
  pipeline continues) rather than erroring the whole run. A follow-up
  search of the Apify Store (see "Multi-source discovery" above) found no
  better/fixed Yell.com actor — `jungle_synthesizer`'s is still the only
  real one in the Store — but did find a genuinely better UK
  trade-directory alternative in **Checkatrade** (`checkatrade` source,
  `trev0n/checkatrade-scraper`), now registered alongside Yell rather than
  replacing it (Yell still adds coverage Checkatrade doesn't, for the rare
  multi-word query that happens to work). Worth revisiting Yell again if
  the actor gets fixed upstream.
- **Checkatrade actor** (`trev0n/checkatrade-scraper`) is live-tested and
  working — real structured records with phone/rating/reviews, and
  (unlike Yell) fails soft rather than erroring on an unmatched multi-word
  trade slug. Its coverage is scoped to trade/home-services categories
  only — harmlessly returns 0 for prospector's non-trade verticals
  (solicitors, accountants, dental, vets, funeral directors) — and its
  trade-slug matching is a best-effort naive guess (title-case/hyphenate
  the sector term), not a hardcoded mapping to Checkatrade's ~1600 real
  categories, so hit rate varies by how closely a vertical's search term
  happens to resemble Checkatrade's own naming.
- **SerpAPI organic search** (`engine=google`, 2-3 pages deep,
  directory-domain exclusions) genuinely surfaces real independent
  businesses Places misses (confirmed live: 14 net-new "air conditioning"
  firms in South London beyond what Places+Yell found). BUT organic SERP
  snippets don't carry structured phone/address data the way Places does
  — most organic-sourced businesses land with `phone=NULL`,
  `postcode=NULL` until the `site fetch` stage scrapes their homepage.
  Result quality is also mixed: some results are real business names
  (e.g. "Cool Electrics", "South Eastern Air Conditioning (London) Ltd."),
  others are generic listicle/blog page titles picked up as the SERP
  result title rather than an actual business name (e.g. "Local Air
  Conditioning Companies in London for AC..."). Treat organic-sourced
  rows as lower-confidence than Places/Yell until `site fetch` has run
  — and now, **cross-checked** via `prospector crosscheck organic` (see
  "Organic-search cross-check / validation" above): live-tested against
  all 14 of run #16's organic-only businesses, 13/14 returned a GBP match
  and 0/14 a live Companies House match, but re-examining the matched
  `google_place_id`s showed those 13 "matches" actually correspond to
  only 8 distinct real businesses (generic organic names collapsing onto
  the same top-ranked GBP listing) and 2 of those 8 were already known
  from the Places-sourced pass in the same run (missed by the original
  phone/domain dedupe since the organic record had no phone/domain
  overlap at insert time) — leaving only ~4/14 (29%) as solid, specific,
  plausibly-net-new validated leads, plus 1/14 with an independently
  verified real business site and no GBP listing at all (a genuine
  `weak_gbp` candidate). **Recommendation**: keep organic as a discovery
  source, but only trust `organic_validated=1` rows whose
  `google_place_id` isn't shared with another organic row in the same
  run — everything else needs a manual spot-check before outreach.
