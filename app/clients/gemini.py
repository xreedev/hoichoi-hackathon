"""Gemini wrapper (google-genai Interactions API). All Gemini calls go through here."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from google import genai

from app.clients._common import is_mock, load_mock, retry_sync, save_raw
from app.config import Config

log = logging.getLogger(__name__)

# One semaphore per process, shared by every GeminiClient instance (free-tier concurrency cap).
_SEM: threading.BoundedSemaphore | None = None
_SEM_LOCK = threading.Lock()


def _semaphore(n: int) -> threading.BoundedSemaphore:
    global _SEM
    with _SEM_LOCK:
        if _SEM is None:
            _SEM = threading.BoundedSemaphore(n)
        return _SEM


def has_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


class GeminiClient:
    def __init__(self, cfg: Config, raw_dir: Path | None = None):
        self.cfg = cfg
        self.raw_dir = raw_dir
        self.sem = _semaphore(cfg.concurrency.gemini_max_parallel)
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        return self._client

    def _call(self, what: str, fn):
        c = self.cfg.concurrency
        with self.sem:
            return retry_sync(fn, c.retry_max_attempts, c.retry_base_delay_s, what)

    def create(self, name: str, timeout_s: float, **body: Any):
        """Non-streaming interactions.create with retry, semaphore and raw capture."""
        it = self._call(name, lambda: self.client.interactions.create(timeout=timeout_s, **body))
        save_raw(self.raw_dir, name, it.model_dump(mode="json"))
        return it

    def ping(self) -> str:
        if is_mock():
            return load_mock("gemini_ping")["output_text"]
        it = self.create(
            "gemini_ping",
            self.cfg.timeouts.gemini_text_s,
            model=self.cfg.models.text,
            input="ping",
            generation_config={"thinking_level": "low"},
        )
        return it.output_text or ""

    def upload(self, path: Path):
        """Upload via the Files API and poll until ACTIVE."""
        f = self._call(f"upload {path.name}", lambda: self.client.files.upload(file=str(path)))
        deadline = time.monotonic() + self.cfg.timeouts.gemini_file_active_s
        while f.state is not None and f.state.name == "PROCESSING":
            if time.monotonic() > deadline:
                raise TimeoutError(f"Gemini file {f.name} not ACTIVE after upload")
            time.sleep(2)
            f = self.client.files.get(name=f.name)
        if f.state is not None and f.state.name != "ACTIVE":
            raise RuntimeError(f"Gemini file {f.name} state={f.state.name}")
        log.info("gemini upload %s → %s (%s)", path.name, f.uri, f.mime_type)
        return f
