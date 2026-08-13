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

"Targets" = total businesses captured in the run (not filtered to
`review_target_score` — see `prospector/deploy.py::_run_meta` for why: a
handful of earlier runs' businesses no longer carry v2 scores in the
current DB, but the run itself is still real and worth listing).
