"""Shared plumbing for every external client: retry/backoff, raw-response capture, MOCK replay."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from app.config import ROOT

log = logging.getLogger(__name__)
T = TypeVar("T")

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "mock"

# Errors worth retrying: rate limits, overload, transient network. Matched on text so we don't
# depend on each SDK's exception hierarchy.
_RETRYABLE_MARKERS = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500", "INTERNAL",
                      "timed out", "Timeout", "Connection", "ConnectError", "RemoteProtocolError")


def is_mock() -> bool:
    return os.environ.get("MOCK") == "1"


def is_retryable(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return any(m in text for m in _RETRYABLE_MARKERS)


def _delay(attempt: int, base: float) -> float:
    return base * (2 ** attempt) + random.uniform(0, base)


def retry_sync(fn: Callable[[], T], attempts: int, base_delay: float, what: str) -> T:
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - re-raised when not retryable / out of attempts
            if i == attempts - 1 or not is_retryable(e):
                raise
            d = _delay(i, base_delay)
            log.warning("%s failed (%s); retry %d/%d in %.1fs", what, e, i + 1, attempts - 1, d)
            time.sleep(d)
    raise RuntimeError("unreachable")


async def retry_async(fn: Callable[[], Awaitable[T]], attempts: int, base_delay: float, what: str) -> T:
    for i in range(attempts):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1 or not is_retryable(e):
                raise
            d = _delay(i, base_delay)
            log.warning("%s failed (%s); retry %d/%d in %.1fs", what, e, i + 1, attempts - 1, d)
            await asyncio.sleep(d)
    raise RuntimeError("unreachable")


def save_raw(raw_dir: Path | None, name: str, payload: Any) -> None:
    """Persist a raw provider response under runs/<sha>/raw/ for audit and fixture recording."""
    if raw_dir is None:
        return
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = raw_dir / f"{name}.{stamp}.{random.randint(0, 9999):04d}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def load_mock(name: str) -> Any:
    """MOCK=1 replay: tests/fixtures/mock/<name>.json."""
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"MOCK=1 but no fixture at {path}")
    return json.loads(path.read_text())
