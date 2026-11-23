import json

import pytest

from benchmarks.orbitbench.call_evidence import CallEvidence
from benchmarks.orbitbench.evidence_report import build_report, create_demo, read, render_markdown, write_report


def test_missing_evidence_stays_in_denominator_and_cost_is_unknown(tmp_path):
    plan = create_demo(tmp_path / "demo")
    report = build_report(plan)
    assert report["data_kind"] == "simulated"
    assert report["arms"]["control"]["strict_successes"] == 2
    treatment = report["arms"]["treatment"]
    assert treatment["planned"] == 3
    assert treatment["strict_successes"] == 1
    assert treatment["success_rate"] == 1 / 3
    assert treatment["accounted_cny"] is None
    assert treatment["cost_per_strict_success_cny"] is None
    assert report["arms"]["control"]["accounted_cny"] == pytest.approx(0.04)
    assert report["arms"]["control"]["cost_per_strict_success_cny"] == pytest.approx(0.02)
    assert report["arms"]["control"]["additional_attempts_within_logical_calls"] == 1
    assert report["quality_preservation_proven"] is False
    assert "SIMULATED" in render_markdown(report)


def test_recorded_scripted_runtime_cannot_be_mistaken_for_live_model_data(tmp_path):
    path = create_demo(tmp_path / "demo")
    plan = read(path)
    plan["data_kind"] = "recorded"
    plan["execution_mode"] = "scripted_runtime_oracle_assisted"
    path.write_text(json.dumps(plan), encoding="utf-8")
    report = build_report(path)
    assert report["execution_mode"] == "scripted_runtime_oracle_assisted"
    assert "scripted_runtime_oracle_assisted" in render_markdown(report)
    assert report["human_review_completed"] is False
    assert report["quality_preservation_proven"] is False


def test_audit_queue_is_reproducible_and_keeps_anomalies_separate(tmp_path):
    plan = create_demo(tmp_path / "demo")
    report = build_report(plan)
    assert report == build_report(plan)
    selected = {row["trial_id"]: row for row in report["audit_queue"]}
    assert selected["treatment-2"]["selection"] == "all_anomalies"
    assert selected["control-0"]["selection"] == "seeded_random_normal"
    assert selected["control-0"]["inclusion_probability"] == 0.5


def test_modified_bundle_cannot_supply_a_verified_success(tmp_path):
    plan = create_demo(tmp_path / "demo")
    response = next((plan.parent / "control-0").glob("calls/*/response.json"))
    response.write_text("{}", encoding="utf-8")
    report = build_report(plan)
    assert report["arms"]["control"]["strict_successes"] == 1
    row = next(row for row in report["trials"] if row["trial_id"] == "control-0")
    assert row["status"] == "unverified"
    assert row["accounted_cny"] is None


def test_duplicate_trials_and_duplicate_bundles_are_rejected(tmp_path):
    plan = create_demo(tmp_path / "demo")
    content = read(plan)
    content["trials"].append(content["trials"][0])
    plan.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate planned trial"):
        build_report(plan)
    content["trials"].pop()
    content["trials"][0]["attempt_bundles"] *= 2
    plan.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="bundle cannot count"):
        build_report(plan)


def test_later_success_does_not_replace_first_failure(tmp_path):
    for name, success in (("first", False), ("later", True)):
        collector = CallEvidence(tmp_path / name)
        collector.write(
            "trials/t.json",
            {
                "arm": "control",
                "message_id": "m" if name == "first" else "m-later",
                "verifier_failures": [] if success else ["wrong_artifact"],
                "execution": {
                    "status": "passed" if success else "task_failed",
                    "verification": {"state": "passed" if success else "failed"},
                },
            },
        )
        collector.finish()
    plan = {
        "schema": "orbit.offline-review-plan.v1",
        "data_kind": "simulated",
        "trials": [
            {
                "trial_id": "t",
                "arm": "control",
                "task_id": "task",
                "message_id": "m",
                "attempt_bundles": ["first", {"path": "later", "message_id": "m-later"}],
            }
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    row = build_report(path)["trials"][0]
    assert row["status"] == "task_failed"
    assert row["success"] is False
    assert len(row["evidence_paths"]) == 2
    assert "trial_identity_mismatch" not in row["reasons"]


def test_report_output_cannot_modify_source_or_overwrite_results(tmp_path):
    plan = create_demo(tmp_path / "demo")
    with pytest.raises(ValueError, match="outside evidence"):
        write_report(plan, plan.parent / "control-0" / "report")
    output = tmp_path / "report"
    write_report(plan, output)
    assert (output / "REPORT.md").exists()
    assert (output / "audit-queue.json").exists()
    with pytest.raises(FileExistsError):
        write_report(plan, output)


def test_failed_reservation_cost_is_labelled_as_upper_bound(tmp_path):
    plan = create_demo(tmp_path / "demo")
    ledger_path = plan.parent / "budget.jsonl"
    events = [json.loads(line) for line in ledger_path.read_text("utf-8").splitlines()]
    events[1]["kind"] = "failed"
    ledger_path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    report = build_report(plan)
    row = next(row for row in report["trials"] if row["trial_id"] == "control-0")
    assert row["cost_sources"] == ["failed_reservation_upper_bound"]


def test_ledger_duplicate_charge_is_rejected(tmp_path):
    plan = create_demo(tmp_path / "demo")
    ledger = plan.parent / "budget.jsonl"
    lines = ledger.read_text("utf-8").splitlines()
    ledger.write_text("\n".join([*lines, lines[-1]]), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid budget terminal"):
        build_report(plan)


def test_open_reservation_is_unknown_cost_not_zero(tmp_path):
    plan = create_demo(tmp_path / "demo")
    ledger_path = plan.parent / "budget.jsonl"
    lines = ledger_path.read_text("utf-8").splitlines()
    ledger_path.write_text("\n".join([lines[0], *lines[2:]]), encoding="utf-8")
    report = build_report(plan)
    assert report["arms"]["control"]["accounted_cny"] is None
    assert report["budget_ledger_summary"]["open_reservations"] == 1
    assert report["budget_ledger_summary"]["accounted_cny"] is None


@pytest.mark.parametrize("reason,flag", [("stop", "token_limit_suspected"), ("length", "truncation_reported")])
def test_limit_reached_is_not_automatically_truncation(tmp_path, reason, flag):
    collector = CallEvidence(tmp_path / "bundle")
    collector.attempts.append("a")
    collector.write(
        "calls/a/request.json",
        {
            "attempt_id": "a",
            "logical_call_id": "l",
            "identity": {"arm": "control", "message_id": "m"},
            "requested_parameters": {"max_tokens": 512},
        },
    )
    collector.write(
        "calls/a/response.json", {"finish_reason": reason, "usage": {"prompt_tokens": 100, "completion_tokens": 512}}
    )
    collector.write("calls/a/terminal.json", {"status": "returned", "duration_ms": 1})
    collector.write(
        "trials/t.json",
        {"arm": "control", "message_id": "m", "verifier_failures": [], "outcome": {"explicit_reply": True}},
    )
    collector.finish()
    plan = {
        "schema": "orbit.offline-review-plan.v1",
        "data_kind": "simulated",
        "trials": [
            {"trial_id": "t", "arm": "control", "task_id": "task", "message_id": "m", "attempt_bundles": ["bundle"]}
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    flags = build_report(path)["trials"][0]["attempts"][0]["flags"]
    assert flag in flags
    assert not ({"token_limit_suspected", "truncation_reported"} <= set(flags))
