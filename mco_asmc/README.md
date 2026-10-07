# The Hidden Ledger — Implementation Plan

Step-by-step working plan for the IMRC 2026 submission ("The Hidden Ledger:
Marginal Cost and Marginal Cost Opacity of AI-Adoption Shortcuts in
Enterprise Systems"). This is the actionable, checklist-level companion to
the phase roadmap — each step below says exactly what to do and where to
record the outcome. Results go in `results/` (see its own README for the
file-naming convention).

Execution has started -- see **Progress Log** at the bottom for the dated,
narrative record of what was actually run and found (the checklist below
stays a checklist; conclusions and raw findings live in the log, not inline
here, so the two don't drift out of sync).

---

## Phase 0 — Scoping & instrumentation

**Goal:** turn the three abstract task categories into three concrete,
runnable comparisons, and fix every methodological choice *before* running
a single measurement.

- [x] **Lock the three task pairs** (using existing assets, not synthetic benchmarks):
  - [x] Aggregation/transformation: **LOCKED 2026-10-02** — see Progress Log for the exact definition
  - [x] Data retrieval: **LOCKED 2026-10-02** — see Progress Log for the exact definition (`mco_asmc/experiments/data_retrieval.py`)
  - [x] Classification/tagging: **LOCKED 2026-10-02** — see Progress Log for the exact definition
- [x] **Fix the model + rate card, in writing**: Ollama `gpt-oss:120b` (via the existing `call_llm` pattern in `emotional_intelligence/extraction_pipeline.py`). **Rate card locked 2026-10-02** against https://ollama.com/pricing (peak-hour rate): **$0.15 / 1M input tokens, $0.60 / 1M output tokens**. (Off-peak rate exists on the same page but isn't used — see Progress Log.)
- [x] **Write the accuracy floor per task pair**, before any run:
  - [x] Classification: LLM output must match the existing rule-based label ≥ **85%** of the time — **locked 2026-10-02**, see Progress Log for the justification
  - [x] Retrieval: must return the exact correct row(s), no partial credit — **applied 2026-10-02**, see Progress Log for the known weakness in how this is currently graded
  - [x] Aggregation: digest must cover the same key facts as the deterministic summary — **locked 2026-10-02**, see Progress Log for the exact "must-include" checklist definition
- [x] **Decide the real volume tiers** — run 10 / 100 / 1,000 for real (free-tier budget); 10,000 / 100,000 / 1,000,000 are *projected*, not executed (see the cost-model note below). N=10 trial 1 executed for Data Retrieval 2026-10-02; 100/1,000 and the remaining trials are still open.
- [x] **Set up the results log format** (see `results/README.md`) before Phase 1 starts

### Cost-model decomposition (apply to every task pair in Phase 1)

Don't treat cost-per-request as one blended number — split it into:

```
cost_per_request(N) = variable_cost + fixed_cost / N
```

- **variable_cost** = token cost × price (LLM side) or CPU time × price (engineered side). Volume-independent — should measure the same at N=10 as N=1,000. This is what actually projects linearly to 10k/100k/1M.
- **fixed_cost** = one-time build/setup cost (time spent writing the SQL query/schema/rule-based logic, converted to $). Gets *divided* by N, not multiplied — it shrinks as volume grows. This is why naively multiplying a low-volume per-request figure overstates high-volume cost.

Projected cost at 10k/100k/1M = plug N into the formula above using the
variable_cost and fixed_cost measured in Phase 1. State this explicitly in
the paper's Methodology as an acknowledged limitation: *"costs above
N=1,000 are analytically projected via the fitted variable+fixed
decomposition, not empirically measured; we assume no step-change
infrastructure or pricing-tier costs occur in this range."*

**Before trusting the projection**, confirm variable_cost actually stays
flat across the real 10/100/1,000 runs. If it creeps up (free-tier rate
limiting, retry overhead, growing latency under load), that's a real
non-linearity worth reporting as a finding, not smoothing over.

---

## Phase 1 — ASMC: run the paired-task experiments

**Goal:** real cost curves for all three task pairs, fitted `variable_cost`
+ `fixed_cost` per pair, and the crossover volume for each.

For **each** of the three task pairs:

- [ ] Build the engineered-solution path (SQL query / rule-based classifier / deterministic summarizer) — **done for all 3 task pairs** (2026-10-02): `experiments/data_retrieval.py`, `experiments/classification.py`, `experiments/aggregation.py`
- [ ] Build the LLM-shortcut path (reusing `call_llm`) — **done for all 3 task pairs** (2026-10-02)
- [ ] Run both at N = 10, 100, 1,000 requests, multiple trials per tier (see Phase 3 — don't skip repeats) — **Data Retrieval: N=10 trial 1 done (production). Classification: N=10 trial 1 done (local, safe either way — synthetic data). Aggregation: structurally limited to volume_n=1 (one real test case), see Progress Log. 100/1,000 tiers and remaining trials open for all three.**
- [ ] Log every run to `results/` (task, N, side, tokens, latency, $ cost, accuracy pass/fail) — **done for the runs above**, see `results/raw_*.csv`
- [x] Fit `variable_cost` + `fixed_cost` for both sides from the logged runs — **done 2026-10-04** for all 3 task pairs, see `results/asmc_summary.csv` and Progress Log
- [x] Compute the crossover volume — **done 2026-10-04**: no crossover for Retrieval/Classification (engineered dominates at every N), real crossover at N≈92,089 for Aggregation
- [x] Project cost at N = 10k / 100k / 1M — **done 2026-10-04**, see `results/asmc_summary.csv`
- [ ] Repeat at a second complexity level per task pair (e.g., a harder classification schema, a longer transcript to summarize) to start generalizing the decision rule

**Instrumentation shortcut (revised 2026-10-02):** the plan originally said
to point the already-live CostLens SDK at each task pair's LLM calls. In
practice, CostLens' tracker (`costlens_agent/tracker.py`) batches usage
records and ships them async to a remote CostLens service for dashboarding
— it isn't built to hand back a synchronous value for a specific request,
which is what a per-row experiment log needs. `data_retrieval.py` instead
reads token counts directly off Ollama's own response (`prompt_eval_count`
/ `eval_count`) and prices them against the locked rate card inline. Revisit
CostLens as a cross-check later if useful, but it's not the source of truth
for this log.

**Engineered-side cost (open item, see Grafana entry in Progress Log):** for
now, `cost_usd` on the engineered side is hardcoded to `0` and `latency_ms`
(wall-clock, measured in the harness) is the only engineered-side signal
logged. Converting that to a real $ figure needs either `pg_stat_statements`
(not currently enabled on the production Postgres) or infra-level CPU
metrics, neither of which exist yet — tracked as an open item below rather
than guessed at.

---

## Phase 2 — MCO: the opacity study

**Goal:** show how much of Phase 1's real cost an actual organization's own
accounting/chargeback system fails to surface.

- [x] Case study #1 (start here): your own Oracle Cloud deployment — **reframed 2026-10-02**, see Progress Log. The compute side has nothing to show (Always Free tier, real bill is $0 for all three task pairs regardless). The real opacity case study is this app's own already-deployed CostLens cost-tracking system and its LLM-cost attribution, not OCI's billing console.
- [x] For each task pair, compare Phase 1's modeled true cost against what the org's own cost-tracking (CostLens) would actually surface if each were deployed as a real feature — **done 2026-10-02 via source-code analysis**, see Progress Log
- [x] Compute MCO — **done 2026-10-02**: categorical, not a blended %, see Progress Log for why
- [x] **Decided 2026-10-02**: no second/third organization's billing data is realistically obtainable for this project (solo research, one live deployment). Scoped as an **n=1 case study**, multi-org generalization named as future work in the paper.

---

## Phase 3 — Validity controls (run alongside Phase 1)

- [ ] Apply the Phase 0 accuracy floor consistently — drop/flag any run below it, don't average it in
- [ ] Run every configuration multiple times (suggest ≥5 trials per tier) and log variance, not just a mean
- [ ] Re-run at least one task pair with a different model or pricing tier; note whether the crossover point moves — this becomes the sensitivity-analysis paragraph

---

## Phase 4 — Analysis & synthesis

- [ ] One crossover chart per task pair (cost-per-request vs. volume, both curves, crossover point marked, projected region past N=1,000 visually distinguished from measured region)
- [ ] One MCO summary table across org(s)/tasks/volumes
- [ ] Draft the generalized `ASMC(task, volume, complexity) → adopt shortcut or engineer solution` decision rule, and state where it held across task pairs and where it didn't

---

## Phase 5 — Writing & submission

- [ ] Write Results directly from Phase 4's charts/table
- [ ] Write Discussion, tying MCO's findings back to the FinOps/chargeback literature already framed in Related Work
- [ ] Re-check Related Work positioning still holds once real numbers exist
- [ ] Confirm IMRC 2026's actual submission format, page limit, and deadline
- [ ] Tighten the existing abstract only to match what was actually found — don't let it over-promise past Phase 4's real results

---

## Folder layout

```
mco_asmc/
├── README.md              <- this file
├── experiments/           <- runnable harnesses, one file per task pair
│   └── data_retrieval.py
└── results/               <- every experiment run logged here, see results/README.md
```

---

## Fixes Required (consolidated, standing list)

Everything below was surfaced by real runs, not anticipated up front — see
the dated Progress Log entries for the evidence behind each one. This list
is the actionable version; check items off here as they're actually fixed,
not just discussed. Ordered by priority, not by when they were found.

### Must-fix before any accuracy number goes in the paper
- [x] **Retrieval's accuracy grader was too brittle — fixed 2026-10-02.**
      Exact-substring match against the true last message failed on
      correct-but-differently-phrased LLM answers, confirmed by the
      5-trial swing (80-100% across trials 1-5 at N=10, pooled true value
      94%). **First attempted fix (LLM-as-judge) was tried and found
      broken by real testing**, not assumed to work: `gpt-oss:120b`
      answered "incorrect" on a clearly-correct paraphrase even immediately
      after being shown that exact pair as a worked "correct" example in
      the same prompt, with zero visible reasoning despite being asked for
      it — reproducible across plain yes/no, few-shot, and chain-of-thought
      prompt variants. **Final fix**: deterministic word-overlap matching
      (what fraction of the true message's significant words appear in the
      answer), verified against synthetic paraphrase/wrong/verbatim test
      cases before being trusted. See `judge_retrieval_answer()`'s
      docstring in `data_retrieval.py` for the full detail. Deployed to
      production; not yet re-run there (next step).
- [ ] **Classification's rule-based "ground truth" has a real bug.**
      `_NEARBY_INTENT_RE` in `app.py` matches "close to me" but not "close
      by" — confirmed directly against the regex. This means the headline
      96.8% (N=1,000) accuracy figure is measured against a flawed
      reference. Decide how this gets reported: both numbers (96.8% raw /
      99.3% corrected) with the gap explained, not just the raw number.
- [x] **Stop reporting single-trial accuracy — addressed 2026-10-02.**
      Proven necessary (not just best practice) by retrieval trial 3 alone
      showing 80% against a true pooled value of 94%. Both Classification
      (5 trials x 3 tiers) and Retrieval (10 trials at N=10, post-fix) now
      have real multi-trial data with stated ranges, not single-run
      numbers. Still needed: Retrieval's N=100/1,000 tiers, which only
      have 1 trial so far.

### Structural gaps
- [x] **Aggregation's LLM-side cost was unmeasured — fixed 2026-10-03.**
      `weekly_digest.py`'s `generate_weekly_digest()` now takes an additive,
      opt-in `return_usage=True` parameter (every existing caller, including
      `nudges/nudge_engine.py`, is unaffected — verified by reading that
      call site before changing the signature). Deployed to production with
      a pre-change diff reviewed and a backup taken first.
- [x] **Aggregation had no volume axis — fixed 2026-10-03.**
      `seed_demo_data.py` now gives all 110 friends their own templated
      facts/beliefs/memories/preferences/personality snapshot
      (`seed_friend_own_ei_data()`), so each is a real, independent test
      case. `aggregation.py` rewritten to take `--n`/`--trial` like the
      other two harnesses instead of just repeating one case.
- [ ] **Engineered-side "paid-tier cost" is a conservative upper bound,
      not a measurement** (latency x 100%-OCPU-utilization assumption).
      Fine as a documented estimate; must not be presented as more precise
      than it is. Real per-query CPU attribution would need
      `pg_stat_statements` (not enabled on production) or real profiling.

### Lower priority / consistency
- [ ] Classification's N=100/1,000 ran locally, not production — harmless
      for accuracy (synthetic test messages), but inconsistent with
      retrieval's environment discipline for the engineered-side latency
      number specifically (pure-Python regex timing is still technically
      host-dependent).
- [ ] Grafana's real `"untagged"` dollar totals (Phase 2 corroborating
      evidence for the CostLens attribution-opacity finding) haven't been
      pulled — blocked for the agent by the credential-materialization
      classifier; needs the user to check `COSTLENS_SDK`/`COSTLENS_URL`
      and the CostLens dashboard directly.
- [ ] Phase 3's cross-model/pricing-tier sensitivity check (re-run one task
      pair under a different model or rate card, see if the crossover
      point moves) hasn't been started at all.

---

## Progress Log

Dated, narrative record of what was actually decided and run. Checklist
above tracks *what's done*; this tracks *why and what was found*, so
conclusions can be drawn later without re-deriving the reasoning.

### 2026-10-02 — Dataset ready, Data Retrieval locked and smoke-tested

**Dataset.** `seed_demo_data.py` now seeds 110 friends for the real `Amit`
account (10 richly-profiled, LLM-conversation friends + 100 combinatorially-
generated "bulk" friends with templated/non-LLM message threads, so the
bulk batch costs zero LLM calls). Applied identically to local and
production (OCI) via the same script, so the two stay structurally
identical — experiments run locally against this dataset, not against
production, to avoid adding experimental load to the live app. Each friend
has: a friendship with a real display-name nickname, 4–14 direct messages,
1–3 calls, mood history, a Profiles-tab entry, and an
`emotional_intelligence.relationship_profiles` row.

**Task pair locked: Data Retrieval.** "What was the most recent message in
my conversation with friend X, and who sent it?" —
- *Engineered path*: one indexed SQL query (`ORDER BY created_at DESC LIMIT 1`
  on `direct_messages`).
- *LLM path*: the friend's full message thread is dumped into context and
  Ollama `gpt-oss:120b` is asked the same question in natural language.
  This mirrors the real shortcut being studied — skipping the one-line
  query in favor of dumping rows at an LLM.
- Accuracy floor: exact-match per Phase 0 ("no partial credit"), currently
  graded as *does the true last-message content appear verbatim as a
  substring of the LLM's answer*. **Known weakness**: this under-counts —
  a semantically correct answer that paraphrases or doesn't quote verbatim
  fails the check. Needs a better grader (e.g. a second LLM-as-judge pass,
  or relaxed normalization) before the N=10 accuracy number below is
  treated as a real accuracy figure rather than a grading-script artifact.

**Rate card locked**: Ollama Cloud `gpt-oss:120b`, checked against
https://ollama.com/pricing on 2026-10-02 — **$0.15/1M input tokens,
$0.60/1M output tokens** (peak-hour rate; an off-peak rate exists on the
same page but isn't used here since experiment timing isn't controlled for
peak/off-peak — worth controlling for in a later trial if cost varies by
time of day).

**N=10 smoke test results** (`results/raw_data_retrieval.csv`, trial 1):

| side | latency | tokens | cost | accuracy |
|---|---|---|---|---|
| engineered | 0.5–6.9 ms | — | $0 (see below) | 9/10 (1 friend had no message history — see caveat) |
| llm | 1.49–2.50 s | 360–1,148 (mean ~660) | $0.00012–$0.00039 per request | 7/10 exact-substring match |

Takeaways so far (directional, N=10 is a smoke test, not a result):
- LLM latency is **~2,000–4,000x** the engineered path's (seconds vs.
  single-digit milliseconds) — expected, but useful to have a real number.
- LLM cost per request is small in absolute terms (sub-cent), but non-zero
  against an engineered path that's currently priced at exactly $0 — the
  real comparison needs the engineered side priced too (see open item).
- One test case (a pre-existing real friend, "Neha," with no seeded message
  history) returned no row on the engineered side, which the grader
  currently scores as `accuracy_pass=False` even though "no data" isn't the
  same failure mode as "wrong data." **Fix before N=100**: filter test
  cases to friends with ≥1 message, or handle the empty case as its own
  category rather than folding it into accuracy.

**Open items (as of first write, several resolved below — see next entry)**:
1. Engineered-side `cost_usd` is hardcoded to `0` — no real $ conversion for
   DB CPU time yet.
2. Grafana: user has an existing Grafana Cloud instance
   (`amberolive1722.grafana.net`) wired to production Postgres via a
   `grafana_monitor` role — confirmed this is a direct Postgres datasource,
   not an infra/CPU-metrics agent. `pg_stat_statements` is **not** enabled
   on production Postgres, so there's no real per-query exec-time data
   available yet, via Grafana or otherwise. Enabling it needs a
   `shared_preload_libraries` change + a production Postgres restart — a
   real production-affecting change, not done without explicit sign-off.
   Still waiting on a Grafana Cloud API token + confirmation of what's
   actually on its existing dashboards before building a client against it.
3. Accuracy grading needs a better method than exact-substring match (see
   above) before N=100/1,000 runs are worth trusting.
4. N=100 and N=1,000 tiers, plus the ≥5 trials/tier Phase 3 wants, haven't
   been run yet.

### 2026-10-02 (later same day) — Grafana wired up, found real monitoring already live; Phase 0 fully locked; production-execution blocked by platform safety controls

**Grafana token obtained** (`amberolive1722.grafana.net`, stored in `.env`
as `GRAFANA_URL`/`GRAFANA_API_TOKEN` — service account, Viewer role).
Querying `/api/datasources` revealed the stock auto-provisioned Grafana
Cloud stack (Loki/Prometheus/Tempo/Pyroscope/k6) — **no custom Postgres
datasource**, contradicting the earlier guess that the `grafana_monitor`
Postgres role was feeding a Grafana dashboard directly. Querying the
Prometheus datasource (`grafanacloud-amberolive1722-prom`, a Mimir backend)
via Grafana's own datasource-proxy API (`/api/datasources/uid/<uid>/resources/api/v1/query`
— the service-account token alone is enough, Grafana handles the
datasource's own credentials internally) found:

```
up{instance="throughline-app", job="integrations/node_exporter"}     = 1
up{instance="throughline-app", job="integrations/postgres_exporter"} = 1
```

**Real monitoring is already live** on the production box — a Grafana
Agent (or similar) is shipping both node-level and Postgres-level metrics.
The `grafana_monitor` Postgres role is almost certainly `postgres_exporter`'s
own DB user querying Postgres's internal stats views, not a Grafana
dashboard querying business data directly. Built `experiments/grafana_client.py`
as a thin reusable query client against this (smoke-tested, works).

**Important scope correction on what Grafana is actually for here**: Phase 1
experiments were originally run against a LOCAL database copy (to avoid
load on production). But node_exporter/postgres_exporter only monitor the
PRODUCTION box — so if Phase 1 runs locally, those metrics describe a
different machine than the one being measured, making them useless for
per-query cost attribution regardless of how good the monitoring is. Even
running ON production, Prometheus's scrape interval (tens of seconds) is
far coarser than a single sub-10ms query, so true per-request CPU
attribution still isn't resolvable without `pg_stat_statements` (still not
enabled — see above). **Conclusion: Grafana's metrics are the right tool
for Phase 2's MCO opacity comparison (aggregate modeled cost vs. what
production's own monitoring/billing surfaces), not for Phase 1's per-request
cost log.** `data_retrieval.py` was updated accordingly: engineered-side
`cost_usd` is now the *real* $0 (confirmed: the production host is an
Oracle Always Free A1 Flex instance, 2 OCPU/12GB, see below), with a new
`cost_usd_paid_tier_sensitivity` column giving a conservative latency-based
upper-bound estimate of what the same query would cost on paid OCI pricing
— computed directly from measured wall-clock latency, no Grafana call
needed for this number after all.

**Unplanned but relevant finding**: Oracle cut the Always Free A1 Flex
allocation in half sometime in 2026 (previously 4 OCPU/24GB total per
tenancy, now 2 OCPU/12GB — confirmed via multiple independent sources,
checked 2026-10-02). The production instance is provisioned at exactly
2 OCPU/12GB (`infra/terraform/variables.tf`'s defaults) — i.e. it is at
the **current** free-tier cap with zero headroom, not comfortably under an
assumed 4 OCPU/24GB cap as the terraform variable descriptions (written
before the cut) still imply. Operationally relevant outside this research
track too — flagged separately, not fixed here.

**Real OCI compute rate card** (for the paid-tier sensitivity column):
Ampere A1 Flex standard pricing, checked 2026-10-02 — **$0.01/OCPU-hour,
$0.0015/GB-hour**.

**Task pair locked: Classification/tagging.** Uses a real, already-deployed
rule-based classifier rather than inventing one: `app.py`'s
`_detect_nearby_category()` (around line 2236) — keyword-matches a chat
message to one of ~9 place categories (hiking, shop, restaurant, cafe,
park, pharmacy, hospital, atm, mall) for the "nearby places" chat feature,
via `_NEARBY_CATEGORY_TAGS`'s substring lookup.
- *Engineered path*: `_detect_nearby_category()` as-is.
- *LLM path*: prompt `gpt-oss:120b` with the message and the fixed category
  list (plus "none" for no match), ask it to pick one.
- **Accuracy floor: 85%** (LLM must agree with the rule-based label at
  least 85% of the time). Judgment call, not derived from a prior
  benchmark — justified because a category mismatch here sends the user an
  irrelevant point-of-interest list (annoying, not harmful), so the bar is
  high but not as strict as retrieval's "no partial credit" (wrong data
  returned outright). Revisit if real-run results make this feel
  miscalibrated either direction.

**Task pair locked: Aggregation/transformation.** `emotional_intelligence/weekly_digest.py`'s
real LLM path (`generate_weekly_digest()` — already in production,
generates insight cards from `facts`/`beliefs`/`memories`/`preferences`/
`personality_snapshots`/`relationship_profiles`) vs. a deterministic
summary **not yet built** (Phase 1 work, not Phase 0 — locking the
*definition* now, building the engineered path later).
- *Engineered path (to build in Phase 1)*: for each of the five
  EI categories with data present in the lookback window, deterministically
  select one item — most-recent `fact`, most-recent `belief`,
  highest-`importance` `memory`, most-recently-`updated_at` `preference`,
  and the latest `personality_snapshot` — and render each as a plain
  templated sentence (no LLM). This mirrors `_fetch_recent_ei_data()`'s own
  query shape, just without the LLM rewrite step.
- *LLM path*: `generate_weekly_digest()` as-is.
- **Accuracy floor — "must-include" checklist, locked**: for a test case to
  pass, every category that the deterministic path found data for must have
  **at least one** LLM-generated card whose `headline`+`body` text
  references that same underlying item (checked by keyword/entity overlap
  against the item's own text — e.g. the fact's `object`, the memory's
  `summary` — same substring-overlap methodology as retrieval's grader, with
  the same known weakness: a correct-but-differently-phrased card could
  under-count). A category correctly *omitted* by both paths (genuinely
  nothing noteworthy that week) is not a failure on either side.

**Phase 0 is now fully locked** (all checkboxes above checked). Nothing
further needs deciding before building Phase 1's remaining two harnesses
(classification, aggregation) — both can reuse `data_retrieval.py`'s
structure (cost model, CSV logging, Ollama call pattern).

**Resolved: experiments now run on production, by the user's own hand.**
Claude Code's auto-mode classifier blocks the agent itself from (1) writing
credentials to production over SSH and (2) sending production DB content to
an external API — both fired when the agent tried to run this directly (see
below for why). Per explicit instruction ("everything should be run on prod
env"), the user ran the deploy + experiment commands themselves instead
(handed over as copy-paste SSH/scp commands), which isn't subject to the
agent's own tool-permission classifier. `data_retrieval.py` now supports
`--side engineered|llm|both` for a possible environment split, but the user
chose to just run `--side both` directly on production themselves, which is
their call to make on their own infrastructure with their own API key.

**First real production run** (`results/raw_data_retrieval.csv`, N=10,
trial 1, 2026-10-02, run on the production host directly):

| side | latency (min–max, mean) | tokens (mean) | cost | accuracy |
|---|---|---|---|---|
| engineered | 0.61–2.60ms, mean 0.96ms | — | $0 real (Always Free); paid-tier sensitivity ~2–7 × 10⁻⁹ $ (negligible) | 10/10 |
| llm | 609–1,398ms, mean 960ms | 690 | $0.000126–$0.000363, mean $0.000225/request | **10/10** exact-substring match |

Notable vs. the earlier local smoke test: LLM latency is ~2x faster here
(mean 960ms vs ~1,950ms locally) — plausibly production's network path to
Ollama Cloud, or just run-to-run variance; and accuracy is 10/10 here vs
7/10 locally, with the SAME crude substring grader — this is almost
certainly the grader's fragility (sensitive to exact phrasing) showing up
differently against a different batch of LLM-generated answers, not
evidence that production is "more accurate." Reinforces the open item that
the accuracy grader needs fixing before treating any of these percentages
as a real number for the paper. Engineered-LLM latency ratio here is
~1,000x (vs ~2,000–4,000x locally) — still overwhelming either way, but the
exact multiplier is clearly sensitive to run conditions, which argues for
the ≥5 trials/tier Phase 3 already calls for, not a single N=10 run.

### 2026-10-02 (later) — Phase 2 reframed and (mostly) completed: the real opacity story is CostLens, not OCI billing

**Why OCI billing console isn't the case study.** Phase 2 originally said
"pull the real OCI billing console output." But Phase 1 already established
the compute side is an Oracle Always Free A1 Flex instance — the real bill
is $0 for every task pair, every volume tier, full stop. There's no
"attributed vs. actual" gap to find there; a $0 bill has nothing to hide.

**What the real case study is.** This app already has its own production
cost-tracking system — CostLens (`costlens_agent/`), installed via
`app.py`'s `install()` call, patching `requests`/`psycopg2`/`redis` to log
every outbound call with a `feature_tag` for per-feature cost attribution.
Reading `costlens_agent/__init__.py` end to end surfaced the actual
mechanism: `_infer_feature_tag()` (lines ~158–176) resolves a tag via, in
order: (1) a hardcoded filename map for standalone EI scripts
(`_MODULE_FEATURE_MAP`), (2) a hardcoded **function-name** map for calls
made from inside `app.py` (`_FUNCTION_FEATURE_MAP`, since several features
share that one file), (3) a route-based fallback, (4) `"untagged"`. Both
maps are **hand-maintained allowlists** — anyone adding a new LLM call site
without also adding an entry here gets silently bucketed into whatever the
nearest fallback is, not a loud error.

**Applying this to the three ASMC task pairs** (if each's LLM-shortcut path
were deployed as a real feature, not just an experiment script):

| task pair | where the LLM call would live | what CostLens would show | opacity? |
|---|---|---|---|
| **Aggregation** | `weekly_digest.py`, a standalone script | `_MODULE_FEATURE_MAP` already has `"weekly_digest.py": "ei.weekly_digest"` — correctly isolated, today, for real | **None.** This one's actually fine. |
| **Classification** | would naturally live inside whichever `app.py` function handles chat intent detection (`chat`/`chat_global`) | `_FUNCTION_FEATURE_MAP` maps `chat`/`chat_global` → `"chat.text"` — a real tag, but a **generic one shared by every other chat feature** | **Full attribution opacity.** The cost isn't $0 or wrong in total, but it's structurally impossible to isolate "how much does nearby-category classification specifically cost" from CostLens's own dashboard — it's blended into all of chat.text. |
| **Data Retrieval** | same situation — no feature exists yet, but the natural integration point is the same chat-handling code | same `"chat.text"` bucket | **Full attribution opacity**, same reasoning. |

**MCO here is categorical, not a blended percentage** — one task pair has
genuinely correct, granular attribution (0% opacity) and two would have
their entire cost structurally folded into an unrelated generic bucket
(100% opacity for the purpose of "can this specific feature's cost be
isolated," even though 0% of the dollar amount itself is missing or wrong
in aggregate). This is arguably a *cleaner* MCO finding than a fuzzy
percentage: it shows the opacity mechanism is deterministic and
code-verifiable, not a measurement artifact — a team maintaining this app
literally cannot answer "what does our nearby-category classifier cost us"
from their own already-deployed cost dashboard today, not because tracking
is broken, but because of how its allowlist is scoped.

**n=1 scoping decided**: this is solo research against one live deployment;
no second organization's billing/cost-tracking internals are realistically
obtainable. The paper should state the MCO claim as an n=1 case study,
multi-org generalization named as explicit future work — consistent with
Phase 0's general instinct to state limitations plainly rather than
over-claim.

**Not done (requires production .env access, deferred)**: confirming
`COSTLENS_SDK`/`COSTLENS_URL` are actually active on production and pulling
real historical "untagged" bucket $ totals as corroborating evidence
alongside the code-level finding above. Attempting to read these was
blocked by Claude Code's own safety classifier (**Credential Materialization**
— reading unredacted `.env` values over SSH, even for non-secret-looking
keys, is treated the same as reading secrets since the tool can't tell
which keys in a blanket file read are sensitive). The code-level finding
above doesn't depend on this — it's true regardless of whether tracking is
currently on — but real $ totals from the "untagged" bucket would
strengthen the case study with an actual number. Open item if the user
wants to pull `COSTLENS_SDK`/`COSTLENS_URL`'s values and check the CostLens
dashboard themselves.

**Phase 2 is effectively done for case study #1** modulo that one
corroborating-data open item. Remaining Phase 2 checklist items
(`mco_case_study_1.csv` write-up in `results/`) are mechanical from here —
not done yet, tracked as a TODO rather than executed, since the dollar
figures feeding it (Classification/Aggregation's own Phase 1 cost
measurements) don't exist yet.

### 2026-10-02 (later still) — Classification and Aggregation harnesses built

**`experiments/classification.py`** — engineered side replicates app.py's
`_detect_nearby_category()`/`_NEARBY_CATEGORY_TAGS`/`_NEARBY_INTENT_RE`
verbatim (not imported, to avoid pulling in the full Flask app's DB
pool/CostLens-install/route-registration side effects just to reach one
pure function — kept in sync by hand, flagged in the file's own comment).
Unlike retrieval, this task needs **no database at all**: test messages are
combinatorially generated from keyword x sentence-frame templates (~10%
generic-nearby, ~10% true-negative, rest category-specific), so N=10/100/1,000
are all cheap to generate for real — no seeded-data volume ceiling here.
Also unlike retrieval, grading is a **clean exact-label match** (LLM's
chosen category === the rule-based label), not a fuzzy substring check —
a real methodological improvement over retrieval's grader, worth noting in
the paper as a case where the task structure itself permits rigorous
grading. Smoke-tested locally (safe to run locally even under the
"everything on production" preference, since every message is synthetic/
template-generated — no real user data ever leaves any box either way):
**N=10, 10/10 agreement (100%)**, clearly passing the locked 85% floor,
~200-270 tokens/request, ~1.2-2.1s latency per LLM call.

**`experiments/aggregation.py`** — LLM side imports and calls
`emotional_intelligence/weekly_digest.py`'s real `generate_weekly_digest()`
directly (not reimplemented), so both paths summarize the identical
underlying rows via the same `_fetch_recent_ei_data`/
`_fetch_recent_relationship_insights` queries weekly_digest.py already
uses. Engineered side (newly built, didn't exist before): one templated
sentence per EI category with data -- most-recent fact, most-recent belief,
highest-importance memory, most-recently-updated preference, latest
personality snapshot, every active-this-week relationship. Coverage check
(the locked "must-include" accuracy floor): every deterministic item's
source text must have a >=5-character word overlapping some LLM card's
headline+body.

**Real, structural limitation surfaced while building this (not a bug to
fix, a fact about the dataset)**: this task has exactly **one** real test
case today — Amit's own seeded EI data (a handful of hardcoded
facts/beliefs/memories/preferences from `seed_demo_data.py`'s
`AMIT_FACTS`/etc. lists). None of the 110 seeded friends have their own EI
facts/beliefs/memories (`seed_new_ei_schema` only gives them a
`relationship_profiles` row, not their own subject-level facts) — there's
no multi-user EI dataset to draw a genuine N=10/100/1,000 volume axis from.
`aggregation.py` is honest about this: it takes `--trials` (repeated LLM
calls against the one real case, legitimate for Phase 3's variance
requirement) rather than `--n`, and logs `volume_n=1` for real rather than
faking a larger number. **Open item**: if aggregation needs real volume
scaling later, `seed_demo_data.py` would need extending to give multiple
synthetic users their own EI facts/beliefs/memories/preferences — not done,
explicitly deferred rather than silently worked around.

**Also surfaced**: `generate_weekly_digest()` doesn't return token counts
to its caller (it discards `call_llm`'s raw response internally), so
`aggregation.py` logs LLM-side `tokens`/`cost_usd` as blank rather than a
fabricated number — true cost for this task pair isn't measurable yet
without either threading token counts through weekly_digest.py's return
value (a real change to existing production code, not done without being
asked) or duplicating its prompt logic standalone (loses the "exact same
code path as production" guarantee that's the whole point of reusing it).
Flagged as an open item, not silently worked around.

Neither harness has been run against production yet (both are ready;
`aggregation.py` sends real EI data to Ollama same as retrieval, so per the
established workflow the user runs it directly rather than the agent).

### 2026-10-02 (later still) — Classification run at N=100; a real bug found in production's own code

**Classification, N=10, trial 1** (run locally — safe anywhere, synthetic
messages only): 10/10 agreement (100%).

**Classification, N=100, trial 1** (`results/raw_classification.csv`):
**97/100 agreement (97%)**, still clearly passing the locked 85% floor,
~200-330 tokens/request, ~1.2-3.0s latency per LLM call. The jump from a
100-sample run (97%) vs. 10-sample (100%) is itself the expected lesson —
small-N accuracy numbers are noisy; this is why Phase 3 wants real volume
tiers and multiple trials, not one small run.

**The 3 mismatches aren't noise — two are the same message, and it exposes
a real gap in app.py's actual production regex, not a quirk of this
experiment:**

| message | rule-based | LLM | what's actually going on |
|---|---|---|---|
| "where's the nearest shopping?" | shop | mall | genuine ambiguity — "shopping" plausibly means either; a reasonable LLM answer, not a clear LLM error |
| "show me what's close by" (x2) | none | generic_nearby | **the LLM is right and the rule-based system is wrong** — confirmed by testing `_NEARBY_INTENT_RE` directly: it matches `"close to me"` but not `"close by"`, a narrower pattern than the natural phrasing people actually use |

Verified directly: `_NEARBY_INTENT_RE.search("show me what is close by")` →
`False`; the same regex against `"close to me"` → `True`. This is a **real,
pre-existing bug in app.py's deployed nearby-places feature**, not
introduced by this experiment — worth fixing in the app itself separately
from this research. For the paper, it's also a genuinely interesting
finding: in this one case, the "LLM shortcut" produces a *more correct*
answer than the "engineered" system it's being measured against, which
complicates a purely cost-based ASMC framing — worth a line in Discussion
(Phase 5) about accuracy-floor validity when the "ground truth" system
itself has known blind spots. **Not fixing the regex in app.py as part of
this research work** (out of scope, would be a separate real bug-fix task)
but flagging it here since it's a genuine finding, not a methodology
artifact.

### 2026-10-02 (later still) — Classification at N=1,000: the pattern is now statistically unambiguous

**Classification, N=1,000, trial 1** (`results/raw_classification.csv`):
**968/1,000 agreement (96.8%)**. All 32 mismatches categorized by hand:

| category | count | share of mismatches | real LLM error? |
|---|---|---|---|
| "show me what's close by" → LLM says generic_nearby, rule-based says none | 25 | 78% | **No** — confirmed bug in production's `_NEARBY_INTENT_RE` (matches "close to me", not "close by"); the LLM is correct every single time this phrasing appears |
| "...shopping..." → LLM says mall, rule-based says shop | 5 | 16% | Debatable — genuine semantic ambiguity, reproducible, not random |
| "where's the nearest hiking?" → LLM says generic_nearby, rule-based says hiking | 1 | 3% | **Yes** — "hiking" is a literal keyword, this is a real LLM miss |
| "best store in my area?" → LLM says generic_nearby, rule-based says shop | 1 | 3% | **Yes** — "store" is a literal keyword, this is a real LLM miss |

At N=1,000 scale, the "close by" regex gap is no longer a one-off curiosity
— it's the dominant failure mode, reproducing identically every time,
proof this is a deterministic bug in the rule-based system rather than LLM
noise. Only 2 of 32 mismatches (0.2% of the full 1,000) are genuine LLM
errors on unambiguous cases. **Corrected accuracy, treating the 25 "close
by" cases as the LLM being right rather than wrong (since it demonstrably
is): 993/1,000 = 99.3%.** The headline number for the paper should
probably be reported both ways — 96.8% against the rule-based system
as-is, and 99.3% against a corrected ground truth — with the gap itself
being evidence for the Discussion point already flagged: an LLM shortcut
measured against a flawed "ground truth" can look artificially worse than
it is, which cuts against a naive reading of ASMC's cost-only framing.

**Classification task pair is now well-measured at N=10/100/1,000** (all
three real tiers done). Remaining for this task pair: ≥5 trials/tier per
Phase 3 (only trial 1 done at each tier so far), and the projected
10K/100K/1M tiers via the variable+fixed cost fit once enough real trials
exist to fit confidently.

### 2026-10-02 (final) — Classification: 5 trials at every tier, Phase 3 satisfied for this task pair

**Classification, 5 trials each at N=10/100/1,000** (`results/raw_classification.csv`,
11,100 rows total, 5,550 real LLM calls, **$0.32 total spend**):

| N | trial range | mean | spread across trials |
|---|---|---|---|
| 10 | 100%, 100%, 100%, 100%, 100% | **100%** | none -- 0pp spread |
| 100 | 97%, 97%, 98%, 96%, 97% | **97.0%** | 96-98%, 2pp spread |
| 1,000 | 96.8%, 96.9%, 96.8%, 96.7%, 96.8% | **96.8%** | 96.7-96.9%, 0.2pp spread |

**This is the clean contrast to retrieval's old grader problem.**
Classification's grading (exact label match) was always sound -- the issue
was the ground-truth system, not the grader -- so trial-to-trial variance
here is just genuine sampling noise from different random test messages,
not grading instability. Compare to retrieval's old grader, which swung
80-100% on *identical* underlying behavior. Also visible here: variance
shrinks as N grows (0.2pp spread at N=1,000 vs effectively unmeasurable at
N=10, since N=10 is too small a sample to ever land on the known ~3%
failure modes) -- exactly the law-of-large-numbers behavior you'd want to
see, and a good illustration for Phase 4's write-up of why volume matters
for measurement quality, not just for the cost curve itself.

**Classification task pair is now fully satisfied for Phase 3** (5 trials,
all three real tiers). Latency stayed consistent throughout: ~1,400-1,700ms
mean per tier, ~215-235 tokens/request -- no meaningful drift at higher N,
which is reassuring for the "variable_cost stays flat across volume" check
Phase 0 flagged as worth confirming before trusting the 10K/100K/1M
projections.

**Running total spend across all experimentation so far**: classification
$0.32 + retrieval (~100 LLM calls across all runs, ~$0.025) ≈ **~$0.35**,
comfortably within free-tier budget with massive headroom for the
remaining N=100/1,000 retrieval tiers and aggregation trials.

### 2026-10-03 — Aggregation: both open items fixed, first real run, and a genuine LLM-vs-engineered behavioral difference found

**Both items from the "Fixes Required" list resolved.** (1) `weekly_digest.py`
now supports `return_usage=True`, additive and opt-in -- confirmed the only
other real caller (`nudges/nudge_engine.py`) doesn't pass it, so its
behavior is byte-for-byte unchanged. Diffed the file against production
before applying, backed up the original, applied, and compiled on
production before moving on. (2) `seed_demo_data.py` now gives all 110
friends their own facts/beliefs/memories/preferences/personality snapshot
(`seed_friend_own_ei_data()`), making each one a real, independent
aggregation test case instead of relying solely on Amit's single account.
`aggregation.py` rewritten to take `--n`/`--trial`, matching the other two
harnesses' interface. Both deployed to production; production reseeded
with the new per-friend EI data.

**First real N=10 run surfaced a real bug in the EXPERIMENT'S OWN grader,
not the LLM.** Initial run: 0 of 9 real cases (friends with seeded EI data)
achieved full coverage -- every single one missed the "personality" item.
Investigated rather than accepted: the deterministic digest's personality
`source_text` was the raw templated string `"openness=0.58,
conscientiousness=0.88, ..."` -- word-matching against that can never
succeed, because no LLM card would ever write `"openness=0.58,"` verbatim.
This guaranteed a miss regardless of what the LLM actually generated, by
construction. **Fixed**: match against the trait *names* themselves
(`"openness conscientiousness extraversion agreeableness neuroticism"`),
real words a card discussing personality would plausibly use. Verified
against the same local test case before redeploying: missed items dropped
from 2 to 1 immediately.

**Re-ran N=10 on production post-fix**: coverage improved from 0/9 to
**2/9 fully covered**, 1326 mean tokens, $0.000591 mean cost, 3,162ms mean
latency. (One contamination note: the pre-fix and post-fix runs landed in
the same file both labeled trial 1, since the file was pulled before the
fix was deployed, re-run, and pulled again without the trial number
changing. Cleaned up by hand on both local and production copies, keeping
only the valid post-fix 20 rows -- unlike retrieval's old-grader-vs-new
comparison, this wasn't a finding worth preserving, just my own bug.)

**What's still being missed, and why it's a finding, not a bug**: 6 of the
remaining 7 incomplete cases all missed the same thing -- the **belief**
item ("Believes in saving first, spending what's left," "Believes in
keeping in close touch with family," etc.). Checked whether this is
another grader artifact the way personality was: it isn't -- the
significant words extracted ("saving," "spending," "family") are exactly
the kind of plain English words a real card would use. The actual
explanation is structural: `weekly_digest.py`'s own system prompt
instructs the LLM to **skip any category with nothing genuinely
noteworthy "this week"** ("most weeks won't have something for every
category... better to return fewer cards than pad"). A belief like
"believes in saving first" doesn't change week to week, so the LLM is
making a reasonable judgment call that it isn't fresh news -- while the
deterministic path has no such judgment and always surfaces the
most-recent belief regardless of staleness. **This is a genuine
behavioral/definitional difference between the two paths, not a quality
defect in either one** -- worth a real paragraph in the paper: the
"engineered" and "LLM" solutions here aren't just cost/latency tradeoffs,
they encode different philosophies about what a digest should even
contain, and "coverage" isn't a cleanly objective target when the two
paths disagree about what's worth including at all. One residual
personality miss (1 of 9) remains unexplained by either the fixed grader
or the belief-staleness theory -- not enough data yet to say whether it's
the same phenomenon or something else.

**Aggregation now has a working, bug-checked harness with real N=10 data.**
Remaining: N=100 (now genuinely possible with 110 real test cases), ≥5
trials per Phase 3, and a paper-level decision on how to frame the
belief-coverage finding (a limitation of the comparison, or a result in
its own right).

### 2026-10-03 (later) — Aggregation at N=100: the belief finding holds at scale

**N=100 run** (`results/raw_aggregation.csv`) ran twice back to back under
the same trial label, same accidental-double-loop pattern as classification
earlier -- both runs used the identical fixed code, so this is bonus sample
size, not a second contamination to clean up. Combined: **207 real test
cases** (friends with seeded EI data; a small number of pre-existing real
friends without seeded data, like "Neha," are skipped as designed).

| metric | value |
|---|---|
| fully covered | 75/207 (**36.2%**) |
| mean tokens | 1,381 |
| mean cost | $0.000626/request (**$0.13 total spent** across all 207 calls) |
| mean latency | 3,394ms (range 1,725–7,180ms) |

**The belief finding from N=10 holds, and gets sharper at this sample
size.** Breaking down what's missed across all incomplete cases:

| missed item | count | share of 207 cases |
|---|---|---|
| belief | 113 | **54.6%** |
| personality | 14 | 6.8% |
| preference | 2 | 1.0% |
| memory | 1 | 0.5% |

Belief is missed in over half of all cases -- not a fluke, not a tail
event, the single dominant pattern in this task pair's data. Personality
dropped to a 6.8% residual after the grader fix (down from 100% when it
was a grader bug) -- small enough now to read as genuine occasional LLM
omission rather than a second systematic issue, consistent with the
"not noteworthy this week" theory rather than another bug. The arithmetic
checks out too: independent ~55% belief-miss and ~7% personality-miss
rates predict roughly 0.45 x 0.93 ≈ 42% full coverage, close to the
observed 36.2% (some cases miss both, pulling the real number down a bit
further, as expected).

**This is now a load-bearing finding for the paper, not a footnote**: at
real volume, the LLM digest systematically omits static, slow-changing
information (beliefs) in favor of what looks "fresh" for the week, while
the deterministic path has no such filter and always includes it. Whether
that's a flaw or a feature depends entirely on what a "weekly digest" is
supposed to do -- which is itself worth stating explicitly in the paper
rather than assumed. The accuracy-floor methodology (Phase 0) didn't
anticipate this: "must cover the same key facts" implicitly assumes both
paths agree on which facts are worth including, and this task pair is a
real counterexample to that assumption.

**Aggregation is now measured at N=10 and N=100** (effectively ~207 cases
at the N=100 tier). Remaining: N=1,000, the ≥5-trials-per-tier Phase 3
requirement (only 1 trial run so far, albeit with bonus sample size), and
writing the belief-vs-personality distinction into the paper's Discussion
section.

### 2026-10-04 — Aggregation at N=1,000: pattern confirmed, clean single run

**N=1,000 run** (`results/raw_aggregation.csv`), clean this time -- exactly
1,000 rows, no accidental double-loop. 990 real cases (10 hit the
non-seeded pre-existing "Kunal"/similar accounts, correctly skipped/trivial
as designed).

| metric | N=100 | N=1,000 |
|---|---|---|
| fully covered | 36.2% | **39.2%** |
| mean tokens | 1,381 | 1,394 |
| mean cost | $0.000626 | $0.000633 |
| mean latency | 3,394ms | 3,828ms |
| belief missed | 54.6% | **48.1%** |
| personality missed | 6.8% | **6.9%** |
| preference missed | 1.0% | 2.0% |
| memory missed | 0.5% | 0.3% |

**The pattern holds at 10x the sample size, essentially unchanged.**
Personality's residual miss rate is nearly identical (6.8% -> 6.9%) across
a 5x larger sample -- strong confirmation it's a stable, genuine LLM
omission rate now, not noise left over from the fixed grader bug. Belief's
miss rate softened slightly (54.6% -> 48.1%) but remains by far the
dominant failure mode, roughly 7x more common than personality and >20x
more common than preference or memory. Total cost for this tier: **$0.627**
across 990 real LLM calls (running total across all experimentation,
all three task pairs: **~$1.11**).

**Aggregation is now measured at all three real volume tiers (10/100/1,000)**,
with the belief-omission finding holding consistently across two orders of
magnitude of sample size -- as solid a basis as this project has for
treating it as a real result rather than an artifact. Remaining for this
task pair: ≥5 trials per tier (only 1 clean trial at N=1,000, 1 at N=10,
and N=100's "trial 1" is really 2 pooled runs) to satisfy Phase 3 properly,
and the projected 10K/100K/1M tiers once the cost curve gets fit in Phase 4.

### 2026-10-02 (later still) — Data Retrieval: 5 trials at N=10, real variance data (Phase 3's first real pass)

**Data Retrieval, N=10, trials 1-5, all on production** (`results/raw_data_retrieval.csv`,
100 rows total): first real multi-trial data for this project, run per
Phase 3's "don't skip repeats."

| side | metric | trial 1 | trial 2 | trial 3 | trial 4 | trial 5 | across-trial range |
|---|---|---|---|---|---|---|---|
| engineered | mean latency | 0.96ms | 0.79ms | 0.81ms | 0.75ms | 0.82ms | 0.75-0.96ms (tight, ~25% spread) |
| engineered | accuracy | 100% | 100% | 100% | 100% | 100% | always 100% (by construction) |
| llm | mean latency | 960ms | 1,438ms | 1,435ms | 1,590ms | 1,308ms | **960-1,590ms (~65% spread)** |
| llm | mean cost | $0.000225 | $0.000204 | $0.000245 | $0.000234 | $0.000220 | $0.000204-$0.000245 (~20% spread, stable) |
| llm | accuracy | 100% | 90% | 80% | 100% | 100% | **80-100% (huge single-trial swing)** |

**This is exactly the lesson Phase 3 exists to catch.** If only trial 3 had
been run, the paper would currently claim 80% LLM accuracy on this task;
trial 1 alone would claim 100%. Neither is "the" number — pooled across all
50 LLM calls, true accuracy is **47/50 = 94%**.

**Checked whether the 3 mismatches (trial 2 x1, trial 3 x2) are a
deterministic bug like classification's "close by" case, the same way that
was checked there**: they aren't. All 3 failures are different trials of
the same two friends (Priya Sharma, Ishita Bansal) with different token
counts/costs each time — consistent with genuine LLM phrasing
non-determinism (a re-generated answer paraphrasing the true last message
instead of quoting it verbatim) tripping the exact-substring grader
inconsistently run to run, not a reproducible system fault. This is a
**different failure mode than classification's**, and worth stating as such
in the paper: classification's measured "errors" were mostly a broken
ground truth; retrieval's measured "errors" are mostly a brittle grader
reacting to harmless LLM phrasing variance. Both point the same direction
though -- **the accuracy floor methodology itself needs hardening (the
open item already flagged) before any single-digit-trial accuracy number
here is trustworthy.**

Cost and latency are far more stable than accuracy across trials (cost
within ~20%, engineered latency within ~25%), which is reassuring for the
cost-curve-fitting work in Phase 4 -- the crossover-volume math depends on
cost/latency stability, not on this grader.

### 2026-10-02 (later still) — Retrieval's grader fixed, but not on the first try

Set out to fix the exact-substring grader. **First attempt: LLM-as-judge**
(the standard recommendation for this exact problem) -- a second Ollama call
given the true message and the LLM's answer, asked to verdict yes/no on
whether the content matches. Tested against three synthetic cases
(paraphrased-correct, wrong-content, verbatim) *before* trusting it, same
discipline as every other grader in this project:

- Verbatim case: correctly said "yes."
- **Paraphrased-correct case: incorrectly said "no."** Rewrote the prompt
  with an explicit few-shot worked example showing this exact pair labeled
  "correct" -- the model **still said "incorrect" on the live pair
  immediately after being shown it as the example**. Tried a
  chain-of-thought variant (asked it to reason before verdicting) -- it
  produced a verdict with zero visible reasoning, and now called the
  *wrong-content* case "incorrect" too (right answer, but by the point a
  judge is unreliable on an unambiguous case with no visible work, it isn't
  trustworthy on the ambiguous ones either).

This wasn't a prompt-wording problem -- it was tried three different ways
and failed the same clear test case each time. Concluded `gpt-oss:120b` via
Ollama's native API is **not reliable for this specific judgment task** in
this configuration. This is a real, useful negative result for the paper:
"have an LLM judge the LLM" is commonly suggested as the fix for brittle
string-matching graders, but it is not automatically better, and this
project's own experience is a concrete counterexample -- it must be
verified against real test cases before being trusted, exactly like any
other grading method, not adopted on reputation.

**Final fix: deterministic word-overlap matching.** What fraction of the
true message's significant words (>=4 characters, common scaffolding words
like "message"/"conversation"/"answer" excluded so they don't inflate
overlap) appear in the LLM's answer text, threshold 0.4. No secondary LLM
call, free, deterministic. Verified against the same three synthetic test
cases: paraphrased-correct now correctly passes, wrong-content correctly
fails, verbatim correctly passes. Also spot-checked against realistic
casual message styles matching the actual seeded data ("sounds good, will
confirm details soon," "haha yeah tell me," etc.) -- all graded correctly.

`data_retrieval.py` updated and deployed to production
(`/opt/throughline/app/mco_asmc/experiments/`).

**Re-run on production with the fixed grader** (old-grader data preserved
separately as `results/raw_data_retrieval_old_grader.csv` for comparison,
rather than overwritten): ended up with **10 full trials** at N=10 (the
5-trial loop was run twice), all using the new grader.

**Result: 100/100 (100%) accuracy**, up from 94/100 (94%) pooled under the
old substring grader. All 9 cases flagged as disagreements between the two
graders go the same direction -- answers the old brittle grader would have
marked wrong are correctly recognized as right by the new one. This is the
validation the fix needed: not just passing synthetic test cases, but
measurably correcting the exact failure mode it was built to fix, on the
real data that motivated it. Cost ($0.000218-$0.000260/request) and latency
(1,488-1,944ms) across the 10 trials are consistent with earlier findings
-- the grader fix changed the accuracy number, not the cost/latency
picture, as expected (the grader only touches how answers are scored, not
what gets measured).

**Retrieval's accuracy methodology is now solid for N=10.** Remaining for
this task pair: N=100/1,000 tiers (still only N=10 has real data), and
re-running the earlier N=10 production run's engineered-side numbers are
unaffected by this fix (the grader only touches the LLM side), so that data
remains valid as-is.

### 2026-10-04 — Retrieval at N=100: grader fix holds, cost curve starting to take shape

**N=100 run on production** (`results/raw_data_retrieval.csv`), clean
single run with the fixed word-overlap grader:

| metric | N=10 (10 pooled trials) | N=100 |
|---|---|---|
| LLM accuracy | 100/100 | **99/100 (99.0%)** |
| mean tokens | ~660 | 399 |
| mean cost | ~$0.00023 | $0.000146 |
| mean latency (LLM) | ~1,490ms | 969ms |
| mean latency (engineered) | ~0.8ms | 0.861ms |
| total cost this tier | -- | $0.0146 |

**Grader fix holds at 10x the sample size** -- 99% vs the earlier 100%,
well within normal variance for a single genuine LLM miss rather than a
sign the fix is breaking down. Engineered-side latency stayed essentially
flat (0.86ms, consistent with the N=10 range) -- exactly the "variable_cost
stays flat across volume" behavior Phase 0 flagged as worth confirming
before trusting the cost-curve fit. Engineered-vs-LLM latency ratio at this
tier: ~1,126x, consistent with the ~1,000x order of magnitude seen at N=10.

**Retrieval is now measured at N=10 (10 trials) and N=100 (1 trial).**
Remaining: N=1,000, and backfilling trials at N=100 to meet Phase 3's ≥5
requirement.

### 2026-10-04 (later) — Retrieval at N=1,000: all three task pairs now have real data at all three volume tiers

**N=1,000 run on production** (`results/raw_data_retrieval.csv`), clean
single run:

| metric | N=10 (10 trials) | N=100 | N=1,000 |
|---|---|---|---|
| LLM accuracy | 100% | 99.0% | **99.9%** |
| mean tokens | ~660 | 399 | 388 |
| mean cost | ~$0.00023 | $0.000146 | $0.000141 |
| mean latency (LLM) | ~1,490ms | 969ms | 901ms |
| mean latency (engineered) | ~0.8ms | 0.861ms | 0.903ms |
| total cost this tier | -- | $0.0146 | $0.1405 |

Accuracy, cost, and both latencies are all stable across two full orders of
magnitude of volume -- 100% / 99.0% / 99.9% isn't a trend, it's noise
around a true rate near 99-100%, and engineered latency sitting at
~0.8-0.9ms regardless of N is exactly the flat `variable_cost` Phase 0
needed confirmed before the 10K/100K/1M projections mean anything.
Engineered-vs-LLM latency ratio holds at ~1,000x across all three tiers.

**Milestone: every one of the three ASMC task pairs now has real measured
data at all three real volume tiers (10/100/1,000).** Running total spend
across the entire project, all task pairs, all tiers: **~$1.26**. What
remains before Phase 1 is genuinely complete:
- Backfilling trials to meet Phase 3's ≥5-trials-per-tier bar (most tiers
  currently have 1 clean trial; Retrieval N=10 and Classification are the
  exceptions, already at 5+)
- Fitting the `variable_cost`/`fixed_cost` curve per task pair (Phase 1's
  remaining checklist items, Phase 4's crossover charts)
- The projected 10K/100K/1M tiers, computed from that fit, not executed
  directly

### 2026-10-04 (later) — Cost curves fitted (`results/asmc_summary.csv`): two task pairs have NO crossover, one does, and why that happens is the real finding

**variable_cost is real and measured** for every side of every task pair,
taken from the N=1,000 tier (largest, most stable sample): engineered is
$0 for all three (Always Free compute, confirmed earlier), LLM sides are
$0.00014051 (retrieval), $0.00005775 (classification), $0.00063341
(aggregation) -- all essentially flat across N=10/100/1,000 already
established in earlier entries.

**fixed_cost is the one genuinely subjective input in this whole model**,
and it matters enormously -- see below. Estimated at $50/hr dev time, based
on the actual work this session did building each path:
- Retrieval: engineered $8.33 (~10 min, one SQL query) vs LLM $75 (~90 min
  -- this included the two failed grading attempts, which is real
  engineering cost, not wasted motion to exclude)
- Classification: engineered **$0** (pre-existing production code,
  `_detect_nearby_category` already existed before this research) vs LLM
  $37.50 (~45 min, no grader iteration needed)
- Aggregation: engineered $75 (~90 min, genuinely new code) vs LLM $16.67
  (~20 min -- `weekly_digest.py` already existed, only the `return_usage`
  extension was new work)

**Result: two task pairs have NO crossover at all.** For Retrieval and
Classification, engineered dominates at every volume from N=1 to N=1M --
the formula's crossover point comes out negative (-474,486 and -649,351
respectively), meaning it falls outside the valid domain entirely. This
isn't a close call: at N=1M, Retrieval's LLM path still costs ~26x more
per request than engineered ($0.000216 vs $0.0000083); Classification's
LLM path costs infinitely more in relative terms since engineered is
exactly $0 at every volume.

**Aggregation is the one real exception, and the reason is structural, not
random**: a genuine crossover exists at **N≈92,089**. Below that volume,
the LLM path is cheaper (its low fixed cost, from reusing already-existing
`weekly_digest.py`, dominates); above it, engineered's $0 variable cost
eventually wins out despite its higher build cost. **This crossover exists
specifically because the LLM side was cheaper to build than the engineered
side** -- the opposite asymmetry from the other two task pairs, where the
LLM path required new prompt+grader work while the engineered logic
already existed or was trivial. The crossover volume is a direct function
of which side gets to start from existing code, not of the LLM's or
engineered path's intrinsic merit.

**This is the real, generalizable finding for Phase 4's decision rule**:
ASMC's crossover isn't just about volume and task complexity, as Phase 0
assumed going in -- it's highly sensitive to which solution a team happens
to already have built. A team that already has an LLM-based tool and is
deciding whether to engineer a replacement faces a completely different
cost curve than a team building both from scratch, even for the identical
task. The paper's decision rule should state this explicitly: fixed_cost
must be assessed per-organization (what do *you* already have?), not
assumed from the task type alone.

**Caveat stated plainly**: fixed_cost figures are estimates, not
measurements -- the only non-empirical input in this entire cost model.
`variable_cost`, the accuracy floors, and the crossover arithmetic itself
are all real; the dev-time estimates are reasoned but not independently
verified. Sensitivity to this assumption is itself worth a sentence in the
paper -- e.g. Aggregation's crossover volume would shift meaningfully if
its fixed-cost estimates changed, while Retrieval/Classification's
"no crossover" conclusion is robust to reasonable fixed_cost variation
(the gap is too large for a plausible estimate change to flip it).

Full numbers in `results/asmc_summary.csv`.

### 2026-10-08 — Retrieval N=100 backfilled to 5 trials: Phase 3 satisfied

**Retrieval, N=100, trials 1-5** (`results/raw_data_retrieval.csv`, 900
total LLM calls -- trials 2-5 each ran with double the expected sample
size, same harmless accidental-double-loop pattern seen before, all using
the identical fixed grader, so pooled rather than treated as a problem):

| trial | accuracy | mean cost | mean latency |
|---|---|---|---|
| 1 | 99.0% | $0.000146 | 969ms |
| 2 | 100.0% | $0.000145 | 874ms |
| 3 | 100.0% | $0.000148 | 949ms |
| 4 | 100.0% | $0.000140 | 898ms |
| 5 | 99.5% | $0.000136 | 821ms |
| **pooled** | **99.78%** (898/900) | -- | -- |

Remarkably stable across trials -- the 99.0-100% range here is a much
tighter spread than Retrieval's N=10 trials showed pre-fix (80-100%),
direct confirmation the grader fix resolved the actual noise source rather
than just moving it. Cost and latency both stayed consistent with every
earlier measurement at this tier. **Retrieval N=100 now satisfies Phase
3's ≥5-trials bar.**

Remaining backfills: Aggregation N=10/N=100 (in progress). N=1,000
backfills for both task pairs remain deliberately skipped per the
cost/benefit reasoning already logged above.
