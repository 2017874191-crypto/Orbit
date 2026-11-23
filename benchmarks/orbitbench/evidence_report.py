"""Descriptive offline reports from explicitly enumerated local evidence bundles."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from .call_evidence import CallEvidence, verify_bundle
from .canonical import canonical_digest


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _ledger(path):
    """Read estimated charges only; no claim of invoice-level billing verification."""
    if path is None:
        return {}
    events = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    reservations, terminals = {}, {}
    for event in events:
        if event.get("schema") != "orbit.orbitbench.provider-budget.v1":
            raise ValueError("unsupported budget ledger schema")
        key = event["request_id"]
        kind = event["kind"]
        if kind == "reserved":
            if key in reservations:
                raise ValueError("duplicate reservation")
            reservations[key] = event
        elif kind in {"settled", "failed"}:
            if key not in reservations or key in terminals or not _number(event.get("charged_cny")):
                raise ValueError("invalid budget terminal event")
            terminals[key] = event
        else:
            raise ValueError("unsupported budget ledger event")
    return {key: terminals.get(key, {"kind": "open", "charged_cny": None}) for key in reservations}


def _resolve(plan_path, value):
    path = Path(value)
    return (plan_path.parent / path).resolve() if not path.is_absolute() else path.resolve()


def _load_trial(plan_path, item, charges, used_bundles, used_reservations):
    row = {
        "trial_id": item["trial_id"],
        "arm": item["arm"],
        "task_id": item["task_id"],
        "message_id": item["message_id"],
        "success": False,
        "status": "missing_evidence",
        "reasons": [],
        "attempts": [],
        "evidence_paths": [],
        "token_usage_complete": False,
        "cost_complete": False,
        "accounted_cny": None,
        "known_cost_subtotal_cny": 0.0,
        "cost_sources": [],
        "provider_duration_ms": None,
        "end_to_end_latency_ms": None,
    }
    bundles = item.get("attempt_bundles", [])
    if not bundles:
        row["reasons"].append("missing_evidence")
        return row
    usage_complete, duration_complete, costs_complete = True, True, True
    total_duration = 0.0
    seen_calls = 0
    for block_index, relative in enumerate(bundles):
        expected_message = (
            relative.get("message_id", item["message_id"]) if isinstance(relative, dict) else item["message_id"]
        )
        root = _resolve(plan_path, relative["path"] if isinstance(relative, dict) else relative)
        if root in used_bundles:
            raise ValueError("an evidence bundle cannot count toward multiple planned trials")
        used_bundles.add(root)
        row["evidence_paths"].append(str(root))
        try:
            valid = verify_bundle(root)["passed"]
        except (OSError, ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            row["reasons"].append("invalid_bundle")
            costs_complete = usage_complete = duration_complete = False
            continue
        trials = [read(p) for p in (root / "trials").glob("*.json")]
        matches = [t for t in trials if t.get("arm") == item["arm"] and t.get("message_id") == expected_message]
        if len(matches) != 1:
            row["reasons"].append("trial_identity_mismatch")
            costs_complete = usage_complete = duration_complete = False
            continue
        trial = matches[0]
        execution = trial.get("execution", {})
        if block_index == 0:
            latency = execution.get("metrics", {}).get("end_to_end_latency_ms")
            row["end_to_end_latency_ms"] = latency if _number(latency) else None
            if "execution" in trial:
                row["status"] = execution.get("status", "unknown")
                row["success"] = (
                    row["status"] == "passed" and execution.get("verification", {}).get("state") == "passed"
                )
            else:
                row["success"] = not trial.get("verifier_failures") and trial.get("outcome") is not None
                row["status"] = "passed" if row["success"] else "task_failed"
        row["reasons"].extend(trial.get("verifier_failures", []))
        for path in sorted((root / "calls").glob("*/request.json")):
            request = read(path)
            identity = request.get("identity", {})
            # Shared live-scheduler bundles can contain many different Turns.
            if identity.get("arm") != item["arm"] or identity.get("message_id") != expected_message:
                continue
            seen_calls += 1
            response = read(path.with_name("response.json")) if path.with_name("response.json").exists() else {}
            terminal = read(path.with_name("terminal.json"))
            adapter = (
                read(path.with_name("adapter_request.json")) if path.with_name("adapter_request.json").exists() else {}
            )
            usage = response.get("usage") or {}
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
            known_usage = _number(input_tokens) and _number(output_tokens)
            usage_complete = usage_complete and known_usage
            duration = terminal.get("duration_ms")
            if _number(duration):
                total_duration += duration
            else:
                duration_complete = False
            limit = adapter.get("max_tokens", request["requested_parameters"].get("max_tokens"))
            finish_reason = response.get("finish_reason")
            sdk_path = path.with_name("sdk_response.json")
            sdk_reasons = (
                [choice.get("finish_reason") for choice in read(sdk_path).get("choices", [])]
                if sdk_path.exists()
                else []
            )
            flags = []
            if any(reason in {"length", "max_tokens", "max_output_tokens"} for reason in [finish_reason, *sdk_reasons]):
                flags.append("truncation_reported")
            elif _number(output_tokens) and _number(limit) and output_tokens >= limit:
                flags.append("token_limit_suspected")
            if terminal.get("status") in {"cancelled", "exception", "provider_error"}:
                flags.append(terminal["status"])
            if not known_usage:
                flags.append("usage_missing")
            reservation_path = path.with_name("budget_reservation.json")
            reservation = read(reservation_path).get("budget_request_id") if reservation_path.exists() else None
            charge = charges.get(reservation)
            if reservation is not None:
                if reservation in used_reservations:
                    raise ValueError("budget reservation counted twice")
                used_reservations.add(reservation)
            if charge and charge["kind"] != "open":
                row["known_cost_subtotal_cny"] += charge["charged_cny"]
                row["cost_sources"].append(
                    "settled_estimate" if charge["kind"] == "settled" else "failed_reservation_upper_bound"
                )
                if charge.get("policy_violations"):
                    flags.append("budget_policy_violation")
            else:
                costs_complete = False
                if charge and charge["kind"] == "open":
                    flags.append("budget_reservation_open")
            row["reasons"].extend(flags)
            row["attempts"].append(
                {
                    "attempt_id": request["attempt_id"],
                    "block_index": block_index,
                    "logical_call_id": request.get("logical_call_id"),
                    "finish_reason": finish_reason,
                    "input_tokens": input_tokens,
                    "sdk_finish_reasons": sdk_reasons,
                    "output_tokens": output_tokens,
                    "output_limit": limit,
                    "flags": flags,
                    "response_path": str(path.with_name("response.json")),
                    "budget_request_id": reservation,
                }
            )
    if not seen_calls:
        usage_complete = duration_complete = costs_complete = False
        row["reasons"].append("no_call_evidence")
    if "invalid_bundle" in row["reasons"] or "trial_identity_mismatch" in row["reasons"]:
        row["success"] = False
        row["status"] = "unverified"
    if row["status"] != "passed":
        row["reasons"].append(row["status"])
    if not costs_complete:
        row["reasons"].append("cost_unknown")
    row["token_usage_complete"] = usage_complete
    row["cost_complete"] = costs_complete
    row["accounted_cny"] = row["known_cost_subtotal_cny"] if costs_complete else None
    row["provider_duration_ms"] = total_duration if duration_complete else None
    row["reasons"] = sorted(set(row["reasons"]))
    return row


def build_report(plan_path: Path):
    plan_path = plan_path.resolve()
    plan = read(plan_path)
    if plan.get("schema") != "orbit.offline-review-plan.v1" or plan.get("data_kind") not in {"simulated", "recorded"}:
        raise ValueError("explicit schema and data_kind are required")
    rate = plan.get("audit_rate", 0.2)
    if not _number(rate) or not 0.1 <= rate <= 0.2:
        raise ValueError("audit_rate must be between 0.1 and 0.2")
    if not isinstance(plan.get("trials"), list) or not plan["trials"]:
        raise ValueError("a nonempty predeclared trial list is required")
    for item in plan["trials"]:
        if not all(
            isinstance(item.get(key), str) and item[key] for key in ("trial_id", "task_id", "arm", "message_id")
        ):
            raise ValueError("trial identities must be nonempty strings")
        if not isinstance(item.get("attempt_bundles", []), list):
            raise ValueError("attempt_bundles must be an ordered list")
    ids = [t["trial_id"] for t in plan["trials"]]
    identities = [(t["arm"], t["message_id"]) for t in plan["trials"]]
    if len(ids) != len(set(ids)) or len(identities) != len(set(identities)):
        raise ValueError("duplicate planned trial")
    charges = _ledger(_resolve(plan_path, plan["budget_ledger"]) if plan.get("budget_ledger") else None)
    rows, used_reservations = [], set()
    # A shared live bundle is allowed across distinct Turns, never across the same identity.
    for trial in plan["trials"]:
        rows.append(_load_trial(plan_path, trial, charges, set(), used_reservations))
    groups = defaultdict(list)
    for row in rows:
        groups[row["arm"]].append(row)
    arms, audit = {}, []
    for arm, group in sorted(groups.items()):
        successes = sum(r["success"] for r in group)
        cost = sum(r["accounted_cny"] for r in group) if all(r["cost_complete"] for r in group) else None
        logical = defaultdict(int)
        for row in group:
            for attempt in row["attempts"]:
                if attempt["logical_call_id"]:
                    logical[(row["trial_id"], attempt["block_index"], attempt["logical_call_id"])] += 1
        arms[arm] = {
            "planned": len(group),
            "strict_successes": successes,
            "success_rate": successes / len(group),
            "missing_or_unverified": sum(r["status"] in {"missing_evidence", "unverified"} for r in group),
            "attempts": sum(len(r["attempts"]) for r in group),
            "additional_attempts_within_logical_calls": sum(max(n - 1, 0) for n in logical.values()),
            "truncation_reported_trials": sum(
                any("truncation_reported" in a["flags"] for a in r["attempts"]) for r in group
            ),
            "token_limit_suspected_trials": sum(
                any("token_limit_suspected" in a["flags"] for a in r["attempts"]) for r in group
            ),
            "accounted_cny": cost,
            "cost_per_strict_success_cny": cost / successes if cost is not None and successes else None,
            "known_cost_subtotal_cny": sum(r["known_cost_subtotal_cny"] for r in group),
            "status_counts": dict(Counter(r["status"] for r in group)),
            "usage_incomplete_trials": sum(not r["token_usage_complete"] for r in group),
        }
        latencies = sorted(r["end_to_end_latency_ms"] for r in group if r["end_to_end_latency_ms"] is not None)
        arms[arm]["latency"] = {
            "available": len(latencies),
            "planned": len(group),
            "p50_ms": median(latencies) if latencies else None,
            "p95_nearest_rank_ms": latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None,
            "scope": "first_attempt_available_end_to_end_latencies_only",
        }
        normal = [r for r in group if not r["reasons"]]
        normal.sort(key=lambda r: hashlib.sha256(f"{plan.get('audit_seed', 42)}:{r['trial_id']}".encode()).hexdigest())
        count = math.ceil(rate * len(normal))
        for row in group:
            if row["reasons"]:
                audit.append(
                    {
                        "trial_id": row["trial_id"],
                        "arm": arm,
                        "selection": "all_anomalies",
                        "inclusion_probability": 1.0,
                        "reasons": row["reasons"],
                    }
                )
        for row in normal[:count]:
            audit.append(
                {
                    "trial_id": row["trial_id"],
                    "arm": arm,
                    "selection": "seeded_random_normal",
                    "inclusion_probability": count / len(normal),
                    "reasons": [],
                }
            )
    by_id = {row["trial_id"]: row for row in rows}
    for entry in audit:
        entry.update(
            {
                "evidence_paths": by_id[entry["trial_id"]]["evidence_paths"],
                "reviewer": None,
                "verdict": None,
                "notes": "",
            }
        )
    payload = {
        "schema": "orbit.offline-evidence-report.v1",
        "data_kind": plan["data_kind"],
        "execution_mode": plan.get("execution_mode", "unspecified"),
        "plan_digest": canonical_digest(plan),
        "scope": "descriptive_first_predeclared_attempt_success_all_attempt_cost",
        "quality_preservation_proven": False,
        "human_review_completed": False,
        "success_definition": "first_attempt_passes_recorded_task_verifier_not_a_general_quality_guarantee",
        "cost_basis": "ledger_rate_estimates_plus_failed_reservation_bounds_not_invoice",
        "arms": arms,
        "trials": rows,
        "audit_queue": audit,
        "unassigned_budget_request_ids": sorted(set(charges) - used_reservations),
        "budget_ledger_summary": {
            "provided": bool(plan.get("budget_ledger")),
            "open_reservations": sum(event["kind"] == "open" for event in charges.values()),
            "accounted_cny": sum(event["charged_cny"] for event in charges.values())
            if plan.get("budget_ledger") and all(_number(event["charged_cny"]) for event in charges.values())
            else None,
            "scope": "entire_supplied_ledger_including_unassigned_requests",
        },
    }
    return {**payload, "report_digest": canonical_digest(payload)}


def _cell(value):
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ").replace("[", "\\[").replace("]", "\\]")


def render_markdown(report):
    lines = [
        "# Offline Agent evidence report",
        "",
        f"Data: **{report['data_kind'].upper()}**",
        f"Execution mode: **{_cell(report.get('execution_mode', 'unspecified'))}**",
        "",
        "Descriptive review only. Quality preservation and human review are not established.",
        "Success means passing the recorded task verifier, which may only check a marker in scheduler tasks.",
        "",
        "Success uses the first predeclared block attempt; costs include all declared block attempts.",
        "Missing evidence stays in the planned denominator. Unknown cost is not zero.",
        "",
        "| Arm | Planned | Strict successes | Success rate | Unverified | Truncated | Accounted CNY | CNY/success |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm, row in report["arms"].items():
        cost = row["accounted_cny"]
        unit = row["cost_per_strict_success_cny"]
        lines.append(
            f"| {_cell(arm)} | {row['planned']} | {row['strict_successes']} | {row['success_rate']:.1%} | "
            f"{row['missing_or_unverified']} | {row['truncation_reported_trials']} | "
            f"{cost if cost is not None else 'unknown'} | {unit if unit is not None else 'unknown'} |"
        )
    lines += [
        "",
        "Costs are ledger rate estimates; failed reservations are conservative bounds, not invoices.",
        "",
        f"Unassigned budget entries (excluded from arm costs): {len(report['unassigned_budget_request_ids'])}.",
        f"Open budget reservations: {report['budget_ledger_summary']['open_reservations']}.",
        "",
        "| Arm | Status counts | Usage incomplete | End-to-end latency coverage | P50 ms | P95 ms |",
        "| --- | --- | ---: | --- | ---: | ---: |",
    ]
    for arm, row in report["arms"].items():
        latency = row["latency"]
        lines.append(
            f"| {_cell(arm)} | {_cell(row['status_counts'])} | {row['usage_incomplete_trials']} | "
            f"{latency['available']}/{latency['planned']} | "
            f"{latency['p50_ms'] if latency['p50_ms'] is not None else 'unknown'} | "
            f"{latency['p95_nearest_rank_ms'] if latency['p95_nearest_rank_ms'] is not None else 'unknown'} |"
        )
    lines += [
        "",
        "Latency quantiles describe available measurements only; missing durations and timeouts are not zero.",
        "",
        "## Trial evidence",
        "",
    ]
    for row in report["trials"]:
        lines.append(
            f"- **{_cell(row['trial_id'])}** ({_cell(row['arm'])}): {_cell(row['status'])}; "
            f"reasons: {_cell(', '.join(row['reasons']) or 'none')}"
        )
        for root in row["evidence_paths"]:
            lines.append(f"  - [Evidence bundle](<{Path(root).as_posix()}>)")
        for attempt in row["attempts"]:
            lines.append(
                f"  - [Response](<{Path(attempt['response_path']).as_posix()}>): "
                f"{_cell(attempt['finish_reason'])}, output={attempt['output_tokens']}, limit={attempt['output_limit']}"
            )
    lines += ["", "## Human review queue", "", "Random-normal and anomaly samples must be analyzed separately.", ""]
    for item in report["audit_queue"]:
        lines.append(
            f"- [ ] {_cell(item['trial_id'])}: {item['selection']}; "
            "check factual correctness, required constraints, real tool evidence, and failure attribution"
        )
    return "\n".join(lines) + "\n"


def write_report(plan_path: Path, output_root: Path):
    # No overwrites, and output must not contaminate the hashed source bundles.
    report = build_report(plan_path)
    output_root = output_root.resolve()
    for row in report["trials"]:
        if any(output_root.is_relative_to(Path(root)) for root in row["evidence_paths"]):
            raise ValueError("report output must be outside evidence bundles")
    output_root.mkdir(parents=True, exist_ok=False)
    for name, payload in (("report.json", report), ("audit-queue.json", report["audit_queue"])):
        (output_root / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "REPORT.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def create_demo(root: Path):
    root.mkdir(parents=True, exist_ok=False)
    trials, ledger = [], []
    for arm in ("control", "treatment"):
        for index in range(3):
            key = f"{arm}-{index}"
            bundle = root / key
            item = {
                "trial_id": key,
                "task_id": f"task-{index}",
                "arm": arm,
                "message_id": key,
                "attempt_bundles": [key],
            }
            trials.append(item)
            if arm == "treatment" and index == 2:
                continue  # Deliberately missing evidence, still part of the denominator.
            collector = CallEvidence(bundle)
            failed = index == 1
            retried = arm == "control" and index == 1
            for attempt in range(2 if retried else 1):
                aid = f"{key}-attempt-{attempt}"
                collector.attempts.append(aid)
                collector.write(
                    f"calls/{aid}/request.json",
                    {
                        "attempt_id": aid,
                        "logical_call_id": key,
                        "identity": {"arm": arm, "message_id": key},
                        "requested_parameters": {
                            "max_tokens": 512,
                            "messages": [{"role": "user", "content": "SIMULATED TASK"}],
                        },
                    },
                )
                collector.write(
                    f"calls/{aid}/response.json",
                    {
                        "content": "SIMULATED RESPONSE",
                        "finish_reason": "length" if failed else "stop",
                        "usage": {"prompt_tokens": 100, "completion_tokens": 512 if failed else 10},
                    },
                )
                collector.write(f"calls/{aid}/terminal.json", {"status": "returned", "duration_ms": 2})
                collector.write(f"calls/{aid}/budget_reservation.json", {"budget_request_id": aid})
                ledger += [
                    {"schema": "orbit.orbitbench.provider-budget.v1", "kind": "reserved", "request_id": aid},
                    {
                        "schema": "orbit.orbitbench.provider-budget.v1",
                        "kind": "settled",
                        "request_id": aid,
                        "charged_cny": 0.01,
                    },
                ]
            collector.write(
                f"trials/{key}.json",
                {
                    "arm": arm,
                    "message_id": key,
                    "verifier_failures": ["marker_missing"] if failed else [],
                    "outcome": {"explicit_reply": True},
                },
            )
            collector.finish()
    (root / "budget.jsonl").write_text("\n".join(json.dumps(e) for e in ledger) + "\n", encoding="utf-8")
    plan = {
        "schema": "orbit.offline-review-plan.v1",
        "data_kind": "simulated",
        "audit_rate": 0.2,
        "audit_seed": 42,
        "budget_ledger": "budget.jsonl",
        "trials": trials,
    }
    (root / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return root / "plan.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", type=Path)
    source.add_argument("--demo", type=Path, help="Create explicitly simulated local evidence in a new directory")
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = create_demo(args.demo) if args.demo else args.plan
        report = write_report(plan, args.output_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Offline report rejected: {type(exc).__name__}: {exc}\n")
    print(
        json.dumps(
            {"data_kind": report["data_kind"], "report_digest": report["report_digest"], "arms": report["arms"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
