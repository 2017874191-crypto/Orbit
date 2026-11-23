import asyncio
import json

import pytest

from benchmarks.orbitbench.call_evidence import CallEvidence, EvidenceProvider, evidence_trial, main, verify_bundle
from orbit.providers.base import ErrorClassification, LLMProvider, LLMResponse


class FakeProvider(LLMProvider):
    _CHAT_RETRY_DELAYS = (0,)

    def __init__(self):
        super().__init__()
        self.calls = 0

    def get_default_model(self):
        return "fake"

    async def chat(self, messages, **kwargs):
        self.calls += 1
        await asyncio.sleep(0)
        if self.calls == 1:
            return LLMResponse(
                content="retry",
                finish_reason="error",
                error_classification=ErrorClassification("rate_limit", retryable=True),
            )
        return LLMResponse(
            content=messages[0]["content"],
            finish_reason="length",
            model="fake-reported",
            usage={"completion_tokens": 512},
        )


async def test_records_each_retry_and_detects_response_tampering(tmp_path):
    collector = CallEvidence(tmp_path / "call-evidence")
    delegate = FakeProvider()
    provider = EvidenceProvider(delegate, collector)
    with evidence_trial(arm="control", message_id="turn-1"):
        result = await provider.chat_with_retry(
            messages=[{"role": "user", "content": "marker missing"}], max_tokens=512
        )
    assert result.finish_reason == "length"
    assert delegate.calls == 2
    collector.finish()
    assert verify_bundle(collector.root)["passed"]
    requests = [json.loads(p.read_text("utf-8")) for p in collector.root.glob("calls/*/request.json")]
    assert len({r["logical_call_id"] for r in requests}) == 1
    assert all(r["identity"] == {"arm": "control", "message_id": "turn-1"} for r in requests)
    path = next(collector.root.glob("calls/*/response.json"))
    path.write_text("{}", encoding="utf-8")
    assert not verify_bundle(collector.root)["passed"]


async def test_concurrent_calls_do_not_mix_trial_identity(tmp_path):
    collector = CallEvidence(tmp_path)
    delegate = FakeProvider()
    delegate.calls = 2
    provider = EvidenceProvider(delegate, collector)

    async def run(name):
        with evidence_trial(arm=name, message_id=name):
            await provider.chat(messages=[{"role": "user", "content": name}])

    await asyncio.gather(run("control"), run("treatment"))
    for path in tmp_path.glob("calls/*/request.json"):
        request = json.loads(path.read_text("utf-8"))
        response = json.loads(path.with_name("response.json").read_text("utf-8"))
        assert request["identity"]["arm"] == response["content"]


async def test_cancelled_attempt_has_terminal_record(tmp_path):
    class CancelProvider(FakeProvider):
        async def chat(self, **kwargs):
            raise asyncio.CancelledError()

    collector = CallEvidence(tmp_path)
    with evidence_trial(arm="control", message_id="cancel"):
        with pytest.raises(asyncio.CancelledError):
            await EvidenceProvider(CancelProvider(), collector).chat(messages=[])
    terminal = json.loads(next(tmp_path.glob("calls/*/terminal.json")).read_text("utf-8"))
    assert terminal["status"] == "cancelled"


async def test_write_failure_prevents_provider_dispatch(tmp_path, monkeypatch):
    collector = CallEvidence(tmp_path)
    delegate = FakeProvider()

    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(type(collector.store), "append_immutable", broken)
    with evidence_trial(arm="control", message_id="disk"):
        with pytest.raises(RuntimeError, match="persistence failed"):
            await EvidenceProvider(delegate, collector).chat(messages=[])
    assert delegate.calls == 0


def test_missing_terminal_is_not_complete(tmp_path):
    collector = CallEvidence(tmp_path)
    collector.attempts.append("interrupted")
    collector.write("calls/interrupted/request.json", {"identity": {}})
    assert collector.finish()["complete"] is False
    assert not verify_bundle(tmp_path)["passed"]


async def test_sdk_capture_excludes_credentials_and_retains_response_id(tmp_path, monkeypatch):
    from orbit.providers import litellm_provider as module

    async def fake_completion(**kwargs):
        assert kwargs["api_key"] == "private-key-do-not-store"
        return module.litellm.ModelResponse(
            id="provider-response-123",
            model="deepseek/deepseek-v4-flash",
            choices=[{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    monkeypatch.setattr(module, "acompletion", fake_completion)
    delegate = module.LiteLLMProvider(
        api_key="private-key-do-not-store",
        default_model="deepseek/deepseek-v4-flash",
        extra_headers={"Authorization": "private-header-do-not-store"},
    )
    collector = CallEvidence(tmp_path)
    with evidence_trial(arm="control", message_id="sdk"):
        await EvidenceProvider(delegate, collector).chat(messages=[{"role": "user", "content": "hello"}])
    collector.finish()
    assert verify_bundle(tmp_path)["passed"]
    sdk = json.loads(next(tmp_path.glob("calls/*/sdk_response.json")).read_text("utf-8"))
    assert sdk["id"] == "provider-response-123"
    assert sdk["choices"][0]["message"]["content"] == "ok"
    for path in tmp_path.rglob("*.json"):
        assert "private-key-do-not-store" not in path.read_text("utf-8")
        assert "private-header-do-not-store" not in path.read_text("utf-8")


async def test_offline_export_links_failed_trial_to_response(tmp_path, monkeypatch, capsys):
    delegate = FakeProvider()
    delegate.calls = 2
    collector = CallEvidence(tmp_path)
    with evidence_trial(arm="treatment", message_id="missing-marker"):
        await EvidenceProvider(delegate, collector).chat(
            messages=[{"role": "user", "content": "wrong"}], max_tokens=512
        )
    collector.write(
        "trials/failure.json",
        {"arm": "treatment", "message_id": "missing-marker", "verifier_failures": ["marker_missing"]},
    )
    collector.finish()
    monkeypatch.setattr("sys.argv", ["call_evidence", str(tmp_path)])
    assert main() == 0
    rows = [json.loads(row) for row in capsys.readouterr().out.splitlines()]
    assert rows[0]["passed"]
    assert rows[1]["identity"]["arm"] == "treatment"
    assert rows[1]["finish_reason"] == "length"
    assert rows[1]["usage"]["completion_tokens"] == rows[1]["requested_max_tokens"] == 512
    assert delegate.calls == 3  # Offline verification/export made no additional provider request.


async def test_evidence_failure_aborts_harness_instead_of_becoming_retryable_infrastructure_failure(tmp_path):
    from types import SimpleNamespace

    from benchmarks.orbitbench.call_evidence import EvidenceIntegrityError
    from benchmarks.orbitbench.harness import _execute_trial
    from benchmarks.orbitbench.schema import ExperimentSpec

    class BrokenPack:
        async def run_trial(self, context):
            raise EvidenceIntegrityError("disk full")

    context = SimpleNamespace(
        experiment=ExperimentSpec(
            suite="evidence", repetitions=1, pack_ids=("context",), output_root=tmp_path, identity={}
        ),
        key=SimpleNamespace(pack_id="context"),
    )
    with pytest.raises(EvidenceIntegrityError, match="disk full"):
        await _execute_trial(BrokenPack(), context)
