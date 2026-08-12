# prospector

Local business prospecting pipeline for **Antek Automation**. Runs an
interactive wizard, pulls data from Google Places (primary business
discovery) with SerpAPI as an automatic fallback (also used for reviews),
Companies House (ownership/PSC check), scores each business, and stores
everything in SQLite with CSV export.

**Prospector v2 note:** the original ad-spend targeting model (score
businesses by whether they run Meta/Google ads, via two Apify actors) was
removed in Phase 1 of the "Prospector v2: UK High-Ticket Firms,
Review-Based Targeting" rebuild. The new targeting model — UK high-ticket
firms with weak review profiles — is being built out in later phases; this
README reflects the current (Phase 1) state: ad-spend module gone,
everything else intact.

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
exports/                    # CSV exports land here
reports/                     # HTML + PDF reports land here
prospector.db                # created on first run
```

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
# One PDF per run: priority-tier counts, then a card per qualified
# business (name, address, priority tier + score, ad signals, ownership,
# top pain-flagged review quote, contact info) — sorted priority A->C then
# score, same order as the CSV export.
prospector report --run-id 1
# optional: --limit N (default 25) caps how many businesses are shown

# One-page sales-readiness snapshot for a single business.
prospector report --business-id 7
```

Both write an `.html` and a `.pdf` to `./reports/` (created automatically),
named `PROSPECTOR-RUN-<id>-<area>.pdf` / `PROSPECTOR-BIZ-<id>-<name>.pdf`.
The HTML is kept alongside the PDF for a quick eyeball in a browser without
regenerating.

**Setup**: `prospector report` needs Playwright's Chromium browser, which
is a separate download from the `playwright` pip package:

```bash
playwright install chromium
```

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

## Error handling

Every external call (SerpAPI, Companies House) goes through
`prospector/http.py`, which retries up to 2 times with exponential
backoff on network errors, timeouts, 429s, and 5xx responses. A failure on
one business/sector is logged and skipped rather than aborting the whole
run.

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
  model scored businesses by ad spend; the new model (later phases) targets
  UK high-ticket firms with weak review profiles instead.
