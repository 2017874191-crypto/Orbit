"""Local non-streaming call evidence. This is not a malicious-code security boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from orbit.providers.base import LLMProvider, LLMResponse
from orbit.providers.observation import observe_provider

from .artifacts import ArtifactStore
from .canonical import canonical_digest, to_primitive
from .schema import ExperimentRef

_trial: ContextVar[dict[str, str] | None] = ContextVar("evidence_trial", default=None)
_logical_call: ContextVar[str | None] = ContextVar("evidence_logical_call", default=None)


class EvidenceIntegrityError(RuntimeError):
    """Abort evaluation rather than retry paid work after losing its evidence."""


@contextmanager
def evidence_trial(**identity: str) -> Iterator[None]:
    token = _trial.set(identity)
    try:
        yield
    finally:
        _trial.reset(token)


@contextmanager
def evidence_logical_call() -> Iterator[None]:
    token = _logical_call.set(uuid.uuid4().hex)
    try:
        yield
    finally:
        _logical_call.reset(token)


def _snapshot(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        value = {key: _snapshot(item) for key, item in value.items()}
    elif isinstance(value, (list, tuple)):
        value = [_snapshot(item) for item in value]
    return json.loads(json.dumps(to_primitive(value), ensure_ascii=False, allow_nan=False))


class CallEvidence:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.store = ArtifactStore(ExperimentRef(experiment_id="call-evidence", root=self.root))
        self.errors: list[str] = []
        self.attempts: list[str] = []

    def write(self, relative: str, value: Any) -> None:
        try:
            self.store.append_immutable(self.root / relative, _snapshot(value))
        except Exception as exc:
            self.errors.append(type(exc).__name__)

    def require_complete(self) -> None:
        if self.errors:
            raise EvidenceIntegrityError("call evidence persistence failed; no further calls allowed")

    def finish(self) -> dict[str, Any]:
        files = {}
        for path in sorted(self.root.rglob("*.json")):
            if path.name != "integrity.json":
                files[path.relative_to(self.root).as_posix()] = canonical_digest(json.loads(path.read_text("utf-8")))
        complete = not self.errors and all(
            (self.root / "calls" / attempt / "terminal.json").is_file() for attempt in self.attempts
        )
        payload = {
            "schema": "orbit.call-evidence.v1",
            "files": files,
            "attempt_count": len(self.attempts),
            "complete": complete,
            "protection": "in_process_local_recording_not_os_isolation",
        }
        result = {**payload, "digest": canonical_digest(payload)}
        self.write("integrity.json", result)
        self.require_complete()
        return result


class EvidenceProvider(LLMProvider):
    """Observe every chat attempt, including retries owned by the original wrapper."""

    def __init__(self, delegate: LLMProvider, evidence: CallEvidence) -> None:
        super().__init__()
        self.delegate = delegate
        self.evidence = evidence
        self.generation = delegate.generation
        self._CHAT_RETRY_DELAYS = delegate._CHAT_RETRY_DELAYS

    def get_default_model(self) -> str:
        return self.delegate.get_default_model()

    def supports_explicit_cache_control(self, model: str) -> bool:
        return self.delegate.supports_explicit_cache_control(model)

    def classify_error(self, exc=None, content=None):
        return self.delegate.classify_error(exc, content)

    async def chat_with_retry(self, **kwargs) -> LLMResponse:
        with evidence_logical_call():
            return await super().chat_with_retry(**kwargs)

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
        reasoning_effort=None,
        tool_choice=None,
    ) -> LLMResponse:
        self.evidence.require_complete()
        identity = _trial.get()
        if identity is None:
            raise RuntimeError("call evidence requires trial identity before dispatch")
        attempt = uuid.uuid4().hex
        self.evidence.attempts.append(attempt)
        prefix = f"calls/{attempt}"
        request = dict(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        )
        self.evidence.write(
            f"{prefix}/request.json",
            {
                "attempt_id": attempt,
                "logical_call_id": _logical_call.get() or uuid.uuid4().hex,
                "run_id": self.evidence.root.parent.name,
                "identity": identity,
                "requested_parameters": request,
            },
        )
        self.evidence.require_complete()

        def observe(kind: str, payload: Any) -> None:
            self.evidence.write(f"{prefix}/{kind}.json", payload)

        started = time.perf_counter_ns()
        try:
            with observe_provider(observe):
                response = await self.delegate.chat(**request)
        except BaseException as exc:
            self.evidence.write(
                f"{prefix}/terminal.json",
                {
                    "status": "cancelled" if isinstance(exc, asyncio.CancelledError) else "exception",
                    "exception_type": type(exc).__name__,
                    "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
                },
            )
            raise
        duration = (time.perf_counter_ns() - started) / 1_000_000
        self.evidence.write(
            f"{prefix}/response.json",
            {
                "content": response.content,
                "tool_calls": to_primitive(response.tool_calls),
                "reported_model": response.model,
                "finish_reason": response.finish_reason,
                "usage": response.usage,
                "error_classification": to_primitive(response.error_classification),
            },
        )
        self.evidence.write(
            f"{prefix}/terminal.json",
            {
                "status": "provider_error" if response.finish_reason == "error" else "returned",
                "duration_ms": duration,
            },
        )
        self.evidence.require_complete()
        return response

    async def chat_stream(self, **kwargs):
        raise RuntimeError("call evidence v1 supports non-streaming calls only")
        yield  # Keep the async iterator contract without silently changing streaming behavior.


def verify_bundle(root: Path) -> dict[str, Any]:
    record = json.loads((root / "integrity.json").read_text("utf-8"))
    payload = {key: value for key, value in record.items() if key != "digest"}
    valid = record["digest"] == canonical_digest(payload) and record["complete"]
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*.json") if p.name != "integrity.json"}
    valid = valid and actual == set(record["files"])
    for relative, digest in record["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            valid = False
            continue
        valid = valid and path.is_file() and canonical_digest(json.loads(path.read_text("utf-8"))) == digest
    requests = list((root / "calls").glob("*/request.json"))
    valid = (
        valid
        and len(requests) == record["attempt_count"]
        and all(p.with_name("terminal.json").is_file() for p in requests)
    )
    return {"passed": bool(valid), "attempt_count": record["attempt_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local call evidence and export failed trial calls offline.")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    try:
        result = verify_bundle(args.root)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"passed": False, "error": "unreadable_or_invalid_bundle"}))
        return 1
    print(json.dumps(result))
    if not result["passed"]:
        return 1
    failed = {}
    for trial in (args.root / "trials").glob("*.json"):
        record = json.loads(trial.read_text("utf-8"))
        if record["verifier_failures"]:
            failed[(record["arm"], record["message_id"])] = (record, trial)
    for request_path in (args.root / "calls").glob("*/request.json"):
        request = json.loads(request_path.read_text("utf-8"))
        identity = request["identity"]
        if (identity["arm"], identity["message_id"]) not in failed:
            continue
        response_path = request_path.with_name("response.json")
        response = json.loads(response_path.read_text("utf-8")) if response_path.exists() else {}
        trial_record, trial_path = failed[(identity["arm"], identity["message_id"])]
        adapter_path = request_path.with_name("adapter_request.json")
        adapter = json.loads(adapter_path.read_text("utf-8")) if adapter_path.exists() else {}
        print(
            json.dumps(
                {
                    "identity": identity,
                    "finish_reason": response.get("finish_reason"),
                    "usage": response.get("usage"),
                    "requested_max_tokens": request["requested_parameters"]["max_tokens"],
                    "adapter_max_tokens": adapter.get("max_tokens"),
                    "verifier_failures": trial_record["verifier_failures"],
                    "trial_record_path": str(trial_path),
                    "response_path": str(response_path),
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
