---
type: reference
date: 2026-10-14
tags: [lhoft, stokr, solar-bond, monitoring, automation, model]
ai-first: true
project: "[[projects/lhoft-stokr-case-2026/plan]]"
---

# Living factsheet: market monitor

## For future Claude

`python model/monitor/monitor.py run` fetches dated public market prices (Eurostat Luxembourg business power price; DE-LU solar capture price), writes them into a COPY of the solar bond workbook, recalculates it in Excel, checks the Python engine still matches to the cent, and lists every sentence in the factsheet and proposal whose displayed figure would change. Nothing it does changes the base model or the documents; an analyst approves or rejects each run, and that decision is only logged. Built 2026-10-14 for the LHoFT x STOKR entry ([[projects/lhoft-stokr-case-2026/handoff-2026-10-12]]); first live run: capture price EUR 60.67/MWh against the model's EUR 45, 16 sentences stale.

## What it does

One command, run from the project folder `projects/lhoft-stokr-case-2026`:

```
python model/monitor/monitor.py run
```

Data flow:

1. **Fetch.** One small module per source in `sources/` (add a third by writing one module and listing it in `sources/__init__.py`). Every value is stored with unit, period, source URL, `fetched_at` and a status: `ok`, `source unavailable, using last good value`, `source unavailable, using model input (no cached value)`, or `failed`. Last good values live in `cache/`.
   - `host_cost` (EUR/MWh, Inputs!C17): Eurostat `nrg_pc_205`, LU, band ID (2,000-19,999 MWh), excl. VAT, newest semester with a value. The JSON-stat index is decoded with strides, and every non-time dimension is checked to be the one category asked for.
   - `capture` (EUR/MWh, Inputs!C21): DE-LU day-ahead price weighted hour by hour by solar generation, trailing 12 months (UTC days). This is the same definition as the German "Marktwert Solar" the base value comes from (45.08 for 2025, green-2-market.de). The plain baseload average is fetched for context and NOT written into the model. Source order: Fraunhofer energy-charts API (3 tries, 5 s and 20 s waits), then SMARD (Bundesnetzagentur public API, filter 4169 price and 4068 solar, region DE-LU), then cache, then the model input. Both series are averaged to whole hours before weighting.
   - Method check, done once on 2026-10-14: the same code on calendar 2025 via SMARD gives a capture price of EUR 46.06 and a baseload of EUR 89.33, against the published 2025 solar market value of EUR 45.08. The EUR 1 gap comes from UTC hours and a different solar series; the method is right, and the result is not adjusted.
2. **Scenario copy.** `solar_bond_model.xlsx` is copied to `runs/<YYYY-MM-DD_HHMM>/model-market.xlsx`; the changed inputs are written into the copy's Inputs sheet with Excel COM (refusing any cell that holds a formula), `CalculateFull`, save, and the saved values are read back.
3. **Facts export.** The project's own `export_facts.py`, `proposal_facts.py` and `feasibility_facts.py` run unchanged against the copy (in a scratch folder deleted afterwards) and write `runs/<run>/facts/`. `mc_facts.json` is carried over: the Monte Carlo is not re-run.
4. **Reconcile.** `mc_engine.reconcile()` on the recalculated copy: 19 rows x 25 years x Case A, Case B, Downside, plus the IRR and payback cells. PASS needs a difference under EUR 0.50 (it has been EUR 0.000000) and proof that the engine read the new inputs.
5. **Base check.** The unchanged base workbook is exported again and compared with the published `model/*facts.json`. If they differ, the report says so, because then not every drift would come from the market.
6. **Drift check.** `claim_checker.py`'s numbers layer (no AI call) traces every displayed number in `deliverables/factsheet.html` and `deliverables/proposal.html` to a BASE fact; that fact's market value is compared at the precision shown. A sentence is stale when one of its numbers would change.
7. **Report.** `runs/<run>/report.html` (one A4 page) and `report.json`: what was fetched, old and new inputs, nine key outputs (lowest cover in the plan and in the both-hosts-leave downside, missed payments, debt capacity at 1.30x, year-1 revenue, hosts' after-tax savings, owner payback, owner return after tax with and without the bond), the stale sentences with old and new figures, and the checks. `runs/latest.json` points to the newest run and to the newest live run.
8. **Human approval.** `python model/monitor/monitor.py approve <run-id> --by NAME --note "..."` (or `reject`) appends one line to `approvals.csv`. **It does not change the base model, its facts or the documents.** An analyst applies an approved change by hand (edit the Inputs cell, recalc, `python export_facts.py`, re-run the claim checker and re-render), so every published figure keeps one owner.

## How to run

```
python model/monitor/monitor.py run                              # live data
python model/monitor/monitor.py run --simulate host_cost=145     # drift test, labelled SIMULATED
python model/monitor/monitor.py run --simulate capture=30
python model/monitor/monitor.py approve 2026-10-14_1812 --by Manuel --note "..."
python model/monitor/monitor.py reject  2026-10-14_1809_SIM --by Manuel --note "..."
python deliverables/render.py model/monitor/runs/<run>/report.html 120   # PDF + PNG of the report
```

A simulation skips the fetch, changes only the inputs named, and is marked SIMULATED in its folder name and report. It must flag: `host_cost=145` flags the three owner-economics sentences in the proposal (payback, return 4.1%, return with the bond 3.1%) and nothing in the factsheet; `capture=30` flags the cover, capacity, CFADS, owner-return and export-price sentences (15).

`claim_checker.py` gained one option, `--facts-dir DIR` (and `load_facts(folder)`), to load `facts.json` and `*_facts.json` from another folder. With no option it behaves exactly as before: the factsheet and proposal `--no-ai` reports were identical before and after the change (0 unmatched on both), and `tests/bad-factsheet.html` still shows its planted errors (1 unmatched, 5 wording flags).

## Scheduling (not registered)

`run_monitor.ps1` runs one live pass and appends its console output to `runs/monitor.log`. To run it weekly on Monday 07:00, Manuel can register it himself:

```
schtasks /create /tn "LHoFT-StokrMarketMonitor" /sc weekly /d MON /st 07:00 /tr "powershell -NoProfile -ExecutionPolicy Bypass -File \"<project folder>\model\monitor\run_monitor.ps1\""
```

Leave out `/ru SYSTEM`: Excel COM needs Manuel's logged-in session, so the task runs only when he is logged on. Remove it with `schtasks /delete /tn "LHoFT-StokrMarketMonitor" /f`. It fits vault rule 6 ([[decisions/rule-6-amendment-daily-scan-2026-09-09]]): deterministic, no LLM, writes only new files under `model/monitor/runs/` and `cache/`, logs every run. It has NOT been registered.

## Honest limits

- **Market prices barely move this bond's cash flows.** 70% of output is sold to the hosts at a fixed EUR 110/MWh (+1% a year), and the tender premium fixes a reference price on exported power. Only the hosted roofs' 30% surplus (no premium on it, p2 = 0) and everything after the premium's 15 years sell at the market price. The host grid price changes only the hosts' savings, never the cover. In the first live run a 35% higher capture price (EUR 45 to 60.67) lifted the plan's lowest cover by 0.09x (1.50x to 1.59x). That is why the downside cover (0.94x, both hosts leave in year 5) does not move: from year 5 all output earns the premium until year 15.
- **This is a pre-issue tool.** After issue the same pipeline is meant to run each quarter on meter and payment data (generation, PPA invoices paid, premium received, reserve balance) so the factsheet reports actuals against plan. It is not built for real meter data yet: there is no meter or payment source, and the scenario only overwrites Inputs cells.
- **Monte Carlo is not re-run**; Monte Carlo figures in the documents never show as stale.
- **Tracing is by value.** The checker traces each number to one fact with that value at the shown precision. The drift check corrects two known traps: a number traced through an unnatural reading (EUR 2M read as a cover ratio of 1.6 rounded to 2) is moved back to its natural reading; a number traced to a feasibility fact pinned at the old input value (e.g. "grid EUR 121.9") is checked against its identical Summary twin, which does move. A "pay back in about 14 years" figure, which the checker files as a period, is checked against payback figures only. These two second looks are tagged "check by eye". Small bare whole numbers (table headers such as "11") are listed in `report.json` under `weak_moves` but not flagged.
- **Year indexes and words in prose are not checked.** "Both leaving puts payments behind from year 5" (grant route, EUR 1.0M bond) is read by the checker as a period, not a figure. Under the 2026-10-14 capture price that case is never late (`feas.aid_1m.leave2_first_late` 5 to 0, EUR 1.01M owed at year 15 to 0), and the monitor did NOT flag the sentence. Before approving, read `report.json` > `drift` > `moved_facts`, which lists every headline fact that moves with its base and market value.
- **Feasibility scenarios are pinned.** `feasibility_facts.py` runs grid prices of 115, 121.9 and 145 by name, so if Eurostat moves, the "central 121.9" label in those facts goes out of date until the script is edited.
- **energy-charts returned HTTP 503 or timed out (60 s) on 2026-10-14 from 14:00 to at least 18:16**, so the live capture price came from SMARD; the energy-charts parser follows the API's documented format (`unix_seconds`, `price`; `production_types` named "Solar") but has not yet run against a live response.
- The trailing 12 months is a moving window, not the calendar year the base input describes; a dated sentence ("about EUR 45 per MWh in 2025") stays true and is listed because the model input it stands for would change.

## Files

- `monitor.py` (CLI), `sources/` (`common.py`, `eurostat.py`, `wholesale.py`), `scenario.py` (copy, COM recalc, facts export), `drift.py` (stale sentences), `report.py` (HTML).
- `runs/<run>/`: `model-market.xlsx`, `facts/`, `report.json`, `report.html` (+ `.pdf`, `-p1.png` once rendered). `runs/latest.json`, `runs/monitor.log`.
- `cache/<input>.json`: last good fetched value per input. `approvals.csv`: decisions.
