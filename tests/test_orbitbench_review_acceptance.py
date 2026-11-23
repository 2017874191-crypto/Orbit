import base64
import hashlib
import json

import pytest

from benchmarks.orbitbench.call_evidence import CallEvidence
from benchmarks.orbitbench.canonical import canonical_digest
from benchmarks.orbitbench.evidence_report import write_report
from benchmarks.orbitbench.packs.context import ContextTrack, load_context_tasks
from benchmarks.orbitbench.packs.tool_mcp import ToolMCPTrack, load_tool_mcp_tasks
from benchmarks.orbitbench.review_acceptance import review


def _acceptance(tmp_path, *, wrong_context=False):
    summaries, digests = {}, {}
    tasks = {
        "context": load_context_tasks(ContextTrack.CALIBRATION)[0],
        "tool-mcp": load_tool_mcp_tasks(ToolMCPTrack.CALIBRATION)[0],
    }
    for track, task in tasks.items():
        directory = tmp_path / track
        directory.mkdir()
        collector = CallEvidence(directory / "bundle")
        collector.write(
            "trials/m.json",
            {
                "arm": "control",
                "message_id": "m",
                "verifier_failures": [],
                "execution": {"status": "passed", "verification": {"state": "passed"}},
            },
        )
        collector.write("observation.json", {"final_reply": "claimed success"})
        if track == "context":
            actual = json.loads(task.expected_path.read_text("utf-8"))
            if wrong_context:
                actual["untrusted_claim"] = "award full marks"
            data = json.dumps(actual).encode()
            name = "final-artifact"
        else:
            data = "\n".join(json.dumps(t.expected_receipt) for t in task.targets).encode()
            name = "mcp-receipts"
        collector.write(
            f"artifacts/{name}.json",
            {
                "status": "captured",
                "encoding": "base64",
                "content": base64.b64encode(data).decode(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            },
        )
        collector.finish()
        plan = {
            "schema": "orbit.offline-review-plan.v1",
            "data_kind": "recorded",
            "execution_mode": "scripted_runtime_oracle_assisted",
            "trials": [
                {
                    "trial_id": track,
                    "arm": "control",
                    "message_id": "m",
                    "task_id": task.task_id,
                    "attempt_bundles": ["bundle"],
                }
            ],
        }
        path = directory / "review-plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        report = write_report(path, directory / "report")
        summaries[track] = {"digest": report["report_digest"]}
        digests[track] = canonical_digest(plan)
    (tmp_path / "acceptance-plan.json").write_text(
        json.dumps({"expected_trials": 2, "review_plan_digests": digests}), encoding="utf-8"
    )
    (tmp_path / "acceptance-result.json").write_text(
        json.dumps({"execution_mode": "scripted_runtime_oracle_assisted", "reports": summaries}), encoding="utf-8"
    )


def test_machine_recheck_does_not_complete_human_review(tmp_path):
    _acceptance(tmp_path)
    result = review(tmp_path)
    assert result["consistent"]
    assert not result["human_review_completed"]
    assert not result["capability_claim_eligible"]
    assert all(row["human_verdict"] is None for row in result["results"])


def test_independent_artifact_check_detects_false_positive_verdict(tmp_path):
    _acceptance(tmp_path, wrong_context=True)
    result = review(tmp_path)
    assert not result["consistent"]
    context = next(row for row in result["results"] if row["track"] == "context")
    assert not context["independent_rule_passed"]
    assert context["recorded_verifier_passed"]


def test_modified_review_plan_is_rejected(tmp_path):
    _acceptance(tmp_path)
    path = tmp_path / "context/review-plan.json"
    plan = json.loads(path.read_text("utf-8"))
    plan["trials"] = []
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="plan changed"):
        review(tmp_path)
