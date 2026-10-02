# Results log

Every experiment run from Phase 1 (ASMC) and Phase 2 (MCO) gets recorded
here. Nothing has been run yet — this is the convention to log against once
Phase 1 starts.

## Suggested layout

One CSV per task pair for raw runs, plus one summary file per phase:

```
results/
├── raw_data_retrieval.csv
├── raw_classification.csv
├── raw_aggregation.csv
├── asmc_summary.csv       <- fitted variable_cost/fixed_cost/crossover per task pair, filled in after Phase 1
└── mco_case_study_1.csv   <- Phase 2's org #1 (own Oracle Cloud deployment) comparison
```

## Raw run columns (`raw_*.csv`)

| column | meaning |
|---|---|
| `date` | when the run happened |
| `task_pair` | `data_retrieval` / `classification` / `aggregation` |
| `side` | `engineered` / `llm` |
| `volume_n` | requests in this run (10 / 100 / 1000 — real; higher tiers are projected, not logged here) |
| `trial` | trial number within this volume tier (Phase 3 wants ≥5) |
| `tokens` | token count (LLM side only; blank for engineered) |
| `latency_ms` | measured latency for this request/batch |
| `cost_usd` | actual $ cost for this run -- real billed cost (e.g. $0 on an Always Free compute tier, actual metered Ollama cost on the LLM side), not an estimate |
| `cost_usd_paid_tier_sensitivity` | optional, engineered-side only: what this run would cost on paid-tier infra pricing instead of a free tier, for sensitivity analysis -- blank where not applicable (e.g. the LLM side, whose cost is already real and tier-independent) |
| `accuracy_pass` | `true`/`false` against the Phase 0 accuracy floor for this task |
| `notes` | anything odd — rate limiting, retries, throttling observed |

## Summary columns (`asmc_summary.csv`)

| column | meaning |
|---|---|
| `task_pair` | which of the three |
| `side` | `engineered` / `llm` |
| `variable_cost_usd` | fitted, per request |
| `fixed_cost_usd` | fitted, one-time |
| `crossover_volume` | where engineered cost-per-request = LLM cost-per-request |
| `projected_10k_usd` / `projected_100k_usd` / `projected_1m_usd` | from `variable_cost + fixed_cost/N` |

## MCO columns (`mco_case_study_*.csv`)

| column | meaning |
|---|---|
| `task_pair` | which of the three |
| `volume_n` | matching the ASMC volume tier being compared |
| `true_cost_usd` | from `asmc_summary.csv` at this volume |
| `visible_cost_usd` | what the org's own billing/chargeback actually itemizes at this point |
| `mco_pct` | `(true_cost - visible_cost) / true_cost` — the opacity figure |
| `attribution_notes` | where the missing cost actually went (overhead line, unattributed, bundled into another line item, etc.) |
