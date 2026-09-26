"""Stage cache: runs/<video_sha256>/<stage>.json, keyed by sha256 + hash(stage config + inputs)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter

log = logging.getLogger(__name__)
T = TypeVar("T")


def run_dir(runs_dir: Path, sha: str) -> Path:
    d = runs_dir / sha
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage_path(runs_dir: Path, sha: str, stage: str) -> Path:
    return run_dir(runs_dir, sha) / f"{stage}.json"


def _key_path(runs_dir: Path, sha: str, stage: str) -> Path:
    return run_dir(runs_dir, sha) / f"{stage}.key"


def _dump(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_dump(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    return obj


def save_stage(runs_dir: Path, sha: str, stage: str, obj: Any, key: str) -> Path:
    p = stage_path(runs_dir, sha, stage)
    p.write_text(json.dumps(_dump(obj), ensure_ascii=False, indent=2))
    _key_path(runs_dir, sha, stage).write_text(key)
    return p


def load_stage(runs_dir: Path, sha: str, stage: str, typ: Any) -> Any:
    raw = json.loads(stage_path(runs_dir, sha, stage).read_text())
    return TypeAdapter(typ).validate_python(raw)


def is_cached(runs_dir: Path, sha: str, stage: str, key: str) -> bool:
    kp = _key_path(runs_dir, sha, stage)
    return stage_path(runs_dir, sha, stage).exists() and kp.exists() and kp.read_text() == key


def stage_digest(runs_dir: Path, sha: str, stage: str) -> str:
    """The cache key of an upstream stage; feed it into downstream keys so edits cascade."""
    kp = _key_path(runs_dir, sha, stage)
    return kp.read_text() if kp.exists() else ""


def cached_stage(
    runs_dir: Path,
    sha: str,
    stage: str,
    key: str,
    typ: Any,
    compute: Callable[[], T],
    force: bool = False,
    timings: dict[str, float] | None = None,
) -> T:
    """Return the cached stage output if the key matches, else compute, save and time it."""
    if not force and is_cached(runs_dir, sha, stage, key):
        log.info("stage %s: cache hit", stage)
        return load_stage(runs_dir, sha, stage, typ)
    t0 = time.monotonic()
    out = compute()
    if timings is not None:
        timings[stage] = round(time.monotonic() - t0, 3)
    save_stage(runs_dir, sha, stage, out, key)
    return out
