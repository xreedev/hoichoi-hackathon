"""M8 decide — placeholder until the Jev / Gemini fallback implementation lands (build step 5)."""

from __future__ import annotations

from app.config import Config
from app.schemas import Candidate, Scene, VideoMeta


def run(meta: VideoMeta, cands: list[Candidate], scenes: list[Scene], cfg: Config, force: bool = False,
        timings: dict[str, float] | None = None, warnings: list[str] | None = None,
        model_versions: dict[str, str] | None = None) -> list[Candidate]:
    if warnings is not None:
        warnings.append("decide: not implemented yet — candidates scored on M5/M6 features only")
    return cands
