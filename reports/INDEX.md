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
| #12 | South London | air conditioning companies (freeform vertical) | 2026-08-13 | 32 | [PDF](runs/PROSPECTOR-RUN-12-South-London.pdf) | [CSV](../exports/runs/targets_20260813T210703.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/12/) |
| #17 | North London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 20 (of 22, 2 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-17-North-London.pdf) | [CSV](../exports/runs/targets_run17_north-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/17/) |
| #18 | East London | air conditioning companies (multi-source: places+checkatrade+organic) | 2026-08-14 | 34 (of 35, 1 chain-excluded) | [PDF](runs/PROSPECTOR-RUN-18-East-London.pdf) | [CSV](../exports/runs/targets_run18_east-london.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/18/) |

**London-wide air conditioning sweep, in progress.** Chunked by sub-area
(North/South/East/West/Central) rather than one citywide query, per the
standing rule that single big-area queries plateau and duplicate. Done so
far: North (#17) and East (#18) London, plus the earlier South London
passes (#12/#16). West and Central London were not yet run — this session
hit repeated process-reliability issues (site-fetch steps getting killed/
losing track across session boundaries) that ate most of the available
time; North+East London were driven directly in the foreground once that
became clear, rather than continuing to lose progress via background
delegation. Run `prospector discover run --vertical "air conditioning
companies" --location "West London" --source places,checkatrade,organic
--max-results 60` (then reviews fetch / site fetch / chain rescan / report
--deploy) to continue the sweep for West and Central London on request.

"Targets" = total businesses captured in the run (not filtered to
`review_target_score` — see `prospector/deploy.py::_run_meta` for why: a
handful of earlier runs' businesses no longer carry v2 scores in the
current DB, but the run itself is still real and worth listing).
