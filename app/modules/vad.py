"""M3 vad: Silero VAD over the 16 kHz mono WAV from M1."""

from __future__ import annotations

import logging
from pathlib import Path

from app import store
from app.config import Config, section_hash
from app.schemas import SpeechSegment, VideoMeta

log = logging.getLogger(__name__)
STAGE = "vad"

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        from silero_vad import load_silero_vad

        _MODEL = load_silero_vad()
    return _MODEL


def detect_speech(audio_path: Path, cfg: Config) -> list[SpeechSegment]:
    import soundfile as sf
    import torch
    from silero_vad import get_speech_timestamps

    audio, sr = sf.read(str(audio_path), dtype="float32")
    if sr != cfg.ingest.audio_sr:
        raise ValueError(f"expected {cfg.ingest.audio_sr} Hz audio from M1, got {sr}")
    if audio.ndim > 1:
        raise ValueError("expected mono audio from M1")
    ts = get_speech_timestamps(
        torch.from_numpy(audio), _model(), sampling_rate=sr,
        threshold=cfg.vad.threshold,
        min_silence_duration_ms=cfg.vad.min_silence_duration_ms,
        min_speech_duration_ms=cfg.vad.min_speech_duration_ms,
        return_seconds=True,
    )
    return [SpeechSegment(start_s=float(t["start"]), end_s=float(t["end"]), source="silero") for t in ts]


def silences(segments: list[SpeechSegment], duration_s: float) -> list[tuple[float, float]]:
    """Gaps between speech segments (including head and tail), as (start, end)."""
    gaps, cur = [], 0.0
    for s in sorted(segments, key=lambda s: s.start_s):
        if s.start_s > cur:
            gaps.append((cur, s.start_s))
        cur = max(cur, s.end_s)
    if duration_s > cur:
        gaps.append((cur, duration_s))
    return gaps


def run(meta: VideoMeta, cfg: Config, force: bool = False,
        timings: dict[str, float] | None = None) -> list[SpeechSegment]:
    key = section_hash(STAGE, cfg.vad, store.stage_digest(cfg.runs_dir, meta.sha256, "ingest"))

    def compute() -> list[SpeechSegment]:
        segs = detect_speech(Path(meta.audio_path), cfg)
        log.info("vad: %d speech segments", len(segs))
        return segs

    return store.cached_stage(cfg.runs_dir, meta.sha256, STAGE, key, list[SpeechSegment], compute,
                              force, timings)
