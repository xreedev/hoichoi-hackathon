"""M0 health: prove every tool and key works before the pipeline needs it."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app import media
from app.clients import gemini, sarvam, typesafe
from app.config import Config

log = logging.getLogger(__name__)


@dataclass
class Check:
    name: str
    status: str  # OK | FAIL | SKIPPED
    detail: str


def _check(name: str, fn) -> Check:
    try:
        return Check(name, "OK", fn())
    except Exception as e:  # noqa: BLE001 - health reports every failure, never crashes
        return Check(name, "FAIL", f"{type(e).__name__}: {e}"[:300])


def _gemini(cfg: Config) -> str:
    out = gemini.GeminiClient(cfg).ping()
    if not out.strip():
        raise RuntimeError("empty response")
    return f"{cfg.models.text} → {out.strip()[:40]!r}"


def _sarvam(cfg: Config) -> str:
    with tempfile.TemporaryDirectory() as d:
        wav = media.silent_wav(Path(d) / "silence.wav", 3, cfg.ingest.audio_sr)
        out = sarvam.SarvamClient(cfg).batch_transcribe(wav, Path(d) / "out")
    return f"{cfg.models.sarvam_asr} batch job {out['job_id']} ok"


def _typesafe(cfg: Config) -> str:
    from typesafe_sdk import Noul, TypeSafeClient

    with TypeSafeClient(timeout=cfg.timeouts.jev_s) as client:
        models = client.models.list()
        r = client.system_one(
            state={"sky": "blue"},
            questions={"is_blue": Noul(instructions="Is the `sky` blue?")},
        )
    ids = [m.id for m in getattr(models, "data", [])][:5]
    return f"models={ids} noul={r.answers['is_blue'].noul:.2f} model={r.model}"


def run_health(cfg: Config) -> list[Check]:
    checks = [
        _check("ffmpeg", lambda: media.tool_version("ffmpeg")),
        _check("ffprobe", lambda: media.tool_version("ffprobe")),
    ]
    keyed = [
        ("gemini", gemini.has_key, _gemini),
        ("sarvam", sarvam.has_key, _sarvam),
        ("typesafe", typesafe.has_key, _typesafe),
    ]
    for name, has_key, fn in keyed:
        if not has_key():
            checks.append(Check(name, "SKIPPED", "no API key in environment"))
        else:
            checks.append(_check(name, lambda fn=fn: fn(cfg)))
    return checks


def format_table(checks: list[Check]) -> str:
    w = max(len(c.name) for c in checks)
    return "\n".join(f"{c.name:<{w}}  {c.status:<7}  {c.detail}" for c in checks)


def main(cfg: Config) -> int:
    checks = run_health(cfg)
    print(format_table(checks))
    return 1 if any(c.status == "FAIL" for c in checks) else 0

