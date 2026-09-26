"""Pick the decider: Jev (TypeSafe System One) when enabled, else the Gemini fallback. Same interface."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.clients.gemini_decider import DeciderResponse, GeminiDecider
from app.clients.typesafe import JevClient
from app.config import Config

log = logging.getLogger(__name__)


class JevDecider:
    source = "jev"

    def __init__(self, cfg: Config, raw_dir: Path | None = None):
        self.jev = JevClient(cfg, raw_dir=raw_dir)
        self.fallback = GeminiDecider(cfg, raw_dir=raw_dir)

    async def system_one(self, name: str, state: Any, questions: dict[str, Any],
                         media: list[dict] | None = None) -> DeciderResponse:
        try:
            r = await self.jev.system_one(name, state, questions)
            return DeciderResponse(model=r.model, answers=dict(r.answers), source="jev")
        except Exception as e:  # noqa: BLE001 - documented fallback, logged
            log.warning("jev %s failed (%s); using Gemini fallback decider", name, e)
            return await self.fallback.system_one(name, state, questions)


def get_decider(cfg: Config, raw_dir: Path | None = None) -> JevDecider | GeminiDecider:
    return JevDecider(cfg, raw_dir) if cfg.flags.use_jev else GeminiDecider(cfg, raw_dir)


def answer_to_dict(a: Any) -> dict[str, Any]:
    """Serialise an answer for Candidate.decisions; Score probabilities are re-keyed by label."""
    d = a.model_dump()
    if d.get("type") == "score":
        legend = {int(k): str(v) for k, v in d["legend"].items()}
        d["probabilities"] = {legend[int(k)]: float(v) for k, v in d["probabilities"].items()}
        d["legend"] = {str(k): v for k, v in legend.items()}
    return d
