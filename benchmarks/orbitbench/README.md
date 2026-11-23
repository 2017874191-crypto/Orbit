# OrbitBench Ship-1

OrbitBench is Orbit's checkout-only evaluation campaign. It runs the real Orbit
Runtime through frozen, single-axis comparisons and rebuilds every result from
terminal artifacts. It is not part of the `orbit` wheel and does not add a
public `orbit bench` command.

## Entry points

Run the credential-free gate first:

```bash
make orbitbench-smoke
```

It executes the 2,000-request Scheduler track, the 100-Turn full Runtime track,
the local stdio MCP transport smoke, and an artifact-only report rebuild. It
does not resolve a Provider or make a paid model call.

Run the frozen calibration and formal campaign with:

```bash
make orbitbench
```

The paid command uses the normal Orbit configuration, but only proceeds when it
resolves exactly `deepseek / deepseek-v4-flash`, proves Tool Calling and
complete usage fields with one live preflight, and freezes the tokenizer
identity. Fallback models are forbidden.

## Current Scorecard campaign

### Local evidence recording

Runtime Context Trials and deterministic/real-provider Tool/MCP Trials retain
per-Trial bundles under `<output-root>/.orbitbench-call-evidence/<trial-digest>/`.
The digest binds the experiment, plan, Trial key and block attempt; two arms
and block retries do not overwrite each other. Trial execution artifacts link
the bundle and its integrity digest. Calls include auxiliary Context calls,
model retries and budget reservation IDs when available. The MCP stdio server
does not call the model: its receipt file is copied after shutdown.

Each bundle contains requested parameters, normalized responses and terminal
records, observed final reply/events, final Context artifact or MCP receipt
bytes, and verification findings. Artifact bytes are base64 encoded inside a
JSON record with a SHA-256 checksum; invalid JSON outputs remain inspectable.
Missing or out-of-workspace artifacts are explicitly marked. No expected
answers are copied into the Agent workspace by the recorder.

Verify the bundle and export failed calls without contacting a Provider:

```bash
uv run --frozen python -m benchmarks.orbitbench.call_evidence /absolute/path/to/bundle
```

Recording is non-streaming and in-process. It provides diagnostic evidence,
not OS-level protection against an Agent with arbitrary same-user execution.
MCP receipts here prove fixture calls, not changes in a real business database.
Offline false-success regressions test the rule verifier; they do not establish
LLM Judge robustness or runtime authorization enforcement. Evidence is private
by default in usage: it contains task text and outputs and must be reviewed
before publication. The collector does not configure filesystem ACLs.
Evidence persistence/integrity failures abort the harness instead of entering
comparison-block retries. This prevents further model dispatch under that
evaluation after its evidence channel fails. Ordinary task failures retain
their existing scoring and retry rules.

### Offline review report and human audit queue

The offline report requires an explicit planned-Trial list, rather than using
only the files that happen to exist. Freeze the list before a real evaluation;
an omitted task cannot be discovered from its absent evidence. The input is a
review plan, not proof of preregistration or source authenticity:

```json
{
  "schema": "orbit.offline-review-plan.v1",
  "data_kind": "recorded",
  "audit_rate": 0.2,
  "audit_seed": 42,
  "budget_ledger": "provider-budget.jsonl",
  "trials": [
    {
      "trial_id": "context-task-1-control-r0",
      "task_id": "task-1",
      "arm": "context-fifo",
      "message_id": "identity-from-trial-record",
      "attempt_bundles": [".orbitbench-call-evidence/trial-digest"]
    }
  ]
}
```

Paths are relative to the plan. Each entry is one planned task/arm/repetition.
List all block attempts in their predeclared order. A block with its own message
identity can use `{"path": "bundle-path", "message_id": "that-block-id"}`.
An empty list or nonexistent path stays in the denominator as missing evidence.
The first attempt determines success; later attempts cannot replace a failure.
All declared attempts contribute to cost. This descriptive policy is separate
from the historical campaign's block-selection policy. Do not combine packs,
subjects or distributions into a headline score; use separate review plans.

```bash
uv run --frozen python -m benchmarks.orbitbench.evidence_report \
  --plan /absolute/path/to/review-plan.json --output-root /absolute/new/report-dir
```

Outputs are `report.json`, `REPORT.md`, and `audit-queue.json`. The report checks
bundle hashes, preserves unknown usage/cost/duration as missing, distinguishes
reported truncation from reaching the token ceiling, and separates all anomaly
reviews from seeded random-normal reviews. The target normal sample fraction
is 10–20% per arm, rounded up; actual inclusion probabilities are retained.
Reviewers fill in reviewer/verdict/notes separately. Queue creation does not
mean human review has happened, and the report never certifies quality parity.

Costs come from the supplied ledger's rate estimates and conservative failed
reservations, not invoices. An open or unmatched reservation leaves arm cost
unknown. Unassigned ledger entries such as preflight requests are listed
separately and included only in the entire-ledger summary. Duration quantiles
cover available first-attempt end-to-end measurements, with coverage counts;
they are not a causal latency comparison or timeout-adjusted estimate.

Create a fully synthetic example without calling a model (both paths must be new):

```bash
uv run --frozen python -m benchmarks.orbitbench.evidence_report \
  --demo /absolute/new/demo-evidence --output-root /absolute/new/demo-report
```

For an actual local Runtime-to-report acceptance run, use a new directory:

```bash
uv run --frozen python -m benchmarks.orbitbench.offline_acceptance \
  --output-root /absolute/new/offline-acceptance
```

This freezes review plans before dispatch and executes eight Trials through
Context read/write Tools and real local stdio MCP: one positive and one
deliberately invalid response per arm. The Context scripted Provider explicitly
reads public expected answers; it is oracle-assisted infrastructure validation,
not evidence of model reasoning or generalization. The MCP negative Provider
only claims success and produces no receipt. Acceptance requires all four
positive Trials to pass, all four negative Trials to fail, and report statuses
to match the executed Trials with no unverified bundles.

The command writes `acceptance-result.json` and separate Context and Tool/MCP
reports/audit queues. Reports label `execution_mode` as
`scripted_runtime_oracle_assisted`. They never certify capability or mark human
review complete. Costs remain unknown in the report because no paid Provider
ledger exists; this run issues no external model requests. These cases are
public development fixtures, not private holdouts.

Machine assistance for the acceptance audit is available without rerunning it:

```bash
uv run --frozen python -m benchmarks.orbitbench.review_acceptance \
  /absolute/path/to/offline-acceptance --output /absolute/new/machine-review.json
```

It checks the frozen review-plan/report digests and bundle integrity, decodes
artifact bytes, and rechecks them against the current public fixture. Its
findings are automated rule rechecks, not human judgments. Human reviewers
still fill the original audit queues, and `human_review_completed` remains false.

The lightweight generalization proposal is
`suites/generalization_pilot_v1.protocol.json`. It proposes 24 task cases / 48
Trials across Context and MCP, split into three stress strata with matched
counterfactuals. These are exploratory sample sizes, not proof of quality
noninferiority. This is a proposal, not an executable campaign suite: no private
task loader or holdout access control is implemented by the JSON file. Existing
public tasks and developer-visible mutations stay in the development split;
fresh private task families must be authored and retained by an independent
custodian with separate storage/permissions. Do not generate a supposed secret
holdout inside this development workspace or reuse an exposed final batch.

The current Scorecard campaign runs the Context and Tool/MCP Packs. CallEfficiency
and Runtime remain independent evidence tracks and are composed only after their
own Claim Gates are evaluated. No external Memory backend experiment is included
in this release.

Run or resume the complete multidimensional workflow with:

```bash
ORBIT_BENCH_EXECUTE_PAID=1 make orbitbench-reproduce
```

The command performs a full preflight before any paid call, then runs Runtime,
TokenWise, and the Context plus Tool/MCP Scorecard tracks. It
composes the multidimensional score, renders a terminal table, and writes
`report.json`, `REPORT.md`, `score.json`, and stage logs under
`.orbit/evidence/orbitbench-reproduction/<orbit-commit>/`. Repeating the command
validates and resumes retained stage artifacts instead of rerunning completed
work.

Existing current-commit evidence can be reused without paid execution:

```bash
ORBIT_SCORECARD_FORMAL_SUMMARY=/absolute/path/to/summary.json \
ORBIT_SCORECARD_RUNTIME_EVIDENCE=/absolute/path/to/runtime-evidence.json \
ORBIT_SCORECARD_TOKENWISE_REPORT=/absolute/path/to/tokenwise-report.json \
  make orbitbench-reproduce
```

The runner copies these compact inputs into its output directory and verifies
their commit identities and digests before composing the score. The formal
summary must remain beside the `manifest.json` from the same experiment so its
Scorecard identity can be rebuilt. A diagnostic score is always labeled
separately from a certified score, and the terminal and Markdown reports list
every failed certification check in plain language.

Print the frozen worst-case budget with:

```bash
make orbitbench-scorecard-estimate
```

The historical v1 suite remains the default. A separate v2 draft uses the
currently documented `deepseek-flash` name and peak prices. Review its offline
maximum before considering any paid run:

```bash
uv run --frozen python -m benchmarks.orbitbench.scorecard_campaign estimate \
  --suite benchmarks/orbitbench/suites/agent_application_scorecard_v2.yaml
```

At the 2026-11-10 pricing snapshot, the unchanged 128-Trial design projects
127.7748 CNY at the suite's 7.5 USD-to-CNY multiplier. The draft's 150 CNY
hard cap is a proposed ceiling, not spending approval. The estimate assumes
all input tokens are cache misses and uses peak prices. Recheck the provider's
model and price before execution. A v2 paid run requires explicit `--suite`
and a separate `--output-root`; the configured Orbit model must also resolve
to `deepseek/deepseek-flash`. Do not combine v1 and v2 evidence.
The standalone scorer can read a v2 formal summary when passed
`--campaign-suite agent-application-scorecard-v2`; it still applies the frozen
v1 scoring formula and requires the usual current-commit evidence. The full
`orbitbench-reproduce` workflow remains pinned to v1, including its Runtime
and TokenWise stages, and must not be used to launch the v2 draft.

Run the paid campaign with:

```bash
make orbitbench-scorecard-ship
```

Runtime evidence is not required to measure the independent Context and
Tool/MCP Packs. When `ORBIT_SCORECARD_RUNTIME_EVIDENCE` is supplied, the
campaign verifies that its Orbit product code, dependency identity, and Runtime
Pack are byte-equivalent before making a paid call. It never silently reruns
the Runtime experiment.

The v1 multidimensional score uses Capability 50, Reliability 20, Efficiency
20, and Process 10. Capability averages current treatment rates for Context,
Tool/MCP, and Memory. Context capability is the average of four frozen checks:
the early constraint is still present, the artifact applies it, the latest
decision is applied, and the artifact is exactly correct. The strict external
verifier pass rate is still reported separately and is not relaxed. Context
reserves 500 output tokens and protects its first constraint turn, leaving
enough of the 2,400-token window for the latest decision. Empty Provider
responses may be retried up to four times, symmetrically in both arms.
Context capability validity is independent from token-usage completeness.
Missing usage invalidates only the Context efficiency claim; it is never
coerced to zero and never blocks verifier-backed capability diagnostics.
Efficiency assigns five points to each eligible TokenWise, Context, Tool/MCP,
and Turn-efficiency claim. Missing, ineligible, or commit-incompatible evidence
contributes zero. Process checks MCP disclosure, transport, invalid-target,
and exact-repeat gates. Safety and full evidence coverage are certification
gates, not bonus points.

Compute the score from immutable formal and Runtime artifacts with:

```bash
ORBIT_SCORECARD_FORMAL_SUMMARY=/absolute/path/to/summary.json \
ORBIT_SCORECARD_RUNTIME_EVIDENCE=/absolute/path/to/runtime-evidence.json \
ORBIT_SCORECARD_TOKENWISE_REPORT=/absolute/path/to/tokenwise-report.json \
ORBIT_SCORECARD_MEMORY_SUMMARY=/absolute/path/to/memory-summary.json \
ORBIT_SCORECARD_MEMORY_HANDOFF=/absolute/path/to/memory-handoff.json \
ORBIT_SCORECARD_PREREGISTERED=1 \
  make orbitbench-scorecard-score
```

Runtime, Memory, and preregistration inputs are optional. The Memory summary
is accepted only with a digest-bound handoff that names the current Orbit
commit. The scorer always emits a diagnostic score and assigns zero to missing
dimensions. It emits a certified score only when the scoring specification was
preregistered and every evidence and safety gate is complete.

## Frozen scale and budget

The suite at
`benchmarks/orbitbench/suites/agent_application_ship_1.yaml` freezes:

- calibration: 64 E2E Trials and 34 Retrieval Cases;
- formal: 216 E2E Trials and 260 Retrieval Cases;
- at most two Provider attempts and two whole-block attempts;
- a warning threshold of 80 CNY and a hard ceiling of 100 CNY.

The frozen Provider budget is pack-specific:

- Context Trials allow at most eight logical calls with 42,000 input tokens
  and 1,200 output tokens per call. The benchmark-only Curator is capped at
  four steps and the main Agent Loop at four calls.
- Memory / Skill Trials allow at most four logical calls with 15,000 input
  tokens and 1,500 output tokens per call.
- Tool / MCP Trials allow at most four logical calls with 40,000 input tokens
  and 1,500 output tokens per call.

Pricing all DeepSeek input at the cache-miss rate, using the frozen USD-to-CNY
multiplier, and reserving 5 CNY for external services gives a 62.86592 CNY
fresh-campaign worst case for the current suite.

Before any paid call, OrbitBench includes existing ledger spend and two live
preflight attempts in its projection. It freezes the current request count and
the newly authorized attempt count in a digest-bound approval record. Resumed
runs reuse that lifetime ceiling rather than opening a new budget. Every
request reserves its pack-specific maximum before dispatch and settles from
complete Provider usage; incomplete accounting is an infrastructure failure,
not permission to continue spending.

## Formal result

The final-history Ship-1 campaign ran from clean source commit
`e6c790e37d707f74c44896dbcba9de9ee4ad8327` with
`deepseek / deepseek-v4-flash`. The Provider did not expose a seed, so the
matrix records three repetitions rather than three seeds.

The formal experiment id is
`abffb7d2fe6a76f1102741cacd3cff1ed02697be0fb37fe2fa7a910dbeb11b4d`:

- `ship_complete=true`, but `measurement_valid=false`;
- all 216 planned E2E Trials and all 260 Retrieval Cases have terminal records;
- selected E2E outcomes are 82 passed, 72 task failures, 61 task timeouts,
  and one infrastructure failure;
- 119 of 120 Pairs are valid. One Context comparison exhausted its symmetric
  retry after incomplete treatment usage evidence;
- repeated raw-artifact rebuilds produce report digest
  `25ca985d2f80560fba789d14fc76acc07d0a45ea3b6a0b4aa7a3d4cfadf19eb1`;
- the cumulative main-campaign ledger recorded a 25.26335175 CNY Provider
  high-water mark, or 30.26335175 CNY committed including the fixed 5 CNY
  external-service reserve. This is a budget-control estimate, not a Provider
  invoice.

Across the final-history main campaign, semantic v1 replay, and semantic v2
campaign, the three Provider high-water marks are 25.26335175, 0.13745664,
and 0.21468672 CNY, totaling 25.61549511 CNY with zero open reservations.
This cross-ledger total is also a budget-control estimate, not a Provider
invoice.

Global `positive_claim_eligible` is false and `cv-metrics.json` exports no
metrics. Context has only 23 valid Pairs, so the declared pack invalidates the
aggregate measurement. Tool/MCP remains independently ineligible even though
progressive disclosure reduced equal-task macro estimated visible Tool Schema
tokens by 93.4513% across six measurable tasks: treatment passed 20 of 24
Trials versus 23 of 24 control Trials, including one task that lost at least
two of three passes. Semantic Memory end-to-end produced zero passes in both
arms and no verifier gain. These retained failures are product findings, not
positive resume claims.

## Evidence boundary

Calibration and formal task and query IDs are disjoint. Claim Rules are hashed
before the live preflight. Every planned Trial and Retrieval Case must have one
terminal record, and the report is rebuilt only from those records. Provider,
infrastructure, timeout, cancellation, and task failures remain in the
artifacts.

`ship_complete` means the campaign and evidence chain completed.
`measurement_valid` means the retained measurements are interpretable.
`positive_claim_eligible` is reported globally and per capability; only metrics
that pass every rule in their capability group enter `cv-metrics.json`. A
valid negative result completes Ship-1 without becoming a positive resume
claim.

Deterministic R0 and R1 Runtime metrics are exported separately from a clean
source commit into an immutable, self-digested Runtime evidence artifact. The
current artifact is bound to source commit
`e6c790e37d707f74c44896dbcba9de9ee4ad8327` with evidence digest
`1c9fc1c4882ff09e3cf44140d84206bfb5ce923344cf7e482615a36e4f0f6006`.
Those metrics may be quoted only with their explicit Scheduler or full-path
deterministic scope; they are not live throughput, task-effect, or production
SLO claims.

Raw Trials, traces, Memory content, receipts, and generated reports stay under
`.orbit/evidence/orbitbench/` and are not committed.
