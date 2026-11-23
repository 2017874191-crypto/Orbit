"""Optional, task-local observation of provider boundaries; no evaluator dependency."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_observer: ContextVar[Callable[[str, Any], None] | None] = ContextVar("provider_observer", default=None)


@contextmanager
def observe_provider(callback: Callable[[str, Any], None]) -> Iterator[None]:
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


def provider_event(kind: str, payload: Any) -> None:
    callback = _observer.get()
    if callback is not None:
        callback(kind, payload)
