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
| #16 | South London | air conditioning companies (multi-source: places+yell+organic) | 2026-08-14 | 26 (of 27, 1 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-16-South-London.pdf) | [CSV](../exports/runs/targets_run16_south-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/16/) |
| #17 | North London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 15 (of 21, 6 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-17-North-London.pdf) | [CSV](../exports/runs/targets_run17_north-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/17/) |
| #18 | East London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 32 | [PDF](runs/PROSPECTOR-RUN-18-East-London.pdf) | [CSV](../exports/runs/targets_run18_east-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/18/) |
| #19 | West London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 9 | [PDF](runs/PROSPECTOR-RUN-19-West-London.pdf) | [CSV](../exports/runs/targets_run19_west-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/19/) |
| #20 | Central London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 5 (of 7, 1 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-20-Central-London.pdf) | [CSV](../exports/runs/targets_run20_central-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/20/) |
| #21 | Surrey | hvac | 2026-08-19 | 26 (of 31, 5 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-21-Surrey.pdf) | [CSV](../exports/runs/targets_run21.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/21/) |
| #22 | Guildford, Surrey | hvac | 2026-08-19 | 9 (of 18, 9 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-22-Guildford--Surrey.pdf) | [CSV](../exports/runs/targets_run22.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/22/) |
| #23 | Woking, Surrey | hvac | 2026-08-19 | 21 (of 26, 5 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-23-Woking--Surrey.pdf) | [CSV](../exports/runs/targets_run23.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/23/) |
| #24 | Reigate, Surrey | hvac | 2026-08-19 | 10 (of 14, 4 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-24-Reigate--Surrey.pdf) | [CSV](../exports/runs/targets_run24.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/24/) |
| #25 | Epsom, Surrey | hvac | 2026-08-19 | 20 (of 25, 5 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-25-Epsom--Surrey.pdf) | [CSV](../exports/runs/targets_run25.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/25/) |
| #26 | Camberley, Surrey | hvac | 2026-08-19 | 13 (of 22, 9 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-26-Camberley--Surrey.pdf) | [CSV](../exports/runs/targets_run26.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/26/) |
| #27 | South London | electricians (multi-source: places+checkatrade+organic) | 2026-08-20 | 41 (of 48, 7 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-27-South-London.pdf) | [CSV](../exports/runs/targets_run27.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/27/) |

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

**Surrey HVAC sweep** (Andy: "run hvac companies in Surrey", then "look for
more" after the initial county-wide query capped at 20/source). Chunked
by sub-area — Surrey (#21), Guildford (#22), Woking (#23), Reigate (#24),
Epsom (#25), Camberley (#26) — same reasoning as the London sweep: a
single big-area query plateaus under the per-source result cap. 144 raw
businesses found, 108 non-chain after chain rescan. Content validator
(see above) caught junk live during discovery on every chunk (job/course
listings, blog-signal pages). One gap found and fixed manually: the
validator's title/content checks don't catch a *real* business page that
is simply off-vertical — "air conditioning" as a search term also
surfaces vehicle AC shops (same issue as the "Alpinair" catch in the
London sweep). In run #26 (Camberley) this let through 2 automotive AC
shops (confirmed via their email domains — jdkautomotive.co.uk,
autotest.co.uk), 1 job listing (salutemyjob.com — slipped past the
title filter because "Air Conditioning Technician, Camberley, Surrey"
doesn't match the job-title regex), and 1 trade-news article
(designandbuilduk.net) — all 5 caught by a manual email/name scan after
site-fetch, removed, reports regenerated and redeployed. All other runs
scanned clean.

**Second gap, same run — 2 directory pages Andy spotted manually**
(dentons.net, industryoversight.co.uk). Root cause: the validator's
schema.org check treated "has LocalBusiness schema at all" as proof of a
single real business — wrong, because both directory pages mark up
*every listed business* with its own schema block (industryoversight.co.uk
carried 24 separate JSON-LD LocalBusiness blocks, one per listing).
Fixed two ways in `discovery/validate.py`: (1) new URL-path pattern check
(`/results/`, `order-by-relevance`, `?page=`, `/directory/`, etc) run
before any fetch; (2) rewrote the schema check to parse JSON-LD properly
and count *distinct business names* rather than raw block count — a real
business's repeated/nested schema all names itself once, a directory
names a different business per block. Block-count alone was tried first
and rejected: it false-positived on aacairconditioning.co.uk, a genuine
business whose page legitimately carries 23 LocalBusiness JSON-LD blocks
(same name repeated). Retroactively re-checked all 123 organic-sourced
DB rows against the fixed validator: 0 false positives on the previously-
verified good set, both directory pages still caught, removed from run
#26, report regenerated and redeployed.

**Chain signal extended: templated per-town "service area" pages** (Andy:
"some of the listings are location pages as well"). Root cause: 2 of 5
Camberley "location pages" were already caught by the existing
multi-location chain signal (same domain/CH number at 2+ discovered
locations) — but that signal only fires once a second location has
actually been discovered. The remaining 2
(ecorenewables.co.uk/air-source-heat-pump-installation-camberley/,
southernmaintenancesolutions.com/surrey-service-area/) hadn't yet been
cross-referenced. Added `match_service_area_url_pattern()` to
`chain_signals.py` — flags a URL as a service-area/location-landing-page
if it ends in "-{the searched town}", lives under a literal
"/service-area/"-style path, or matches "/locations/{town}/" — treated
as a chain signal (flag `is_chain`, don't delete) since the page belongs
to a genuine business, it's the business's *scale/independence* that's
in question, same as the multi-location signal. Re-running `chain
rescan` DB-wide with this signal newly flagged 10 more businesses across
runs #16/#17/#21/#22/#23/#26 (not just Camberley) — reports regenerated
and redeployed for all affected runs, target counts above reflect the
new totals.

**Third automotive-AC miss, this time undetectable by name alone**
(Andy: "car air conditioning in the guildford list"). Found: "Precision
Air Conditioning Service" on `car-air-conditioning-service-guildford.co.uk`
(email `PCScars@outlook.com`) — a plausible-sounding building-HVAC
business name with nothing automotive in it at all; only the domain gives
it away. Same root ambiguity as the earlier Alpinair/jdkautomotive/
autotest catches ("air conditioning" matches both building and vehicle
trades) but this one couldn't have been caught by a name-keyword check.
Added `looks_like_automotive_domain()` to `discovery/validate.py` — a
free, no-fetch domain-substring check (automotive/autotest/car-air-con/
vehicle-air-con/autocare/garage/tyres/bodyshop/car-servicing) run
alongside the title check, before any page fetch. Re-checked the whole
DB: only this one row matched (the earlier jdkautomotive.co.uk/
autotest.co.uk misses were already removed manually), removed, run #22
report regenerated and redeployed.

"Targets" = total businesses captured in the run (not filtered to
`review_target_score` — see `prospector/deploy.py::_run_meta` for why: a
handful of earlier runs' businesses no longer carry v2 scores in the
current DB, but the run itself is still real and worth listing).
