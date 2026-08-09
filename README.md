# prospector

Local business prospecting pipeline for **Antek Automation**. Runs an
interactive wizard, pulls data from SerpAPI (Google Maps discovery +
reviews), Companies House (ownership/PSC check), and Apify (Meta + Google ad
spend signals), scores each business, and stores everything in SQLite with
CSV export.

This is a standalone tool that lives alongside Andy's existing
`geo-prospecting` project and **reuses its API keys** (SerpAPI, Companies
House, Apify) rather than requiring new ones — see "Credentials" below. It
does not modify or depend on `geo-prospecting`'s code.

## What it does

1. **Wizard** (`prospector run`) asks for area, radius, trade sector(s),
   minimum review count/rating, max businesses per sector, an ownership
   filter, an ad-qualification rule, and whether to dry-run.
2. **Discover** — SerpAPI Google Maps search per sector/area.
3. **Filter** — drop businesses below the rating/review thresholds or with
   no website.
4. **Reviews** — pull the latest ~20 Google reviews per surviving business
   and flag any that match a "pain" keyword list (missed calls, no
   response, etc.) — a strong signal that automation would help them.
5. **Ownership** — Companies House PSC/officer lookup, to filter out
   group/corporate-owned businesses (optional, on by default).
6. **Ad spend** — Meta (Facebook Ads Library) and Google (Ads Transparency
   Center) checks via two Apify actors, to find businesses already paying
   for ads (i.e. already have marketing budget and buying intent). See
   "Apify sync/async + NULL vs 0" below for how failures, empty results,
   and slow runs are handled.
7. **Score** — a plain weighted function (not ML) assigns Priority
   A/B/C and a numeric score. See `prospector/scoring.py`.
8. **Store** — everything lands in `prospector.db` (SQLite).
9. **Export** (`prospector export --run-id N --format csv`) writes a sorted
   CSV to `./exports/`.

A **dry run** stops after step 3 (discovery/filtering only) — no Companies
House or Apify calls, no spend, no DB writes — so you can see roughly how
many businesses would qualify and what Apify would cost before committing
to a real run.

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
  .env.example             # documents the 3 keys this tool needs
  prospector/
    __init__.py
    wizard.py                    # the interactive "series of asks"
    serpapi_client.py             # Google Maps discovery + reviews
    companies_house_client.py      # PSC/officer ownership lookup
    apify_client.py                 # Meta + Google ad spend actors — sync/async
                                      # polling + NULL-vs-0 policy (see below)
    scoring.py                       # priority scoring function
    db.py                             # SQLite schema + queries
    pipeline.py                        # orchestrates: discover -> filter ->
                                         # reviews -> ownership -> ads -> score -> store
    trade_sectors.py                    # TRADE_SECTORS constant
    cli.py                                # `prospector run` / `export` / `collect` / `list-runs`
    # --- internal support modules, not part of Andy's named list ---
    http.py                               # shared retry/backoff wrapper
    config.py                              # env vars, paths, pricing constants
    pain.py                                 # pain keyword list + matcher
    export.py                                # CSV export
exports/                    # CSV exports land here
prospector.db                # created on first run
```

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
the same `SERPAPI_KEY`, `COMPANIES_HOUSE_API_KEY`, and `APIFY_TOKEN` that
geo-prospecting already uses — nothing to configure out of the box.

If you ever want prospector to use its own separate keys, delete the
symlink and create a real `prospector/.env` file (see `.env.example` for
the three variables it reads). Keys are always read from `.env` at runtime;
none are hardcoded anywhere in the codebase.

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

# Collect any Apify runs that were still going after the 60s sync-poll
# window during `prospector run` (see "Apify sync/async" below)
prospector collect --run-id 1
```

### Wizard flow

1. Area (free text — town/city or postcode district)
2. Radius (5 / 10 / 20 miles / county-wide)
3. Trade sectors (multi-select checklist, grouped by category, + custom
   free-text sectors)
4. Minimum review count (default 10)
5. Minimum rating (default 4.0)
6. Max businesses per sector (default 25 — controls SerpAPI + Apify spend)
7. Ownership filter — exclude group/corporate-owned via Companies House
   (default Y)
8. Ad qualification — Meta OR Google / Meta AND Google / no requirement
   (default OR)
9. Dry run? (default Y — SerpAPI discovery only, no Apify spend)

After the wizard it prints a cost estimate (SerpAPI + Companies House are
free-tier/free; Apify estimate = `sectors x max_per_sector x ($0.00075 FB +
$0.002 Google avg)`) and asks for a final confirmation before spending
anything.

## Trade sectors

The full seed list (legal/medical, property, home improvement, trade
services, other independents — 28 sectors) lives in
`prospector/trade_sectors.py` as `TRADE_SECTORS`, a dict of
`{name, category, ticket_size_estimate, google_search_term}`. Add new
sectors there; the wizard picks them up automatically.

## Apify sync/async polling + NULL vs 0

`fb_ads_active` and `google_ads_active` are nullable ints, and the NULL vs
0 distinction is meaningful and preserved end-to-end (schema ->
`apify_client.py` -> `pipeline.py` -> `scoring.py` -> CSV export):

- **NULL** = unknown / couldn't check (actor run failed, timed out, is
  still running, or came back empty — see below).
- **0** = checked, confirmed not active.
- **1** = checked, confirmed active.

Every Apify actor run is started asynchronously
(`POST /v2/acts/{actor}/runs`) and polled via
`GET /v2/actor-runs/{runId}` every 5s for up to 60s total (Apify's
synchronous run-and-wait endpoints only guarantee ~45s, which isn't always
enough). If a run is still going after 60s, prospector stops blocking: the
Apify run id is stashed on the current run's `pending_apify_runs` JSON
column (`runs` table) instead of failing or hanging the batch, and
`prospector collect --run-id N` picks up the result later, updating the
affected businesses' ad-active fields and re-scoring them.

A run that outright fails (or a single-business Facebook check that comes
back with 0 items) is set to NULL rather than 0 — these two actors are
known to be flaky, and an empty result for one lookup is indistinguishable
from "got blocked/rate-limited" without a second signal to check against.
The batched Google check is the one place a real 0 gets written: if the
batch as a whole succeeds with a non-empty result, any domain absent from
it is a confirmed 0 (the actor demonstrably ran and had the chance to
report on every domain in the batch); only a wholly empty/failed/pending
batch falls back to NULL for every domain in it. See the docstring at the
top of `prospector/apify_client.py` for the full policy and rationale.

`prospector/scoring.py` treats NULL as "unknown — don't reward, don't
penalize": only a channel confirmed `True`/`1` counts toward the
ads-channel score; NULL contributes 0, same as a confirmed 0, since the
scoring function never penalizes an inactive/unknown channel — it only
ever rewards a confirmed-active one. The NULL vs 0 distinction still
matters upstream in storage/export as a data-quality signal ("worth
re-checking via `prospector collect`" vs "genuinely no ads"), it just
doesn't change the score itself.

The exported CSV shows NULL ad-active fields as a blank cell (Python's
`csv` module writes `None` as empty), distinct from an explicit `0`.

## Scoring

`prospector/scoring.py` — plain weighted function:

- ads on both channels: +40, one channel: +20 (NULL/unknown channels don't
  count as active — see "Apify sync/async polling + NULL vs 0" above)
- pain-flagged review present: +25
- independently owned (not group/corporate): +15
- review count bonus: +1 per ~20 reviews, capped at +10

Priority bands:
- **A** — ads on both channels AND a pain-flagged review AND independent
- **B** — ads on one channel AND (pain-flagged review OR independent)
- **C** — everything else that survived the filters

## Database

SQLite, `prospector.db`, three tables: `runs`, `businesses`, `reviews`.
Schema in `prospector/db.py` matches the spec exactly, plus a
`runs.pending_apify_runs` JSON column (added via a `schema_migrations`
migration so it can be applied to a pre-existing database) for the
Apify async-collection fallback described above. `db.init_db()` runs
automatically on every CLI invocation and is idempotent (`CREATE TABLE IF
NOT EXISTS` + `schema_migrations` for any future changes).

## Error handling

Every external call (SerpAPI, Companies House, Apify) goes through
`prospector/http.py`, which retries up to 2 times with exponential
backoff on network errors, timeouts, 429s, and 5xx responses. A failure on
one business/sector/Apify actor run is logged and skipped — set to NULL
where it's an ad-active field, see above — rather than aborting the whole
run.

## Notes / limitations

- SerpAPI's `google_maps` engine has no native radius parameter; radius is
  encoded into the search query text rather than geo-filtered precisely.
  Good enough for prospecting, not exact.
- Companies House name-matching is best-effort (`search/companies?q=name`,
  top result). It fails open — if no confident match is found, the business
  is not excluded by the ownership filter.
- Do **not** wire up any Checkatrade or Trustpilot Apify actor — both were
  tested and found unreliable/broken as of Aug 2026. Stick to
  `curious_coder/facebook-ads-library-scraper` and
  `scrapesage/google-ads-transparency-scraper`.
