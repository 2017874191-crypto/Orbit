"""Offline machine assistance for human review of public scripted acceptance fixtures."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from .call_evidence import verify_bundle
from .canonical import canonical_digest, canonical_json
from .evidence_report import read
from .packs.context import ContextTrack, load_context_tasks
from .packs.tool_mcp import ToolMCPTrack, load_tool_mcp_tasks


def review(root: Path):
    root = root.resolve()
    frozen = read(root / "acceptance-plan.json")
    summary = read(root / "acceptance-result.json")
    if summary.get("execution_mode") != "scripted_runtime_oracle_assisted":
        raise ValueError("this reviewer supports public oracle-assisted acceptance fixtures only")
    tasks = {
        "context": {t.task_id: t for t in load_context_tasks(ContextTrack.CALIBRATION)},
        "tool-mcp": {t.task_id: t for t in load_tool_mcp_tasks(ToolMCPTrack.CALIBRATION)},
    }
    results = []
    for track in ("context", "tool-mcp"):
        plan = read(root / track / "review-plan.json")
        if canonical_digest(plan) != frozen["review_plan_digests"][track]:
            raise ValueError("review plan changed after acceptance")
        report = read(root / track / "report/report.json")
        payload = {k: v for k, v in report.items() if k != "report_digest"}
        if (
            canonical_digest(payload) != report["report_digest"]
            or report["report_digest"] != summary["reports"][track]["digest"]
        ):
            raise ValueError("report digest mismatch")
        planned = {r["trial_id"]: r for r in plan["trials"]}
        for row in report["trials"]:
            entry = planned[row["trial_id"]]
            bundle = (root / track / entry["attempt_bundles"][0]).resolve()
            if not verify_bundle(bundle)["passed"]:
                raise ValueError("source evidence integrity failed")
            task = tasks[track][entry["task_id"]]
            observation = read(bundle / "observation.json")
            filename = "final-artifact" if track == "context" else "mcp-receipts"
            artifact = read(bundle / f"artifacts/{filename}.json")
            content = None
            if artifact["status"] == "captured":
                data = base64.b64decode(artifact["content"], validate=True)
                if hashlib.sha256(data).hexdigest() != artifact["sha256"] or len(data) != artifact["size"]:
                    raise ValueError("artifact byte checksum mismatch")
                content = data.decode("utf-8")
            if track == "context":
                expected = read(task.expected_path)
                actual = json.loads(content) if content is not None else None
                independently_passed = actual == expected
                explanation = (
                    "Final JSON equals the current public fixture"
                    if independently_passed
                    else "Final JSON differs despite success/score claims"
                )
            else:
                expected = [target.expected_receipt for target in task.targets]
                actual = (
                    [json.loads(line) for line in content.splitlines() if line.strip()] if content is not None else []
                )
                independently_passed = sorted(map(canonical_json, actual)) == sorted(map(canonical_json, expected))
                explanation = (
                    "Fixture receipts match target calls"
                    if independently_passed
                    else "Success text has no matching fixture receipts"
                )
            actual_record = read(bundle / "trials" / f"{entry['message_id']}.json")
            recorded_passed = actual_record["execution"]["verification"]["state"] == "passed"
            results.append(
                {
                    "trial_id": row["trial_id"],
                    "track": track,
                    "arm": row["arm"],
                    "bundle": str(bundle),
                    "artifact_status": artifact["status"],
                    "current_public_expected_digest": canonical_digest(expected),
                    "independent_rule_passed": independently_passed,
                    "recorded_verifier_passed": recorded_passed,
                    "consistent": independently_passed == recorded_passed == row["success"],
                    "explanation": explanation,
                    "final_reply": observation["final_reply"],
                    "decoded_artifact": actual,
                    "human_verdict": None,
                }
            )
    return {
        "schema": "orbit.machine-assisted-review.v1",
        "review_kind": "automated_rule_recheck_not_human_review",
        "scope": "current_public_fixture_not_historical_or_secret_holdout_verification",
        "human_review_completed": False,
        "capability_claim_eligible": False,
        "consistent": len(results) == frozen["expected_trials"] and all(r["consistent"] for r in results),
        "source_report_digests": {track: summary["reports"][track]["digest"] for track in tasks},
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = review(args.root)
    if any(args.output.resolve().is_relative_to(Path(row["bundle"])) for row in result["results"]):
        raise ValueError("review output must not modify source evidence bundles")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {"consistent": result["consistent"], "reviewed": len(result["results"]), "human_review_completed": False}
        )
    )
    return 0 if result["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
