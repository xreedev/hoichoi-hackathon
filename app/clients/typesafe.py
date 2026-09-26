"""TypeSafe (Jev / System One) wrapper. All TypeSafe calls go through here."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from app.clients._common import retry_async, save_raw
from app.config import Config

log = logging.getLogger(__name__)


def has_key() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


class JevClient:
    def __init__(self, cfg: Config, raw_dir: Path | None = None):
        self.cfg = cfg
        self.raw_dir = raw_dir
        self.sem = asyncio.Semaphore(cfg.concurrency.jev_max_parallel)

    async def system_one(self, name: str, state: Any, questions: dict[str, Any]):
        """One System One request. Returns the SDK's SystemOneResponse; logs response.model."""
        from typesafe_sdk import AsyncTypeSafeClient

        c = self.cfg.concurrency

        async def call():
            async with AsyncTypeSafeClient(timeout=self.cfg.timeouts.jev_s) as client:
                return await client.system_one(state=state, questions=questions)

        async with self.sem:
            r = await retry_async(call, c.retry_max_attempts, c.retry_base_delay_s, f"jev {name}")
        log.info("jev %s answered by %s", name, r.model)
        save_raw(self.raw_dir, f"jev_{name}", {"model": r.model, "state": state,
                                               "answers": {k: v.model_dump() for k, v in r.answers.items()}})
        return r
