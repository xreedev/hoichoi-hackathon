"""M4 asr — placeholder until the Sarvam/Gemini Transcribe implementation lands (build step 6)."""

from __future__ import annotations

from app.config import Config
from app.schemas import TranscriptChunk, VideoMeta


def run(meta: VideoMeta, cfg: Config, force: bool = False, timings: dict[str, float] | None = None,
        warnings: list[str] | None = None, model_versions: dict[str, str] | None = None) -> list[TranscriptChunk]:
    if warnings is not None:
        warnings.append("asr: not implemented yet — empty transcript")
    return []
