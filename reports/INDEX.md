# Prospector — run report index

Every prospecting run that has a generated PDF/CSV in this repo, with links
to the GitHub-hosted files and the live Cloudflare Pages report.

The always-current version of this index is the deployed one:
**https://jv-prospecting-reports.pages.dev** (rebuilt automatically on every
`prospector report --run-id N --deploy` / `prospector deploy --run-id N` —
see README "Live reports (Cloudflare Pages)"). This file is a hand-maintained
snapshot for browsing straight from GitHub — update it when you commit a new
run's `reports/runs/`/`exports/runs/` artifacts.

Runs #1, #9 and #10 aren't listed below: they never had a PDF/CSV generated
and committed (run 1 predates the Prospector v2 report rebuild; runs 9-10
were small ad-hoc test runs, 4 businesses each). `prospector report --run-id
<N> --deploy` will generate and publish a page for any of them on request.

| Run | Location | Vertical | Date | Targets | PDF | CSV | Live report |
|---|---|---|---|---|---|---|---|
| #2 | London | Cosmetic dentistry / implant clinics | 2026-08-09 | 96 | [PDF](runs/PROSPECTOR-RUN-2-London.pdf) | [CSV](../exports/runs/run_2_20260809T103717Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/2/) |
| #3 | London | HVAC engineers | 2026-08-09 | 47 | [PDF](runs/PROSPECTOR-RUN-3-London.pdf) | [CSV](../exports/runs/run_3_20260809T141259Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/3/) |
| #4 | Essex | Kitchen fitters / kitchen renovation | 2026-08-09 | 33 | [PDF](runs/PROSPECTOR-RUN-4-Essex.pdf) | [CSV](../exports/runs/run_4_20260809T124635Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/4/) |
| #5 | Essex | Cosmetic dentistry / implant clinics | 2026-08-09 | 19 | [PDF](runs/PROSPECTOR-RUN-5-Essex.pdf) | [CSV](../exports/runs/run_5_20260809T130510Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/5/) |
| #6 | Hampshire | HVAC engineers | 2026-08-09 | 31 | [PDF](runs/PROSPECTOR-RUN-6-Hampshire.pdf) | [CSV](../exports/runs/run_6_20260809T134145Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/6/) |
| #7 | Essex | Bathroom renovation | 2026-08-09 | 33 | [PDF](runs/PROSPECTOR-RUN-7-Essex.pdf) | [CSV](../exports/runs/run_7_20260809T141305Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/7/) |
| #8 | Essex | Landscaping companies | 2026-08-11 | 29 | [PDF](runs/PROSPECTOR-RUN-8-Essex.pdf) | [CSV](../exports/runs/run_8_20260811T210019Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/8/) |
| #11 | Hampshire | Heating / plumbing / electrical (larger firms, not sole traders) | 2026-08-13 | 11 | [PDF](runs/PROSPECTOR-RUN-11-Hampshire-targets.pdf) | [CSV](../exports/runs/run_11_20260813T061939Z.csv) | [Live](https://jv-prospecting-reports.pages.dev/runs/11/) |

"Targets" = total businesses captured in the run (not filtered to
`review_target_score` — see `prospector/deploy.py::_run_meta` for why: a
handful of earlier runs' businesses no longer carry v2 scores in the
current DB, but the run itself is still real and worth listing).
