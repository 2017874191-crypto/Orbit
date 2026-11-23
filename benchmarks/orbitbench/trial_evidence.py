"""Evidence lifecycle for non-streaming Context and MCP runtime Trials."""

from __future__ import annotations

import base64
import hashlib
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from pathlib import Path

from orbit.spine import Text

from .call_evidence import CallEvidence, EvidenceIntegrityError, EvidenceProvider, evidence_trial
from .canonical import canonical_digest, to_primitive
from .records import TrialStatus
from .verifier import require_normalized_relative_path

_collector: ContextVar[CallEvidence | None] = ContextVar("runtime_trial_evidence", default=None)


def observed_provider(provider):
    collector = _collector.get()
    return EvidenceProvider(provider, collector) if collector is not None else provider


def capture_observation(observation) -> None:
    collector = _collector.get()
    if collector is not None:
        collector.write(
            "observation.json",
            {
                "final_reply": "\n".join(event.content for event in observation.events if isinstance(event, Text)),
                "runtime_state": observation.runtime_state,
                "delivery_state": observation.delivery_state,
                "failure_category": observation.failure_category,
                "events": to_primitive(observation.events),
            },
        )


def capture_workspace_file(workspace: Path, relative: str, *, name: str) -> None:
    collector = _collector.get()
    if collector is None:
        return
    require_normalized_relative_path(relative, field_name="artifact")
    path = (workspace / relative).resolve()
    try:
        if not path.is_relative_to(workspace.resolve()):
            collector.write(f"artifacts/{name}.json", {"source": relative, "status": "outside_workspace"})
        elif not path.is_file():
            collector.write(f"artifacts/{name}.json", {"source": relative, "status": "missing"})
        else:
            if path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("artifact exceeds evidence size ceiling")
            data = path.read_bytes()
            collector.write(
                f"artifacts/{name}.json",
                {
                    "source": relative,
                    "status": "captured",
                    "encoding": "base64",
                    "content": base64.b64encode(data).decode("ascii"),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                },
            )
    except (OSError, ValueError) as exc:
        collector.errors.append(type(exc).__name__)


def recorded_trial(function):
    """Preserve the runner contract and attach a per-attempt evidence bundle."""

    @wraps(function)
    async def run(*args, context, **kwargs):
        identity = {
            "experiment_id": context.experiment_id,
            "plan_digest": context.plan_digest,
            "key": to_primitive(context.key),
            "block_attempt": context.block_attempt,
        }
        trial_id = canonical_digest(identity)
        root = context.experiment.output_root / ".orbitbench-call-evidence" / trial_id
        collector = CallEvidence(root)
        collector.write("manifest.json", {"schema": "orbit.trial-call-evidence.v1", **identity})
        collector.require_complete()
        token = _collector.set(collector)
        result = None
        integrity = None
        try:
            with evidence_trial(
                arm=context.variant.variant_id,
                message_id=trial_id,
                task_id=context.key.task_id,
                pack_id=context.key.pack_id,
            ):
                try:
                    result = await function(*args, context=context, **kwargs)
                    collector.write(
                        f"trials/{trial_id}.json",
                        {
                            "arm": context.variant.variant_id,
                            "message_id": trial_id,
                            "verifier_failures": list(result.verification.findings)
                            or ([] if result.status is TrialStatus.PASSED else [result.status.value]),
                            "execution": to_primitive(result),
                        },
                    )
                except BaseException as exc:
                    collector.write(
                        f"trials/{trial_id}.json",
                        {
                            "arm": context.variant.variant_id,
                            "message_id": trial_id,
                            "verifier_failures": [type(exc).__name__],
                            "exception_type": type(exc).__name__,
                        },
                    )
                    raise
                finally:
                    try:
                        integrity = collector.finish()
                    except Exception as exc:
                        raise EvidenceIntegrityError("cannot finalize trial evidence") from exc
        finally:
            _collector.reset(token)
        if not integrity["complete"]:
            raise EvidenceIntegrityError("trial call evidence is incomplete")
        return replace(
            result,
            metrics={**result.metrics, "call_evidence_digest": integrity["digest"]},
            artifact_refs=(*result.artifact_refs, root.relative_to(context.experiment.output_root).as_posix()),
        )

    return run
