# Orbit evaluation baseline (2026-11-05)

## Checkout and scope

- Source: `main` at `d4dfea7ea9f95f83de44879c46be1749d9ea5517`. `origin/main` was fetched and matched local `HEAD` with zero commits ahead or behind. The worktree was clean before validation.
- This revision follows the `pico` to `orbit` namespace migration. The benchmark entry point is `benchmarks.orbitbench`; the old `picobench` command is no longer the current entry point. See [migration](orbit-migration.md).
- The checkout contains benchmark code, frozen suites, and public summaries. Historical raw run artifacts are absent. A digest in a summary is an identity reference, not an independently replayable raw record in this checkout.
- “Source fact” below means a property of this checkout. “Historical result” means a result reported by project documentation for a different source commit. “Inference” is a proposed interpretation or next step.

## Evidence matrix

| Track | Current source / metric | Recorded result and binding | Current HEAD evidence status |
| --- | --- | --- | --- |
| Runtime R0/R1 | Deterministic scheduler fate, queue wait, full Runtime Turn path; `benchmarks/orbitbench/packs/runtime/` | Historical deterministic artifact bound to `e6c790e3...`; see [OrbitBench](../../benchmarks/orbitbench/README.md) | Local smoke passed on a modified worktree (see below). It is a development check, not clean-commit claim evidence. |
| Live scheduler | 80 paired repetitions; foreground accept-to-terminal P95, request fate, marker verifier | Historical result bound to `00fd757f...`: 4,160 Turns, 13.53% P95 reduction, 4,158/4,160 marker passes; aggregate `claim_eligible=false`. See [scheduler experiments](runtime-scheduler-experiments.md). | Different commit; raw records absent. No current-HEAD claim. |
| Ship-1 Context / Memory / Tool-MCP | Frozen paired trials, retrieval cases, task verifiers, usage, token and schema cost, recall and leakage rules; `agent_application_ship_1.yaml` | Historical result bound to `e6c790e3...`: 216 E2E Trials and 260 Retrieval Cases; `ship_complete=true`, `measurement_valid=false`, `positive_claim_eligible=false`. Context had 23/24 valid Pairs; Tool/MCP treatment passed 20/24 versus control 23/24. See [OrbitBench](../../benchmarks/orbitbench/README.md). | No raw artifacts or current-HEAD campaign result. The current scorecard suite covers Context and Tool/MCP; no external Memory backend experiment is bundled. |
| CallEfficiency | DeepSeek prefix stability, cache hit rate and estimated cost per verified success | Historical report: 36 valid pairs, 72 Trials, 504 Provider calls; estimated cost per verified success fell from $0.008356 to $0.002311; report says all Claim Gates passed. See [cost experiment](tokenwise-cost.md). | External raw report absent. The result is a frozen workload and price estimate, not an invoice or current-HEAD measurement. |
| Tracing | Paired local Turns, P50/P95, trace correlation and bytes per Turn | Historical report: 1,000 valid pairs, 100% correlation; P95 2.912292 ms off vs 5.157333 ms on. Relative-overhead interval crosses zero. See [tracing overhead](tracing-overhead.md). | Raw manifest and receipts absent; no current-HEAD measurement. |
| PinchBench / ClawBench / AppWorld | Task cards and external benchmark adapters under `benchmarks/` | No committed local results. Some PinchBench cards deliberately probe removed capabilities; see [benchmark index](../../benchmarks/README.md). | Harness presence alone is not a capability result. |

`measurement_valid`, `positive_claim_eligible`, and a diagnostic score have distinct meanings. A completed campaign can have an invalid aggregate measurement; a diagnostic score is not a certified score. The [scorecard](../../benchmarks/orbitbench/README.md) requires compatible evidence and preregistration for certification.

## Local validation on this Windows checkout

The first attempts used `UV_OFFLINE=1`. After changing the Windows test dependency selection, uv downloaded locked packages to prepare the local environment. The benchmark itself used a deterministic local Provider and stdio MCP fixture; no live Provider evaluation was called.

| Command | Observed outcome |
| --- | --- |
| `make orbitbench-smoke` | Stopped before Python execution: `--all-extras` requests `boxlite==0.9.5`, which has no `win_amd64` wheel. No smoke result. |
| `make test-retained` | TUI bundle built successfully (`ui-tui/dist/entry.js`, ignored by Git); Python test phase stopped at the same BoxLite installation error. No pytest result. |
| `uv run --offline --frozen --extra dev pytest tests/test_spine_scheduler.py tests/test_cli_runtime_assembly.py -q` | Stopped before test collection because locked `litellm==1.85.0` was absent from the local uv cache. No test result. |
| `make orbitbench-smoke` after Windows compatibility edits | Passed: 2,000-request Scheduler track, 100-Turn Runtime track, local stdio MCP smoke, and report rebuild. Local runtime evidence digest: `1bff98ca4970d399b764af6c29e85f07b4a39c080826bfad4b614453b061ef0c`. The worktree was modified, so this is not a clean-commit performance claim. |
| `make test-retained` after edits | Entered pytest; interrupted after an early failure and slow progress. No full-suite result. The first failure was Windows directory `fsync` in the budget ledger, subsequently fixed. |
| Targeted OrbitBench contract, budget, retained-gate, semantic-campaign and Make target tests | 45 passed after the directory `fsync` fix (30 + 15). |
| Full retained suite, latest Windows run (`PYTHONUTF8=1 make test-retained`) | Completed collection and execution: 3,794 passed, 49 failed, 10 skipped, 23 deselected, 52 errors. `make` exited 1. Many errors come from POSIX-only sandbox socket tests using `/tmp`; failures also span symlink/chmod behavior, shell expectations, environment identity, and release-tree assumptions. This is **not** a full-suite pass. |
| First-failure and focused diagnosis | Unix-socket RPC test skipped where `socket.AF_UNIX` is unavailable; AppWorld POSIX shell/executable-bit fixture skipped on Windows. Further focused run: 3 failed, 25 passed. Failures included AppWorld Bash/worktree execution, CRLF-sensitive byte comparison, and an atomic I/O test that saw `PermissionError` from a directory path. |

The [Windows migration note](orbit-migration.md) separately reports 198 product tests and 86 TUI RPC tests passed during migration, while Context and Tool/MCP benchmark collection stopped on the Unix-only `fcntl` import. Those are documented earlier checks, not a fresh run in this baseline.

## Local validation on the VMware Ubuntu checkout

This checkout ran in Ubuntu 20.04 with Python 3.12.16, uv 0.13.7, Node 22.14.0, npm 10.9.2, and Git 2.50.1. Its Git metadata was reconstructed from the host bundle at `d4dfea7ea9f95f83de44879c46be1749d9ea5517`; the working tree also contained the uncommitted changes described here. These are development checks of that worktree, not measurements of the clean commit.

| Command | Observed outcome |
| --- | --- |
| `npm ci --prefix ui-tui` and `npm run build --prefix ui-tui` | Passed after restoring the upstream `picomatch` and `picocolors` entries in the TUI lockfile. The Orbit migration had renamed those transitive dependencies in the lockfile without changing their integrity hashes. |
| `make test-retained` with the Linux default `--all-extras` | Passed with exit code 0: 3,905 passed, 23 deselected, 13 warnings. The TUI bundle built first. The selected suite excludes the opt-in markers listed below. A prior run printed the same passing pytest count but exited 139 during interpreter shutdown; stopping active Skill watchers at runtime close and process exit removed that crash. |
| `make orbitbench-smoke` | Passed with exit code 0 using scripted local fixtures; the Runtime track exercised 100 Turns and report rebuild passed. Evidence digest: `38f01703179897027fde4d233b8f24af990f14621c89ee84edb9b1fd0025b165`. No paid Provider call was made. |

The smoke output is tied to this modified checkout even though its evidence path contains the HEAD commit ID. Its digest is a reproducibility handle for this run, not a clean-commit capability or performance claim.

## Read-only review of the existing paid campaign

The current Scorecard suite already fixes 32 calibration Trials, 96 formal Trials, the Context and Tool/MCP tasks and claim rules, and a 100 CNY hard cap. `make orbitbench-scorecard-estimate` completed without a Provider call and reported 128 Trials, 1,220 maximum request attempts, and a 56.31192 CNY worst-case estimate under the suite's frozen prices ($0.14 per million cache-miss input tokens and $0.28 per million output tokens).

Those prices and the model identity need a new baseline before paid execution. As checked on 2026-11-10, the [DeepSeek model update](https://api-docs.deepseek.com/updates/) says the suite's `deepseek-v4-flash` name is a compatibility alias routed to V4.1 Flash. The [current pricing page](https://api-docs.deepseek.com/quick_start/pricing/) lists Flash cache-miss input/output prices of $0.15/$0.60 off-peak and $0.30/$1.20 peak per million tokens. Applying the suite's frozen 7.5 USD-to-CNY multiplier to the estimate's maximum token counts yields about 63.89 CNY off-peak or 127.77 CNY peak. These are recalculations of a ceiling, not observed spend or a current provider quote; the peak projection exceeds the configured 100 CNY cap. The historical V4 Flash results cannot be treated as results for the currently routed V4.1 Flash model.

The formal and ship campaigns require a clean Git worktree in code. The current modified checkout is therefore a valid target for read-only estimate and deterministic smoke, but cannot enter the paid formal gate as-is.

A separate `agent_application_scorecard_v2.yaml` draft now preserves the v1 Trial counts and Claim Rules while selecting `deepseek-flash`, peak input/output prices of $0.30/$1.20 per million tokens, and a proposed 150 CNY hard cap. Its offline maximum is 127.7748 CNY. This is configuration preparation, not approval or a live result. The standalone Scorecard scorer can explicitly accept the v2 campaign identity; the complete reproduction workflow remains fixed to v1 and cannot compose a v2 campaign. Before any paid v2 run, check the latest model and pricing again, bind the Orbit runtime configuration to `deepseek/deepseek-flash`, use a separate evidence directory, and obtain a clean committed subject.

## Assessment and next gates

**Source fact:** The default pytest selection in `pyproject.toml` excludes `real_llm`, `llm_judge`, `real_vm`, `real_channel`, `external_runtime`, and `e2e`. Passing it would validate retained deterministic tests, not live channel or Provider behavior.

**Inference:** The Windows blockers include environment portability and POSIX-only file operations. The local smoke now passes after replacing OrbitBench's `fcntl` locks with the shared portable lock, skipping unsupported directory `fsync` on Windows, and excluding BoxLite from Windows deterministic-gate dependency selection. The Linux retained gate and smoke pass on the modified checkout; Windows full-suite and clean-commit evidence remain outstanding.

Next gates, in order:

1. Freeze the current source changes into a reviewable commit when authorized, then rerun the Linux retained gate and deterministic smoke against that clean commit. Retain command, environment identity, counts, failures, and artifact digests.
2. Review the separate v2 paid-suite draft, its current-model identity and 127.7748 CNY peak-price ceiling before approving any paid call. Preserve the historical v1 suite and results as a separate model-era record.
3. Resolve or explicitly scope the remaining Windows full-suite portability failures.
4. Rebuild any available historical raw artifacts offline against their bound commits. Do not relabel them as current-HEAD results.
5. Only after the local gates and budget review pass, run a small current-commit live calibration with its existing strict task verifiers.
