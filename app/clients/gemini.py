"""Gemini wrapper (google-genai Interactions API). All Gemini calls go through here."""

from __future__ import annotations

import json
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


_CLIENT: genai.Client | None = None


def _shared_client() -> genai.Client:
    """One process-wide SDK client: lazily creating one per call races and closes in-flight requests."""
    global _CLIENT
    with _SEM_LOCK:
        if _CLIENT is None:
            _CLIENT = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        return _CLIENT


def has_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


class GeminiClient:
    def __init__(self, cfg: Config, raw_dir: Path | None = None):
        self.cfg = cfg
        self.raw_dir = raw_dir
        self.sem = _semaphore(cfg.concurrency.gemini_max_parallel)

    @property
    def client(self) -> genai.Client:
        return _shared_client()

    def _call(self, what: str, fn):
        c = self.cfg.concurrency
        with self.sem:
            return retry_sync(fn, c.retry_max_attempts, c.retry_base_delay_s, what)

    def create(self, name: str, timeout_s: float, **body: Any):
        """Non-streaming interactions.create with retry, semaphore and raw capture."""
        it = self._call(name, lambda: self.client.interactions.create(timeout=timeout_s, **body))
        save_raw(self.raw_dir, name, it.model_dump(mode="json"))
        return it

    def stream_text(self, name: str, timeout_s: float, **body: Any) -> dict[str, Any]:
        """Streaming interactions.create. Concatenates model-output text deltas.

        Returns {"text", "step_types", "model", "usage"}; raises on an error event.
        """
        def consume() -> dict[str, Any]:
            stream = self.client.interactions.create(timeout=timeout_s, stream=True, **body)
            text: list[str] = []
            step_types: list[str] = []
            model, usage = body.get("model"), None
            for ev in stream:
                et = getattr(ev, "event_type", None)
                if et == "step.start":
                    step_types.append(getattr(ev.step, "type", "unknown"))
                elif et == "step.delta" and getattr(ev.delta, "type", None) == "text":
                    text.append(ev.delta.text)
                elif et == "error":
                    raise RuntimeError(f"Gemini stream error: {ev.error}")
                elif et == "interaction.completed":
                    inter = ev.interaction
                    model = getattr(inter, "model", None) or model
                    u = getattr(inter, "usage", None)
                    usage = u.model_dump(mode="json") if u is not None else None
            return {"text": "".join(text), "step_types": step_types, "model": model, "usage": usage}

        out = self._call(name, consume)
        save_raw(self.raw_dir, name, out)
        return out

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

    def upload_cached(self, path: Path, record: Path) -> dict[str, str]:
        """Upload once and remember {name, uri, mime_type}; reuse while the file is still ACTIVE."""
        if is_mock():
            return {"name": "files/mock", "uri": "mock://proxy", "mime_type": "video/mp4"}
        if record.exists():
            rec = json.loads(record.read_text())
            try:
                f = self.client.files.get(name=rec["name"])
                if f.state is not None and f.state.name == "ACTIVE":
                    return rec
            except Exception as e:  # noqa: BLE001 - expired / deleted → re-upload
                log.info("cached Gemini file %s unusable (%s); re-uploading", rec.get("name"), e)
        f = self.upload(path)
        rec = {"name": f.name, "uri": f.uri, "mime_type": f.mime_type}
        record.write_text(json.dumps(rec))
        return rec

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
