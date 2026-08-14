# Prospector — run report index

Every prospecting run that has a generated PDF/CSV in this repo, with links
to the GitHub-hosted files and the live Cloudflare Pages report.

The always-current version of this index is the deployed one:
**https://jv-prospecting-reports.pages.dev** (rebuilt automatically on every
`prospector report --run-id N --deploy` / `prospector deploy --run-id N` —
see README "Live reports (Cloudflare Pages)"). This file is a hand-maintained
snapshot for browsing straight from GitHub — update it when you commit a new
run's `reports/runs/`/`exports/runs/` artifacts.

Runs before today (2026-08-13) — #2 through #8 — have been removed from
this index and the live site at Andy's request; their artifacts still exist
in git history if needed. Runs #1, #9 and #10 were never listed here: they
never had a PDF/CSV generated and committed (run 1 predates the Prospector
v2 report rebuild; runs 9-10 were small ad-hoc test runs, 4 businesses
each). `prospector report --run-id <N> --deploy` will generate and publish
a page for any of them on request.

| Run | Location | Vertical | Date | Targets | PDF | CSV | Live report |
|---|---|---|---|---|---|---|---|
| #11 | Hampshire | Heating / plumbing / electrical (larger firms, not sole traders) | 2026-08-13 | 11 | [PDF](runs/PROSPECTOR-RUN-11-Hampshire-targets.pdf) | [CSV](../exports/runs/run_11_20260813T061939Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/11/) |
| #12 | South London | air conditioning companies (freeform vertical) | 2026-08-13 | 25 | [PDF](runs/PROSPECTOR-RUN-12-South-London.pdf) | [CSV](../exports/runs/targets_run12_south-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/12/) |
| #16 | South London | air conditioning companies (multi-source: places+yell+organic) | 2026-08-14 | 27 | [PDF](runs/PROSPECTOR-RUN-16-South-London.pdf) | [CSV](../exports/runs/targets_run16_south-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/16/) |
| #17 | North London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 19 | [PDF](runs/PROSPECTOR-RUN-17-North-London.pdf) | [CSV](../exports/runs/targets_run17_north-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/17/) |
| #18 | East London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 32 | [PDF](runs/PROSPECTOR-RUN-18-East-London.pdf) | [CSV](../exports/runs/targets_run18_east-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/18/) |
| #19 | West London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 9 | [PDF](runs/PROSPECTOR-RUN-19-West-London.pdf) | [CSV](../exports/runs/targets_run19_west-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/19/) |
| #20 | Central London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 5 (of 7, 1 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-20-Central-London.pdf) | [CSV](../exports/runs/targets_run20_central-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/20/) |

**London-wide air conditioning sweep — complete.** Chunked by sub-area
(North/South/East/West/Central) rather than one citywide query, per the
standing rule that single big-area queries plateau and duplicate. All 5
areas done: North (#17), East (#18), West (#19), Central (#20), South
(#12/#16, run twice — #16 supplements #12 with Checkatrade/Yell coverage
that didn't exist when #12 originally ran). Central London had heavy
overlap with the other 4 chunks (55 of 62 discovered results were
duplicates already in the DB), as expected for the geographic middle.
No wrong-country leakage found in any chunk (spot-checked). A DB-wide
chain rescan was run after all 5 chunks completed, so chain-exclusion
counts above reflect cross-chunk chain patterns, not just each area in
isolation.

**Process-reliability fix landed mid-sweep**: run #16's site-fetch step
was killed (exit 137) partway through with zero progress saved, because
`prospector site fetch` previously ran as one unbounded batch/transaction.
Fixed in commit 8ce2b6c — `site fetch` now auto-chunks into small batches
(10 businesses/batch by default, `--batch-size` to tune) so a crash can
only lose one batch's worth of work, and simply re-running the command
resumes automatically. Verified against the exact run that crashed
before — completed cleanly in 3 batches on retry.

**Wrong-category leaks manually caught and removed** (Andy spotted
"Alpinair" in run #19 — turned out to be a vehicle/car air-con specialist,
not building HVAC): "air conditioning" as a search term also catches
automotive AC shops, since it's ambiguous between building and vehicle
trades. A name-keyword scan plus a real title-fetch scan across all 135
businesses with a website found 6 genuine misfits, removed from the DB
and reports regenerated: Alpinair (auto AC), East London Recovery (car
breakdown, not aircon at all), Southwest Mobile Autocare Ltd, Eddie Mobile
Mechanic, plus two directory/aggregator pages masquerading as businesses
("...jobs in north london..." and "...Courses in London"). Two ambiguous
"Mechanical"-named businesses (Claremore, Ada Mechanical) were checked
and kept — both are real commercial building-services/plumbing firms, not
automotive, confirmed via their actual page titles rather than assumed
from the name alone.

**Content-validation filter added + retroactively applied** (Andy: some
organic results "look like blog posts and directories," asked for a
filter before DB insertion). New `prospector/discovery/validate.py`
checks each organic result's title against listicle/job/course phrasing
and, if it survives, does one page-content check (schema.org
LocalBusiness markup, blog signals, external-link density) — wired into
`discover_businesses()` so junk is never inserted going forward. Ran
retroactively against all 72 existing organic-sourced businesses: found
4 genuine junk rows (2 title-pattern directory/listicle pages already
caught by name, plus "HVAC companies in London (100 found)" — a directory
page — and "Air Conditioning Near Me London" — a listicle), removed from
runs #16/#18/#20, reports regenerated and redeployed. The link-density
check's first pass over-flagged (8 false positives — cert-body badges
like Gas Safe/NICEIC/TrustMark and CDN/font/social links were being
counted as "directory signal" on completely normal small-business sites)
— fixed with a non-signal-domain exclusion list before it touched the DB;
see commit history for the specific false positives caught in testing.

"Targets" = total businesses captured in the run (not filtered to
`review_target_score` — see `prospector/deploy.py::_run_meta` for why: a
handful of earlier runs' businesses no longer carry v2 scores in the
current DB, but the run itself is still real and worth listing).
